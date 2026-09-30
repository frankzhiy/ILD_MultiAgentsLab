"""Regression checks for the workbook protocol audit."""


def test_cli_and_workbench_share_fact_distribution():
    from scripts.agent_input.prepare_specialty_input import build_specialty_case_input as cli
    from src.agents.common.specialty_input import build_specialty_case_input, _ALLOWED_USES
    from src.schemas.specialty_agent_input import EvidenceRole
    assert cli is build_specialty_case_input
    assert all("diagnostic_support" in uses for uses in _ALLOWED_USES.values())
    assert "clinical_interpretation" not in _ALLOWED_USES[EvidenceRole.REFERENCE_ONLY]


def judgment():
    return dict(assessment_id='A1', role='primary', assessment_type='working_diagnosis',
                statement='肺炎可能', status='possible', medical_basis='有发热记录',
                decision_impact='解释急性变化', claims=[{'statement': '肺炎可能'}],
                assessability='partially_assessable', direction='supports',
                confidence='moderate', clinical_role='primary')


def test_new_judgments_require_independent_dimensions():
    import pytest
    from pydantic import ValidationError
    from src.agents.common.initial_output import SpecialtyAssessment
    from src.agents.common.decision_state import JudgmentContent
    for cls in (SpecialtyAssessment, JudgmentContent):
        data = judgment()
        if cls is JudgmentContent:
            data.pop('assessment_id')
        assert cls.model_validate(data).confidence == 'moderate'
        for key in ('assessability', 'direction', 'confidence', 'clinical_role'):
            incomplete = dict(data); incomplete.pop(key)
            with pytest.raises(ValidationError, match=key):
                cls.model_validate(incomplete)
        with pytest.raises(ValidationError, match='unassessable'):
            cls.model_validate({**data, 'assessability': 'not_assessable'})


def test_active_initial_prompts_allow_recorded_cross_specialty_facts():
    from pathlib import Path
    for path in Path('src/prompts').glob('*/initial_*.md'):
        content = path.read_text()
        assert '通用 confidence' not in content
        for line in content.splitlines():
            if 'reference_only' in line:
                assert '已记录的病例事实均可引用' in line


def test_discussion_projections_preserve_dimensions():
    from src.agents.common.decision_state import initialize_decision_state
    from src.agents.mdt_discussion.prompt_projection import build_specialty_discussion_prompt_view
    from src.agents.mdt_discussion.routing import _active_judgment_view
    data = judgment()
    initial = dict(specialty_assessments=dict(assessments=[data], boundaries=[]),
                   interspecialty_questions=dict(questions=[]))
    state = initialize_decision_state('synthetic', {'pulmonology': initial})
    for projected in (build_specialty_discussion_prompt_view(initial)['specialty_assessments'][0],
                      _active_judgment_view(state, 'pulmonology')[0]):
        for key in ('assessability', 'direction', 'confidence', 'clinical_role'):
            assert projected[key] == data[key]


def team_payload():
    return dict(judgment_reviews=[dict(source_ref='S1', disposition='adopt', rationale='有限支持', required_response=False)],
                problems=[dict(problem_id='P1', title='急性变化', relationship='primary', statement='肺炎可能',
                               assessability='partially_assessable', confidence='moderate', rationale='现有记录支持', source_refs=['S1'])],
                synthesis_rationale='有限判断', coverage_review='已复核当前资料')


def test_duplicate_judgment_review_is_locally_repaired_without_rewriting_synthesis():
    import json
    from copy import deepcopy
    from src.agents.common.team_synthesis import TeamSynthesis
    from src.llm.base import LLMResponse
    from src.llm.structured import StructuredLLMGenerator
    original = team_payload()
    original['judgment_reviews'].append(deepcopy(original['judgment_reviews'][0]))
    prompts = []

    class LLM:
        def complete(self, messages, **kwargs):
            prompts.append(messages)
            result = original if len(prompts) == 1 else {'edits': [
                {'op': 'remove_indices', 'path': '/judgment_reviews', 'value': [1]}]}
            return LLMResponse(content=json.dumps(result), raw={})

    result, trace = StructuredLLMGenerator(LLM(), temperature=0, max_tokens=1000).generate(
        schema_model=TeamSynthesis, schema_name='team', system_prompt='system',
        user_prompt='user', repair_on_validation_error=True,
        string_field_constraints={'JudgmentReview': {'source_ref': {'S1'}}})
    assert len(result.judgment_reviews) == 1
    assert result.problems[0].statement == original['problems'][0]['statement']
    assert "'S1': [0, 1]" in prompts[1][-1].content
    assert '/judgment_reviews' in prompts[1][-1].content
    assert 'Do not rename duplicate IDs' in prompts[1][-1].content
    assert '"JudgmentReview": {"source_ref": ["S1"]}' in prompts[1][-1].content
    assert 'edits' in prompts[1][0].content
    assert trace['attempts'][1]['validated']


