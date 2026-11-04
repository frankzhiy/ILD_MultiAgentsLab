"""Contract tests use synthetic cases, without targeting a reference diagnosis."""
import json

import pytest
from pydantic import ValidationError

from src.agents.common.diagnostic_resolution import DiagnosticResolution
from src.agents.mdt_chair.agent import MDTChairAgent
from src.agents.mdt_chair.models import CaseEvidenceCitation
from src.guidelines.queries import clinical_queries
from src.llm.base import LLMResponse
from src.llm.structured import StructuredGenerationError
from test_expert_protocol import setup_case


def exploration():
    return dict(problem_representation='慢性咳嗽、双肺间质异常，近期出现发热及局灶实变。',
        candidates=[dict(candidate_id='C1', diagnosis='肺炎', level='disease',
            explains='解释急性发热及局灶实变。', fails_to_explain='不能单独解释长期双肺异常。',
            supporting_evidence_refs=['E1'], discriminating_information='病程和影像分布',
            retrieval_question='pneumonia versus chronic interstitial disease')],
        combined_phenotype_review='需要区分慢性基础病与急性叠加过程。',
        unrepresented_findings='长期双肺异常的病因未定。', insufficient_candidate_basis='')


class Sequence:
    supports_json_schema = False
    def __init__(self, values):
        self.values = iter(values)
        self.prompts = []
        self.systems = []
    def complete(self, messages, **kwargs):
        self.prompts.append(messages[-1].content)
        self.systems.append(messages[0].content)
        return LLMResponse(content=json.dumps(next(self.values), ensure_ascii=False), raw={})


def integrate(candidate, change=None):
    _, bundle, team = setup_case()
    bundle.evidence_registry['E1'] = CaseEvidenceCitation(evidence_ref='E1', graph_unit_id='g1',
        evidence_ids=['ev1'], quote='慢性咳嗽及双肺间质异常，近期发热并见局灶实变。')
    team['diagnostic_resolution']['candidate_dispositions'] = [dict(candidate_id='C1',
        outcome='preferred', rationale='患者证据支持急性过程。')]
    team['problems'][0]['candidates'] = [dict(candidate_id='C1', position='preferred', confidence='moderate', rationale='现有病程支持。', source_refs=team['problems'][0]['source_refs'])]
    team['diagnostic_resolution'].update(primary_candidate_id='C1', primary_diagnosis='')
    if change:
        change(team)
    llm = Sequence([candidate, team])
    agent = MDTChairAgent(llm, prompt_path='src/prompts/mdt_chair/expert_synthesis.md', max_attempts=1)
    return agent.integrate(bundle)[0], llm


def test_independent_candidate_is_patient_grounded_and_reviewed_before_sealing():
    result, llm = integrate(exploration())
    assert len(llm.prompts) == 2
    assert 'judgment_reviews' not in llm.prompts[0]
    assert '近期发热' in llm.prompts[0]
    assert '以ILD为核心' in llm.prompts[0]
    assert '主诊断聚焦ILD' in llm.prompts[1]
    assert '主持人独立比较具体疾病并作选择' in llm.prompts[1]
    assert all('选择当前最可能的具体疾病或综合征' in text for text in llm.systems)
    assert result.team_synthesis.diagnostic_exploration.candidates[0].candidate_id == 'C1'
    assert result.team_synthesis.diagnostic_resolution.candidate_dispositions[0].outcome == 'preferred'


def test_json_object_rejects_candidate_reference_not_in_patient_catalog():
    candidate = exploration()
    candidate['candidates'][0]['supporting_evidence_refs'] = ['invented']
    with pytest.raises(StructuredGenerationError, match='Unknown candidate evidence refs'):
        integrate(candidate)


def test_chair_cannot_silently_drop_an_explored_candidate():
    with pytest.raises(StructuredGenerationError, match='every explored candidate'):
        integrate(exploration(), lambda team: team['diagnostic_resolution'].update(candidate_dispositions=[]))