def test_duplicate_errors_report_all_affected_arrays_together():
    from copy import deepcopy
    import pytest
    from src.agents.common.team_synthesis import TeamSynthesis
    payload = team_payload()
    payload['judgment_reviews'].append(deepcopy(payload['judgment_reviews'][0]))
    disposition = dict(issue_id='Q1', outcome='covered', rationale='现有意见覆盖')
    payload['issue_dispositions'] = [disposition, deepcopy(disposition)]
    with pytest.raises(ValueError) as error:
        TeamSynthesis.model_validate(payload)
    assert '/judgment_reviews: Duplicate source_ref' in str(error.value)
    assert '/issue_dispositions: Duplicate issue_id' in str(error.value)


def test_obsolete_review_repair_preserves_active_ids_and_clinical_content():
    import json
    from copy import deepcopy
    from test_expert_protocol import setup_case, Responses
    from src.agents.mdt_chair.agent import MDTChairAgent
    _, bundle, original = setup_case()
    payload = deepcopy(original)
    payload['judgment_reviews'].append({**payload['judgment_reviews'][0], 'source_ref': 'S999'})
    llm = Responses([payload, {'edits': [dict(op='remove_indices', path='/judgment_reviews', value=[4])]}])
    agent = MDTChairAgent(llm, prompt_path='src/prompts/mdt_chair/expert_synthesis.md', max_attempts=2)
    result, trace = agent.integrate(bundle)
    assert [r.source_ref for r in result.team_synthesis.judgment_reviews] == [r['source_ref'] for r in original['judgment_reviews']]
    assert result.team_synthesis.problems[0].statement == original['problems'][0]['statement']
    error = trace['attempts'][0]['validation_error']
    assert 'obsolete indexes=[4]' in error
    assert 'missing=[]' in error
    assert 'do not rename or renumber' in error
    assert json.loads(trace['attempts'][1]['content'])['edits'][0]['value'] == [4]


def test_invalid_issue_link_names_exact_path_and_keeps_valid_links():
    import pytest
    from src.agents.common.team_synthesis import TeamSynthesis
    from src.llm.structured import _apply_repair_edits
    payload = team_payload()
    payload['issue_dispositions'] = [dict(issue_id='Q1', outcome='covered',
        rationale='已有意见覆盖', source_refs=['S1'], linked_issue_ids=['Q1', 'N1'])]
    with pytest.raises(ValueError, match='/issue_dispositions/0/linked_issue_ids') as error:
        TeamSynthesis.model_validate(payload)
    assert "['N1']" in str(error.value)
    fixed = _apply_repair_edits(payload, {'edits': [dict(op='replace',
        path='/issue_dispositions/0/linked_issue_ids', value=['Q1'])]})
    assert TeamSynthesis.model_validate(fixed).issue_dispositions[0].linked_issue_ids == ['Q1']


def test_duplicate_assignment_error_identifies_specialty_and_indexes():
    import pytest
    from src.agents.common.team_synthesis import ClinicalIssue
    from src.llm.structured import _apply_repair_edits
    issue = dict(issue_id='Q1', origin='chair', kind='clarification', question='区分急慢性变化',
        target_specialties=['pulmonology', 'thoracic_radiology'],
        assignments=[dict(specialty='pulmonology', question='解释急性变化'),
                     dict(specialty='pulmonology', question='解释慢性变化'),
                     dict(specialty='thoracic_radiology', question='解释影像文字')],
        source_refs=['S1'], decision_impact='病因判断', closure_criterion='明确范围')
    with pytest.raises(ValueError, match="'pulmonology': \\[0, 1\\]"):
        ClinicalIssue.model_validate(issue)
    fixed = _apply_repair_edits(issue, {'edits': [
        dict(op='replace', path='/assignments/0/question', value='解释急性和慢性变化'),
        dict(op='remove_indices', path='/assignments', value=[1])]})
    result = ClinicalIssue.model_validate(fixed)
    assert [a.specialty for a in result.assignments] == ['pulmonology', 'thoracic_radiology']


def test_same_issue_new_target_continues_but_cosmetic_change_stops():
    from src.agents.common.team_synthesis import MDTChairResult, TeamSynthesis
    from src.agents.mdt_discussion.integration import decide_discussion_continuation
    payload = team_payload()
    payload['issues'] = [dict(issue_id='Q1', origin='chair', kind='clarification', question='解释慢性变化',
        target_specialties=['pulmonology'], assignments=[dict(specialty='pulmonology', question='解释慢性变化')],
        source_refs=['S1'], decision_impact='区分急慢性', closure_criterion='明确慢性变化依据')]
    previous = MDTChairResult(case_id='synthetic', team_synthesis=TeamSynthesis.model_validate(payload))
    current = previous.model_copy(deep=True)
    current.team_synthesis.issues[0].assignments[0].question = '解释未回答的急性变化'
    args = dict(previous=previous, current=current, round_number=1, max_rounds=3, responses=[], reviews=[])
    assert decide_discussion_continuation(**args)['continue_discussion']
    assert decide_discussion_continuation(**{**args, 'round_number': 3})['stop_kind'] == 'budget'
    current.team_synthesis.issues[0].assignments[0].question = '解释 慢性变化？'
    assert decide_discussion_continuation(**args)['stop_kind'] == 'no_progress'
    current.team_synthesis.issues = []
    current.team_synthesis.dissent = ['资料已经充分讨论，仍保留明确异议']
    assert decide_discussion_continuation(**args)['stop_kind'] == 'completed'


def test_facets_are_unique_per_problem_and_time_and_include_severity():
    from copy import deepcopy
    import pytest
    from src.agents.common.team_synthesis import TeamSynthesis
    data = team_payload()
    data['problems'].append({**data['problems'][0], 'problem_id': 'P2', 'relationship': 'comorbidity'})
    facet = dict(problem_id='P1', dimension='severity', statement='中度', status='supported',
                 confidence='moderate', rationale='文字记录', source_refs=['S1'])
    data['diagnostic_facets'] = [facet, {**facet, 'problem_id': 'P2'},
        {**facet, 'timeframe': dict(kind='historical', description='既往') }]
    assert len(TeamSynthesis.model_validate(data).diagnostic_facets) == 3
    data['diagnostic_facets'].append(deepcopy(facet))
    with pytest.raises(ValueError, match='unique dimensions'):
        TeamSynthesis.model_validate(data)


def test_stop_kind_is_independent_of_acceptance_and_report_preserves_it():
    import pytest
    from test_expert_protocol import setup_case, run_expert
    from src.agents.mdt_discussion.final_report import project_team_report
    state, bundle, payload = setup_case()
    payload['dissent'] = ['讨论已充分，但仍保留异议']
    result = run_expert(bundle, [payload])
    with pytest.raises(ValueError, match='stop kind'):
        project_team_report('100-IPF', result.model_dump(), [], '完成', None, state)
    result.team_synthesis.stop_kind = 'completed'
    report, _ = project_team_report('100-IPF', result.model_dump(), [], '没有可推进议题', None, state)
    assert report.team_synthesis.stop_kind == 'completed'
    assert report.team_synthesis.acceptance == 'explicit_dissent'
    assert report.stop_reason == '没有可推进议题'


def test_source_snapshot_keeps_version_conditions_relations_and_guidelines():
    from test_expert_protocol import setup_case, run_expert
    from src.agents.common.decision_state import project_active_specialty_outputs
    from src.agents.mdt_chair.agent import build_chair_prompt_bundle
    state, _, payload = setup_case()
    outputs = project_active_specialty_outputs(state)
    assessment = outputs['pulmonology']['specialty_assessments']['assessments'][0]
    assessment['evidence'] = {'evidence_relations': [dict(evidence_ids=['ev-1'], graph_unit_id='gu-1',
        quote='发热记录', direction='supports', function='qualifying')]}
    assessment['guideline_evidence'] = [dict(chunk_id='g-1', relevance='判断范围', application='限定病因')]
    bundle = build_chair_prompt_bundle('100-IPF', outputs)
    result = run_expert(bundle, [payload])
    ref = payload['judgment_reviews'][0]['source_ref']
    source = result.team_synthesis.source_catalog[ref]
    assert source['version_id'] == result.team_synthesis.dependency_versions[ref]
    assert source['conditions'] == assessment['conditions']
    assert source['evidence_relations']['supporting'] == ['ev-1']
    assert source['evidence_relations']['qualifying'] == ['ev-1']
    assert source['guideline_evidence'][0]['chunk_id'] == 'g-1'
    outputs['pulmonology']['specialty_assessments']['assessments'][0]['evidence'] = {}
    later = build_chair_prompt_bundle('100-IPF', outputs, source_seed=bundle)
    assert later.evidence_registry['ev-1'].quote == '发热记录'


def test_configured_chair_prompt_controls_actual_generation(tmp_path):
    from test_expert_protocol import setup_case, Responses
    from src.agents.mdt_chair.agent import MDTChairAgent
    from src.workbench.runner import build_run_signature
    from src.utils.config import load_yaml
    config = load_yaml('configs/agents/mdt_chair/agent.yaml')
    assert config['prompt'] == 'src/prompts/mdt_chair/expert_synthesis.md'
    assert 'ledger_prompt' not in config
    prompt = tmp_path / 'chair.md'
    prompt.write_text('CONFIGURED-CHAIR {{ chair_input }} {{ output_schema }}')
    config = dict(protocol_version='expert.v2', prompt=str(prompt), guideline_retrieval={'enabled': False})
    import yaml
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(config))
    _, bundle, payload = setup_case()
    agent = MDTChairAgent.from_config(path, Responses([payload]))
    _, trace = agent.integrate(bundle)
    assert 'CONFIGURED-CHAIR' in trace['prompt']
    signature = build_run_signature(config)
    prompt.write_text('CHANGED-CHAIR')
    assert signature != build_run_signature(config)