@pytest.mark.parametrize('level', ['pattern', 'category', 'finding'])
@pytest.mark.parametrize('status', ['specific_diagnosis', 'specific_differential'])
def test_specific_completion_requires_disease_or_syndrome_level(level, status):
    with pytest.raises(ValidationError, match='does not complete'):
        DiagnosticResolution(status=status, achieved_level=level,
            primary_diagnosis='待解释的肺部异常', goal_review='仅获得描述层级。')


def test_input_limited_is_not_a_diagnostic_outcome():
    with pytest.raises(ValidationError, match='status'):
        DiagnosticResolution(status='input_limited', achieved_level='category',
            primary_diagnosis='慢性肺实质病变', goal_review='当前判断。',
            remaining_gaps=[dict(kind='patient_evidence', question='病变分布',
                why_unresolved='原文未记录。')])


@pytest.mark.parametrize('level', ['disease', 'syndrome', 'pattern', 'category', 'finding'])
def test_provisional_judgment_does_not_require_a_specific_level_or_missing_data(level):
    result = DiagnosticResolution(status='provisional_diagnosis', achieved_level=level,
        primary_diagnosis='当前最支持的判断', goal_review='已比较主要解释及其依据。')
    assert result.remaining_gaps == []
    with pytest.raises(ValidationError, match='must be discussed'):
        DiagnosticResolution(**{**result.model_dump(), 'remaining_gaps': [dict(kind='inference',
            question='已有的混合发现能否共同解释？', why_unresolved='尚未比较。', issue_ids=['I1'])]})


def test_clinical_queries_keep_late_facts_and_exclude_filename_and_task_instructions():
    queries = clinical_queries({'case_id': 'IPF-answer', 'consultation': {'question': '必须CPFE'},
        'case_input': json.dumps({'evidence_blocks': [{'text': '早期事实。' * 800},
                                                    {'text': '最后记录了肺气肿。'}]}),
        'assessment': {'diagnosis': '具体候选A'}})
    joined = ' '.join(queries)
    assert '最后记录了肺气肿' in joined
    assert '具体候选A' in joined
    assert 'IPF-answer' not in joined and '必须CPFE' not in joined