def test_synthesis_acceptance_requires_all_specialties_and_exact_revision():
    import pytest
    from src.agents.common.team_synthesis import TeamSynthesis, Specialty
    from test_expert_protocol import setup_case, run_expert
    _, bundle, payload = setup_case()
    team = run_expert(bundle, [payload]).team_synthesis.model_dump()
    team['acceptance_records'] = [dict(specialty=s, revision=team['revision'], snapshot_hash=team['snapshot_hash'],
        decision='accept', rationale='已核对本专业判断') for s in Specialty.__args__]
    assert TeamSynthesis.model_validate(team).acceptance == 'accepted_with_boundaries'
    team['judgment_boundaries'] = []
    assert TeamSynthesis.model_validate(team).acceptance == 'jointly_accepted'
    team['acceptance_records'][0]['decision'] = 'dissent'
    assert TeamSynthesis.model_validate(team).acceptance == 'explicit_dissent'
    team['acceptance_records'].pop(0)
    assert TeamSynthesis.model_validate(team).acceptance == 'chair_adjudicated'
    team['acceptance_records'][0]['revision'] += 1
    with pytest.raises(ValueError, match='exact synthesis'):
        TeamSynthesis.model_validate(team)


def test_specialty_acceptance_is_bound_by_program_and_independent_of_prior_votes():
    from src.agents.mdt_discussion.specialty_agent import SpecialtyDiscussionAgent
    from test_expert_protocol import setup_case, run_expert, Responses
    state, bundle, payload = setup_case()
    team = run_expert(bundle, [payload]).team_synthesis
    agent = SpecialtyDiscussionAgent(Responses([dict(decision='accept_with_boundaries', rationale='病原仍未定', boundaries=['病原未定'])]),
        specialty='pulmonology', config={'guideline_retrieval': {'enabled': False}})
    from src.agents.common.decision_state import project_active_specialty_outputs
    record, trace = agent.review_synthesis(team=team, specialty_output=project_active_specialty_outputs(state)['pulmonology'])
    assert record.revision == team.revision and record.snapshot_hash == team.snapshot_hash
    assert record.specialty == 'pulmonology'
    assert 'acceptance_records' not in trace['prompt']



def test_followup_run_links_immutable_case_versions_via_api(tmp_path, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    from src.workbench.catalog import RunCatalog
    from src.workbench.events import EventStore
    from src.workbench.runner import AGENTS, RunOrchestrator
    api_module = importlib.import_module('src.workbench.app')
    for agent in AGENTS:
        path = tmp_path / f'configs/agents/{agent}/agent.yaml'
        path.parent.mkdir(parents=True)
        path.write_text('model: synthetic')
    catalog=RunCatalog(tmp_path)
    runner=RunOrchestrator(tmp_path, catalog, EventStore(tmp_path/'events.sqlite3'))
    monkeypatch.setattr(api_module,'catalog',catalog)
    monkeypatch.setattr(api_module,'orchestrator',runner)
    started=[]
    monkeypatch.setattr(runner,'start',lambda run_id, path: started.append(run_id))
    client=TestClient(api_module.app)
    def create(**extra):
        return client.post('/api/runs',json=dict(case_id='case-1',source='paste',raw_text='初始记录',**extra))
    first=create().json()
    response=client.post('/api/runs',json=dict(case_id='case-1',source='paste',raw_text='初始记录；新增肺功能结果',parent_run_id=first['id']))
    assert response.status_code == 202, response.text
    follow=response.json(); manifest=follow['manifest']
    assert manifest['parent_run_id'] == first['id']
    assert manifest['parent_case_version_id'] == first['manifest']['case_version_id']
    assert manifest['case_version_id'] != manifest['parent_case_version_id']
    assert client.get(f"/api/runs/{follow['id']}").json()['manifest'] == manifest
    parent_path=catalog.run_dir(first['id'])/'case-1_input.txt'
    assert parent_path.read_text() == '初始记录'
    for parent, case_id, status in ((first['id'],'other-case',422),('missing','case-1',404),('../escape','case-1',422)):
        response=client.post('/api/runs',json=dict(case_id=case_id,source='paste',raw_text='后续记录',parent_run_id=parent))
        assert response.status_code == status
    parent_path.write_text('偷偷修改')
    assert create(parent_run_id=first['id']).status_code == 422
    assert len(started) == 2