def test_new_run_task_and_index_are_immutable_and_retry_preserves_task(tmp_path):
    from src.workbench.runner import AGENTS, RunOrchestrator
    from src.workbench.catalog import RunCatalog
    from src.workbench.events import EventStore
    from src.utils.persistence import verify_protocol_snapshot
    for agent in AGENTS:
        directory = tmp_path / 'configs/agents' / agent
        directory.mkdir(parents=True)
        (directory / 'agent.yaml').write_text('model: synthetic\n')
    runner = RunOrchestrator(tmp_path, RunCatalog(tmp_path), EventStore(tmp_path / 'events.sqlite3'))
    context = dict(template='review_diagnosis', question='请复核原文诊断依据。')
    request = dict(source='paste', case_id='case-a', raw_text='已有检查原文。', consultation=context)
    run_id, _ = runner.prepare(request)
    directory = runner.catalog.run_dir(run_id)
    manifest = json.loads((directory / '.workbench_run.json').read_text())
    assert manifest['consultation'] == context
    assert 'knowledge_index' in json.loads((directory / 'protocol_manifest.json').read_text())
    verify_protocol_snapshot(tmp_path, directory)
    retry_id, _ = runner.prepare({**request, 'consultation': {'template': 'assess_ild'}}, retry_run_id=run_id)
    assert runner.catalog.run_summary(runner.catalog.run_dir(retry_id))['manifest']['consultation'] == context
    manifest['consultation'] = {'template': 'assess_ild', 'question': ''}
    (directory / '.workbench_run.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='会诊任务快照'):
        verify_protocol_snapshot(tmp_path, directory, allow_stage_rerun=True)


def test_api_defaults_and_batch_case_overrides():
    from src.workbench.app import CreateRunRequest, CreateBatchRequest
    assert CreateRunRequest(case_id='a').consultation.template == 'refine_diagnosis'
    batch = CreateBatchRequest(kind='run', case_ids=['a', 'b'], case_consultations={'b': {'template': 'assess_ild'}})
    assert batch.case_consultations['b'].template == 'assess_ild'
    with pytest.raises(ValidationError):
        CreateBatchRequest(kind='run', case_ids=['a'], case_consultations={'b': {'template': 'assess_ild'}})


def test_old_chair_snapshot_is_readable_but_requires_new_review_for_new_stages():
    from src.workbench.catalog import RunCatalog
    from test_expert_protocol import run_expert
    _, bundle, payload = setup_case()
    value = run_expert(bundle, [payload]).model_dump(mode='json')
    assert RunCatalog._is_current_chair_output(value)
    value['team_synthesis'].pop('diagnostic_resolution')
    assert not RunCatalog._is_current_chair_output(value)


def test_batch_uses_each_selected_task_and_preserves_unoverridden_default(monkeypatch, tmp_path):
    import asyncio
    from src.workbench.runner import RunOrchestrator
    from src.workbench.catalog import RunCatalog
    from src.workbench.events import EventStore
    from pathlib import Path
    cases = tmp_path / 'data/raw_cases'
    cases.mkdir(parents=True)
    for case in ('a', 'b'):
        (cases / f'{case}.txt').write_text('病例原文。')
    runner = RunOrchestrator(tmp_path, RunCatalog(tmp_path), EventStore(tmp_path / 'events.sqlite3'))
    requests = []
    def prepare(request):
        requests.append(request)
        return request['case_id'], cases / f"{request['case_id']}.txt"
    monkeypatch.setattr(runner, 'prepare', prepare)
    async def start(*args):
        return None
    monkeypatch.setattr(runner, 'start', start)
    monkeypatch.setattr(runner.catalog, 'run_dir', Path)
    monkeypatch.setattr(runner.catalog, 'run_summary', lambda _: {'discussion_complete': True})
    async def exercise():
        batch = runner.create_batch(dict(kind='run', case_ids=['a', 'b'], max_case_concurrency=1, max_request_concurrency=2,
            consultation={'template': 'refine_diagnosis'}, case_consultations={'b': {'template': 'assess_ild'}}))
        await runner.batch_tasks[batch['id']]
    asyncio.run(exercise())
    assert [r['consultation']['template'] for r in requests] == ['refine_diagnosis', 'assess_ild']


def test_unresolved_candidate_must_have_a_real_issue_route():
    from src.agents.common.team_synthesis import TeamSynthesis
    _, _, payload = setup_case()
    payload['diagnostic_resolution']['candidate_dispositions'] = [dict(candidate_id='C1',
        outcome='needs_discussion', rationale='还有现有材料可以比较。')]
    with pytest.raises(ValidationError, match='Route each unresolved candidate'):
        TeamSynthesis.model_validate(payload)
    payload['diagnostic_resolution']['candidate_dispositions'][0]['issue_ids'] = ['invented']
    with pytest.raises(ValidationError, match='open issue'):
        TeamSynthesis.model_validate(payload)


def test_other_problem_inference_does_not_downgrade_specific_primary_diagnosis():
    result = DiagnosticResolution(status='specific_diagnosis', achieved_level='disease',
        primary_diagnosis='具体疾病A', goal_review='主诊断已形成，另有并存问题。',
        remaining_gaps=[dict(kind='inference', scope='other_problem',
            question='另一个局部病变如何解释？', why_unresolved='尚可进一步比较。', issue_ids=['I1'])])
    assert result.status == 'specific_diagnosis'
    with pytest.raises(ValidationError, match='must be discussed'):
        DiagnosticResolution(**{**result.model_dump(), 'remaining_gaps': [dict(kind='inference',
            scope='primary_diagnosis', question='主诊断仍未比较？', why_unresolved='未解决。', issue_ids=['I1'])]})


def test_no_specific_candidate_still_produces_a_current_opinion_and_report():
    from src.agents.mdt_discussion.integration import decide_discussion_continuation
    from src.agents.mdt_discussion.final_report import project_team_report
    candidate = exploration()
    candidate.update(candidates=[], insufficient_candidate_basis='现有发现支持慢性肺实质病变，不推造具体亚型。')
    def current_judgment(team):
        team['diagnostic_resolution'] = dict(status='provisional_diagnosis', achieved_level='category',
            primary_diagnosis='慢性肺实质病变', goal_review='整合病程与肺部所见，形成当前最支持的判断。',
            candidate_dispositions=[], remaining_gaps=[])
        team['diagnostic_opinion'].update(diagnosis='当前倾向慢性肺实质病变。',
            reasoning='慢性病程与双肺异常共同支持此判断。',
            differential_reasoning='急性局灶过程不能单独解释长期双肺异常。', limitations=[])
        team['problems'][0].update(candidates=[], title='慢性肺实质病变', statement='当前倾向慢性肺实质病变。',
            rationale='慢性病程与双肺异常共同支持。', limitations=[])
        team['judgment_boundaries'] = []
    result, _ = integrate(candidate, current_judgment)
    decision = decide_discussion_continuation(previous=result, current=result,
        round_number=1, max_rounds=3, responses=[], reviews=[])
    assert decision['stop_kind'] == 'completed'
    assert '已形成当前诊断意见' in decision['stop_reason']
    report, _ = project_team_report(result.case_id, result.model_dump(mode='json'), [],
        decision['stop_reason'], None, None)
    assert report.team_synthesis.diagnostic_resolution.primary_diagnosis == '慢性肺实质病变'
    assert report.team_synthesis.diagnostic_opinion.diagnosis == '当前倾向慢性肺实质病变。'


def test_specific_primary_can_continue_discussion_of_a_separate_candidate():
    from src.agents.common.team_synthesis import TeamSynthesis
    _, _, payload = setup_case()
    payload['issues'] = [dict(issue_id='I1', origin='chair', kind='clarification',
        question='局灶异常是否为并存过程？', target_specialties=['thoracic_radiology'],
        assignments=[dict(specialty='thoracic_radiology', question='解释局灶异常的范围。')],
        source_refs=[payload['judgment_reviews'][1]['source_ref']],
        decision_impact='明确并存过程', closure_criterion='解释范围及证据')]
    payload['diagnostic_resolution']['candidate_dispositions'] = [dict(candidate_id='C1',
        scope='other_problem', outcome='needs_discussion', issue_ids=['I1'], rationale='并存过程仍需解释。')]
    team = TeamSynthesis.model_validate(payload)
    assert team.diagnostic_resolution.status == 'provisional_diagnosis'
    assert team.issues[0].issue_id == 'I1'


def test_successor_reports_all_lost_aliases_at_current_candidate_indexes():
    from src.agents.common.diagnostic_resolution import DiagnosticExploration
    payload = exploration()
    first = payload['candidates'][0]
    first['aliases'] = ['别名A']
    second = {**first, 'candidate_id': 'C2', 'diagnosis': '疾病B', 'aliases': ['别名B']}
    payload['candidates'].append(second)
    previous = DiagnosticExploration.model_validate(payload)
    current = previous.model_copy(deep=True)
    current.candidates.reverse()
    for candidate in current.candidates:
        candidate.aliases = []
    with pytest.raises(ValueError) as failure:
        current.validate_successor(previous)
    assert '/candidates/1/aliases' in str(failure.value)
    assert '/candidates/0/aliases' in str(failure.value)
    assert '别名A' in str(failure.value) and '别名B' in str(failure.value)
