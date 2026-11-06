import json
from copy import deepcopy

import pytest

from src.agents.common.decision_state import initialize_decision_state, project_active_specialty_outputs
from src.agents.common.team_synthesis import TeamSynthesis, KeyEvidenceNeed
from src.agents.mdt_chair.agent import MDTChairAgent, build_chair_prompt_bundle
from src.agents.mdt_chair.expert import synthesis_hash
from src.agents.mdt_discussion.final_report import project_team_report
from src.agents.mdt_discussion.routing import build_discussion_tasks
from src.llm.base import LLMResponse
from src.llm.structured import StructuredGenerationError


class Responses:
    supports_json_schema = True
    def __init__(self, items, exploration=None):
        self.exploration = exploration
        self.items = iter(items)
        self.calls = 0
    def complete(self, messages, **kwargs):
        self.calls += 1
        if kwargs.get('response_format', {}).get('json_schema', {}).get('name') == 'diagnostic_exploration' or messages[-1].content.startswith('# MDT：独立候选形成'):
            return LLMResponse(content=json.dumps(self.exploration or dict(problem_representation='患者文字需要进一步疾病解释。', candidates=[],
                combined_phenotype_review='没有足够患者事实形成新的组合候选。', unrepresented_findings='暂无额外发现。',
                insufficient_candidate_basis='测试输入不包含患者原文判别证据。'), ensure_ascii=False), raw={})
        assert '100-IPF' not in messages[-1].content
        assert '{{' not in messages[-1].content
        return LLMResponse(content=json.dumps(next(self.items), ensure_ascii=False), raw={})


def setup_case():
    outputs = {}
    for specialty in ('pulmonology','thoracic_radiology','rheumatology','pathology'):
        outputs[specialty] = {
            'specialty_assessments': dict(specialty_question='肺部问题', assessability='partially_assessable',
                assessments=[dict(assessment_id=specialty+'-a1', role='primary', assessment_type='working_diagnosis',
                statement='肺炎可能解释急性变化' if specialty=='pulmonology' else '本专业的有限判断',
                assessability='partially_assessable', direction='supports', confidence='low', clinical_role='primary', status='possible', medical_basis='已有文字记录', decision_impact='诊断定位',
                claims=[{'statement': '本专业有限的原子判断', 'evidence_relations': []}], evidence={'evidence_relations':[]})], boundaries=['有资料限制']),
            'interspecialty_questions': {'questions': []}}
    state = initialize_decision_state('100-IPF', outputs)
    bundle = build_chair_prompt_bundle('100-IPF', project_active_specialty_outputs(state))
    refs = [s['specialty_assessments'][0]['source_ref'] for s in bundle.prompt_input['specialties']]
    team = dict(judgment_reviews=[dict(source_ref=r, disposition='adopt', rationale='原文支持有限判断',
                                     evidence_refs=[], required_response=False) for r in refs],
        diagnostic_opinion=dict(case_formulation='急性呼吸道症状与同期肺部异常构成本次主要问题。',
            diagnosis='当前首先考虑肺炎，具体病原尚未明确。',
            reasoning='症状发生与肺部异常的时间相符；其余专科未提供更有解释力的病因。',
            differential_reasoning='当前证据尚不足以将急性变化归为独立的慢性间质性肺病。',
            problem_ids=['P1'], source_refs=[refs[0]], evidence_refs=[], limitations=['病原未定']),
        problems=[dict(problem_id='P1', title='急性肺部病变', relationship='primary', statement='首先考虑肺炎',
                       assessability='partially_assessable', confidence='moderate', rationale='症状与文字报告符合',
                       source_refs=refs, evidence_refs=[], candidates=[], limitations=['病原未定'])],
        judgment_boundaries=[dict(boundary_id='B1', problem_id='P1', established='支持肺炎',
            undetermined='病原未定', reason='缺乏病原结果', decision_impact='不能确定病原', source_refs=refs)],
        disagreements=[], issues=[], issue_dispositions=[], evidence_needs=[], conditional_contributions=[], dissent=[],
        synthesis_rationale='按同一问题整合', coverage_review='无足够证据预设ILD', guideline_evidence=[])
    team['diagnostic_resolution'] = dict(status='provisional_diagnosis', achieved_level='disease',
        primary_diagnosis='肺炎', goal_review='已形成有限具体疾病判断，病原未定。',
        candidate_dispositions=[], remaining_gaps=[])
    return state,bundle,team


def test_chair_must_produce_a_case_level_diagnosis_beyond_the_opinion_ledger():
    _, bundle, payload = setup_case()
    payload.pop('diagnostic_opinion')
    with pytest.raises(StructuredGenerationError, match='diagnostic_opinion'):
        run_expert(bundle, [payload])


def test_new_chair_schema_requires_a_non_null_diagnostic_opinion():
    from src.agents.common.team_synthesis import ChairSynthesisDraft
    schema = ChairSynthesisDraft.model_json_schema()
    assert 'diagnostic_opinion' in schema['required']
    assert schema['properties']['diagnostic_opinion'] == {'$ref': '#/$defs/ChairDiagnosticOpinionDraft'}
    assert 'diagnosis' not in schema['$defs']['ChairDiagnosticOpinionDraft']['properties']


def test_chair_seals_diagnosis_from_adjudicated_problems_and_preserves_reasoning():
    state, bundle, payload = setup_case()
    original = deepcopy(bundle.prompt_input)
    opinion = payload['diagnostic_opinion']
    opinion.update(diagnosis='综合解释C是本次主要病变，解释B作为并存因素保留。',
        reasoning='将各科保留的时间、分布与全身表现共同考虑，解释C比单一器官解释更连贯。',
        differential_reasoning='解释A仅覆盖局部表现，解释B不能单独解释本次变化。')
    assert all(opinion['diagnosis'] != item['statement']
               for specialty in bundle.prompt_input['specialties']
               for item in specialty['specialty_assessments'])
    associated = {**payload['problems'][0], 'problem_id': 'P2', 'relationship': 'comorbidity',
                  'statement': '并存解释B。', 'limitations': []}
    payload['problems'].append(associated)
    result = run_expert(bundle, [payload])
    result.team_synthesis.stop_kind = 'completed'
    report, _ = project_team_report('100-IPF', result.model_dump(mode='json'), [], '完成', None, state)
    expected = '首先考虑肺炎\n\n并存问题：并存解释B。'
    assert result.team_synthesis.diagnostic_opinion.diagnosis == expected
    assert report.team_synthesis.diagnostic_opinion.diagnosis == expected
    assert report.team_synthesis.diagnostic_opinion.reasoning == opinion['reasoning']
    assert bundle.prompt_input == original
    result.team_synthesis.diagnostic_opinion.diagnosis = opinion['diagnosis']
    result.team_synthesis.snapshot_hash = synthesis_hash(result.team_synthesis)
    with pytest.raises(ValueError, match='综合诊断与已审定的临床问题不一致'):
        project_team_report('100-IPF', result.model_dump(mode='json'), [], '完成', None, state)


def test_chair_diagnosis_cannot_present_a_rejected_opinion_as_its_retained_basis():
    _, bundle, payload = setup_case()
    payload['judgment_reviews'][0]['disposition'] = 'reject'
    with pytest.raises(StructuredGenerationError, match='保留的专科判断'):
        run_expert(bundle, [payload])


def test_chair_diagnosis_must_cover_the_primary_problem():
    _, bundle, payload = setup_case()
    payload['diagnostic_opinion']['problem_ids'] = ['not-a-problem']
    with pytest.raises(StructuredGenerationError, match='diagnostic_opinion'):
        run_expert(bundle, [payload])


def test_chair_can_reject_all_specialty_interpretations_and_use_direct_case_evidence():
    from src.agents.mdt_chair.models import CaseEvidenceCitation
    _, bundle, payload = setup_case()
    bundle.evidence_registry['E1'] = CaseEvidenceCitation(evidence_ref='E1',
        graph_unit_id='unit-1', evidence_ids=['ev-1'], quote='合成病例中的时间与器官发现。')
    for review in payload['judgment_reviews']:
        review['disposition'] = 'reject'
    payload['diagnostic_opinion'].update(source_refs=[], evidence_refs=['E1'],
        diagnosis='基于病例原文形成整体解释C，现有专科归因不予采纳。')
    result = run_expert(bundle, [payload])
    assert result.team_synthesis.diagnostic_opinion.source_refs == []
    assert result.team_synthesis.diagnostic_opinion.evidence_refs == ['E1']


def test_case_level_diagnosis_can_agree_with_a_specialist_but_must_cover_acute_changes():
    _, bundle, payload = setup_case()
    payload['diagnostic_opinion']['diagnosis'] = bundle.prompt_input['specialties'][0]['specialty_assessments'][0]['statement']
    assert run_expert(bundle, [payload]).team_synthesis.diagnostic_opinion.diagnosis
    acute = deepcopy(payload['problems'][0])
    acute.update(problem_id='P2', relationship='acute_contributor', title='急性叠加因素', limitations=[])
    payload['problems'].append(acute)
    with pytest.raises(StructuredGenerationError, match='diagnostic_opinion.problem_ids'):
        run_expert(bundle, [payload])
    payload['diagnostic_opinion']['problem_ids'].append('P2')
    assert run_expert(bundle, [payload]).team_synthesis.diagnostic_opinion.problem_ids == ['P1', 'P2']


def test_historical_deduplicated_summary_is_not_promoted_to_a_chair_diagnosis():
    from src.workbench.catalog import RunCatalog
    state, bundle, payload = setup_case()
    result = run_expert(bundle, [payload]).model_dump(mode='json')
    result['team_synthesis'].pop('diagnostic_opinion')
    assert not RunCatalog._is_current_chair_output(result)
    with pytest.raises(ValueError, match='缺少主持人全病例诊断意见'):
        project_team_report('100-IPF', result, [], '完成', None, state)


def run_expert(bundle, payloads, exploration=None):
    llm = Responses(payloads, exploration)
    agent = MDTChairAgent(llm, 
        prompt_path='src/prompts/mdt_chair/expert_synthesis.md', max_attempts=1)
    return agent.integrate(bundle)[0]


def test_chair_preserves_wording_without_rejecting_a_valid_synthesis():
    _, bundle, payload = setup_case()
    payload['synthesis_rationale'] = '本轮未附病理材料不等于患者未接受过肺活检。'
    result = run_expert(bundle, [payload])
    assert result.team_synthesis.synthesis_rationale == payload['synthesis_rationale']
    assert result.team_synthesis.judgment_boundaries[0].undetermined == '病原未定'


def test_report_projects_a_valid_sealed_snapshot_without_wording_rejection():
    state, bundle, payload = setup_case()
    result = run_expert(bundle, [payload])
    team = result.team_synthesis
    team.stop_kind = 'completed'
    team.problems[0].limitations = ['本轮未直接阅片。']
    team.snapshot_hash = synthesis_hash(team)
    report, _ = project_team_report('100-IPF', result.model_dump(mode='json'), [], '完成', None, state)
    assert report.team_synthesis.problems[0].limitations == team.problems[0].limitations


def test_later_chair_round_keeps_audit_catalogs_local():
    from types import SimpleNamespace
    from src.agents.mdt_discussion.models import SpecialtyTaskAnswer
    _, bundle, payload = setup_case()
    previous = run_expert(bundle, [payload])
    previous.team_synthesis.source_catalog['S001']['quote'] = '仅用于本地审计的唯一原文标记'
    agent = MDTChairAgent(Responses([payload]),
        prompt_path='src/prompts/mdt_chair/expert_synthesis.md', max_attempts=1)
    answer = SpecialtyTaskAnswer(answer_id='A1', task_id='T1', issue_type='question',
        issue_id='Q1', answerability='answered', answer='已有证据支持有限判断',
        confidence='low', medical_basis='已有记录', changed_from_previous=False,
        evidence_uses=[dict(evidence_ref='E1', effect='supporting', interpretation='支持有限判断',
                            quote='不应重复发送的讨论证据原文标记')])
    _, trace = agent.integrate(bundle, discussion_previous=previous,
                              discussion_responses=[SimpleNamespace(answers=[answer])])
    assert '仅用于本地审计的唯一原文标记' not in trace['prompt']
    assert 'source_catalog' not in trace['prompt']
    assert 'evidence_catalog' not in trace['prompt']
    assert '不应重复发送的讨论证据原文标记' not in trace['prompt']
    assert '已有证据支持有限判断' in trace['prompt']


@pytest.mark.parametrize("supports_json_schema", [True, False])
def test_chair_sends_schema_once_and_preserves_fallback(supports_json_schema):
    _, bundle, payload = setup_case()
    llm = Responses([payload])
    llm.supports_json_schema = supports_json_schema
    agent = MDTChairAgent(llm, prompt_path='src/prompts/mdt_chair/expert_synthesis.md', max_attempts=1)
    _, trace = agent.integrate(bundle)
    prompt = trace["prompt"]
    assert ("由 API 的严格 JSON Schema response_format 提供。" in prompt) == supports_json_schema
    assert ('"$defs"' in prompt) == (not supports_json_schema)
    assert llm.calls == 2


def test_review_report_is_one_sealed_non_ild_snapshot():
    state,bundle,payload = setup_case()
    result = run_expert(bundle,[payload])
    result.team_synthesis.stop_kind = 'completed'
    report,trace = project_team_report('100-IPF',result.model_dump(mode='json'),[], '审查完成', None,state)
    assert trace['model_calls'] == 0
    assert report.team_synthesis.problems[0].statement == result.team_synthesis.problems[0].statement
    assert report.team_synthesis.acceptance == 'chair_adjudicated'
    assert set(report.model_dump()) == {'schema_version', 'case_id', 'team_synthesis', 'discussion_rounds', 'stop_reason'}
    assert len(report.team_synthesis.problems) == 1
    assert report.team_synthesis.dependency_versions
    modified = result.model_dump(mode='json')
    modified['team_synthesis']['problems'][0]['statement'] = '新增错误诊断'
    with pytest.raises(ValueError,match='hash'):
        project_team_report('100-IPF',modified,[],'完成',None,state)
    modified = result.model_dump(mode='json')
    modified['team_synthesis']['diagnostic_opinion']['diagnosis'] = '未经审定的新诊断'
    with pytest.raises(ValueError, match='hash'):
        project_team_report('100-IPF', modified, [], '完成', None, state)
    result.team_synthesis.dependency_versions['S001'] = 'old-version'
    result.team_synthesis.snapshot_hash = synthesis_hash(result.team_synthesis)
    with pytest.raises(ValueError,match='obsolete'):
        project_team_report('100-IPF',result.model_dump(mode='json'),[],'完成',None,state)


def test_unopposed_judgment_still_requires_review_and_response():
    _,bundle,payload = setup_case()
    missing=deepcopy(payload); missing['judgment_reviews'].pop()
    with pytest.raises(StructuredGenerationError,match='Review every'):
        run_expert(bundle,[missing])
    payload['judgment_reviews'][0].update(disposition='qualify',required_response=True)
    with pytest.raises(StructuredGenerationError,match='Required response'):
        run_expert(bundle,[payload])
    payload['issues']=[dict(issue_id='Q1', origin='chair', kind='inference_gap', question='病因排除充分吗？',
        raised_by=[], target_specialties=['pulmonology'], assignments=[dict(specialty='pulmonology', question='说明判断依据和边界')], affected_specialties=['rheumatology'],
        source_refs=[payload['judgment_reviews'][0]['source_ref']], evidence_refs=[],
        decision_impact='能否优先归因特发性', closure_criterion='说明排除的依据或限定原意见')]
    result=run_expert(bundle,[payload])
    assert result.team_synthesis.issues[0].target_specialties == ['pulmonology']
    assert result.team_synthesis.acceptance == 'chair_adjudicated'


@pytest.mark.parametrize('has_preference', [True, False])
def test_low_confidence_candidates_survive_dissent_and_sealed_report(has_preference):
    state, bundle, payload = setup_case()
    original_state = state.model_dump(mode='json')
    problem = payload['problems'][0]
    problem.update(confidence='low',
        statement='当前更支持解释A' if has_preference else '现有文字不能区分解释A和B',
        rationale='解释A较符合时间关系' if has_preference else '缺少区分A和B的病程记录')
    problem['candidates'] = [dict(
        candidate_id=f'C{index + 1}', diagnosis='解释' + name,
        position=('preferred' if index == 0 else 'alternative') if has_preference else 'unresolved',
        confidence='low', rationale=problem['rationale'], source_refs=problem['source_refs'],
        limitations=['具体病因未证实'],
    ) for index, name in enumerate(('A', 'B'))]
    payload['dissent'] = ['某专科保留不同倾向，主持人维持上述工作判断。']
    from test_diagnostic_resolution import exploration as make_exploration
    from src.agents.mdt_chair.models import CaseEvidenceCitation
    bundle.evidence_registry['E1'] = CaseEvidenceCitation(evidence_ref='E1', graph_unit_id='g1', evidence_ids=['ev1'], quote='合成的病程线索。')
    explored = make_exploration()
    explored['candidates'] = [{**explored['candidates'][0], 'candidate_id': c['candidate_id'], 'diagnosis': c['diagnosis']} for c in problem['candidates']]
    payload['diagnostic_resolution'].update(primary_candidate_id='C1' if has_preference else None, primary_diagnosis='', candidate_dispositions=[dict(candidate_id=c['candidate_id'], outcome='preferred' if c['position'] == 'preferred' else 'retained', rationale=problem['rationale']) for c in problem['candidates']])
    result = run_expert(bundle, [payload], explored)
    result.team_synthesis.stop_kind = 'completed'
    report, _ = project_team_report('100-IPF', result.model_dump(mode='json'), [],
        '现有资料讨论完成', None, state)
    candidates = report.team_synthesis.problems[0].candidates
    assert [c.diagnosis for c in candidates] == ['解释A', '解释B']
    assert sum(c.position == 'preferred' for c in candidates) == int(has_preference)
    assert all(c.confidence == 'low' for c in candidates)
    assert report.team_synthesis.acceptance == 'explicit_dissent'
    assert report.team_synthesis.problems == result.team_synthesis.problems
    assert state.model_dump(mode='json') == original_state


def test_requests_must_change_decision_and_hypotheses_are_separate():
    with pytest.raises(ValueError,match='actual decisions'):
        KeyEvidenceNeed(need_id='N1', status='missing', available_information='', missing_information='病理', action='obtain_new', problem_id='P1',information='病理',source_refs=['S1'],
            branches=[dict(result=r,changed_decision='提高把握') for r in ('阳性','阴性')],
            feasibility_and_burden='有创',if_unavailable='有限结论')
    _,_,payload=setup_case()
    payload['conditional_contributions']=[dict(question='是否需要病理区分',possible_result='特异形态',
        decision_if_found='改变病因归属',cannot_establish='非特异损伤不能定病因',acquisition_value='当前收益有限，不追索')]
    team=TeamSynthesis.model_validate(payload)
    assert not team.evidence_needs
    assert not team.problems[0].evidence_refs


@pytest.mark.parametrize('disposition', ['adopt', 'qualify', 'defer', 'reject'])
def test_specialty_conditional_contribution_requires_traceable_disposition(disposition):
    state, _, payload = setup_case()
    contribution = dict(question='核对既有材料能否区分病因？', possible_result='提示病因A的特异所见',
        decision_if_found='调整病因排序', cannot_establish='非特异所见不能确认病因A',
        acquisition_value='先核对已有报告，不自动要求新增取材')
    state.specialty_context['pathology']['conditional_contributions'] = [contribution]
    outputs = project_active_specialty_outputs(state)
    bundle = build_chair_prompt_bundle('100-IPF', outputs)
    item = bundle.prompt_input['specialties'][-1]['conditional_contributions'][0]
    ref = item['source_ref']
    assert bundle.source_registry[ref].source_type == 'conditional_contribution'
    assert bundle.source_registry[ref].source_path == 'specialty_assessments.conditional_contributions[0]'
    assert bundle.source_metadata[ref] == contribution
    assert not any(bundle.source_evidence[ref].values())
    with pytest.raises(StructuredGenerationError, match=ref):
        run_expert(bundle, [payload])
    payload['judgment_reviews'].append(dict(source_ref=ref, disposition=disposition,
        rationale='保留可改变排序的诊断问题' if disposition in {'adopt', 'qualify'} else '当前材料不可得或不足以改变决定',
        evidence_refs=[], required_response=False))
    if disposition in {'adopt', 'qualify'}:
        with pytest.raises(StructuredGenerationError, match='Retain adopted or qualified'):
            run_expert(bundle, [payload])
    if disposition == 'adopt':
        payload['evidence_needs'] = [dict(need_id='N1', problem_id='P1', information=contribution['question'],
            source_refs=[ref], status='missing', available_information='', missing_information='已有报告内容',
            action='retain_boundary', branches=[],
            feasibility_and_burden='先核对已有材料；是否可得待核实', if_unavailable='维持当前倾向及病因边界')]
    elif disposition == 'qualify':
        payload['judgment_boundaries'][0]['source_refs'].append(ref)
        payload['judgment_boundaries'][0]['undetermined'] = '尚无材料区分病因A，不能把假设所见当作患者事实'
    result = run_expert(bundle, [payload])
    assert result.team_synthesis.source_catalog[ref]['question'] == contribution['question']
    assert result.team_synthesis.judgment_reviews[-1].disposition == disposition
    assert not result.team_synthesis.issues
    assert not result.team_synthesis.problems[0].evidence_refs
    assert bool(result.team_synthesis.evidence_needs) == (disposition == 'adopt')
    assert all(n.action not in {'retrieve_existing', 'obtain_new'} for n in result.team_synthesis.evidence_needs)
    result.team_synthesis.stop_kind = 'completed'
    report, trace = project_team_report('100-IPF', result.model_dump(mode='json'), [], '完成', None, state)
    assert trace['model_calls'] == 0
    assert report.team_synthesis.judgment_reviews == result.team_synthesis.judgment_reviews

    # Even if later projection omits the contribution, its source and disposition remain reviewable.
    outputs['pathology']['specialty_assessments']['conditional_contributions'] = []
    later = build_chair_prompt_bundle('100-IPF', outputs, discussion_round=1, source_seed=bundle)
    llm = Responses([payload])
    agent = MDTChairAgent(llm, prompt_path='src/prompts/mdt_chair/expert_synthesis.md', max_attempts=1)
    updated, _ = agent.integrate(later, discussion_previous=result)
    assert updated.team_synthesis.judgment_reviews[-1].source_ref == ref
    assert contribution['question'] in updated.team_synthesis.source_catalog[ref]['quote']
    missing = deepcopy(payload)
    missing['judgment_reviews'].pop()
    with pytest.raises(StructuredGenerationError, match=ref):
        run_expert(later, [missing])


@pytest.mark.parametrize(("outcome", "keep_open", "message"), [
    ("bounded", True, "Issue I3 has outcome='bounded' but remains in issues"),
    ("continue", False, "Continuing issue must retain its ID: I3"),
])
def test_issue_disposition_error_identifies_the_conflicting_direction(outcome, keep_open, message):
    _, bundle, payload = setup_case()
    payload['issues'] = [dict(
        issue_id='I3', origin='chair', kind='clarification', question='说明当前判断边界',
        target_specialties=['pulmonology'], assignments=[dict(specialty='pulmonology', question='说明判断依据和边界')], source_refs=[payload['judgment_reviews'][0]['source_ref']],
        decision_impact='限定当前归因', closure_criterion='回应现有资料的边界',
    )]
    previous = run_expert(bundle, [payload])
    candidate = deepcopy(payload)
    if not keep_open:
        candidate['issues'] = []
    candidate['issue_dispositions'] = [dict(issue_id='I3', outcome=outcome, rationale='复核后处理', linked_issue_ids=['I3'] if keep_open else [])]
    agent = MDTChairAgent(
        Responses([candidate]), 
        prompt_path='src/prompts/mdt_chair/expert_synthesis.md', max_attempts=1,
    )
    with pytest.raises(StructuredGenerationError, match=message):
        agent.integrate(bundle, discussion_previous=previous)


def test_task_can_reread_previously_unselected_patient_text():
    _,bundle,payload=setup_case()
    payload['issues']=[dict(issue_id='Q1',origin='chair',kind='omission',question='解释遗漏的急性变化',
        raised_by=[],target_specialties=['pulmonology'], assignments=[dict(specialty='pulmonology', question='说明判断依据和边界')],affected_specialties=['rheumatology'],
        source_refs=[payload['judgment_reviews'][0]['source_ref']],evidence_refs=[],
        decision_impact='改变主要解释',closure_criterion='回应急性变化')]
    result=run_expert(bundle,[payload])
    units=[dict(graph_unit_id=f'U{i}',evidence_blocks=[dict(evidence_id=f'E{i}',text=text)],propositions=[])
           for i,text in enumerate(('慢性咳嗽','近期发热'),1)]
    tasks=build_discussion_tasks(chair_result=result.model_dump(mode='json'),
        clinical_propositions={'segments':[{'segment_id':'s1','units':units}]},
        local_graphs={'segments':[]},round_number=1,previous_rounds=[])
    assert [e.quote for e in tasks[0].evidence_candidates] == ['慢性咳嗽','近期发热']
    assert tasks[0].affected_specialties == ['rheumatology']


def test_remote_disconnect_is_retryable_and_atomic_write_preserves_prior_snapshot(tmp_path):
    import http.client
    from src.llm.structured import _is_retryable_transport_error
    from src.utils.persistence import write_json
    try:
        raise RuntimeError('APIYI connection interrupted') from http.client.RemoteDisconnected()
    except RuntimeError as error:
        assert _is_retryable_transport_error(error)
    path=tmp_path/'state.json'
    write_json(path,{'revision':1})
    circular=[]; circular.append(circular)
    with pytest.raises(ValueError):
        write_json(path,circular)
    assert json.loads(path.read_text()) == {'revision':1}
    assert len(list(tmp_path.iterdir())) == 1


def test_catalog_recognizes_current_expert_snapshot(tmp_path):
    from src.workbench.catalog import RunCatalog
    _,bundle,payload=setup_case()
    result=run_expert(bundle,[payload]).model_dump(mode='json')
    assert RunCatalog._is_current_chair_output(result)
    incomplete=deepcopy(result); incomplete['team_synthesis']=None
    assert not RunCatalog._is_current_chair_output(incomplete)


def test_original_questions_and_needs_survive_later_round_filtering():
    state, _, payload = setup_case()
    outputs = project_active_specialty_outputs(state)
    outputs['pulmonology']['interspecialty_questions']['questions'] = [dict(
        question='文字资料能确定影像模式吗？', target_specialty='thoracic_radiology',
        why_it_matters='判断模式', decision_unlocked='影像分型')]
    outputs['pulmonology']['specialty_assessments']['evidence_gaps'] = [dict(
        missing_information='病变分布文字记录', available_information='文字报告',
        why_it_matters='确认模式', decision_unlocked='影像分型')]
    bundle = build_chair_prompt_bundle('100-IPF', outputs)
    question = next(iter(bundle.question_refs_to_classify))
    need_ref = next(iter(bundle.evidence_need_refs_to_classify))
    # Adding original requests changes registry numbering; use this bundle's active sources.
    refs = [s['specialty_assessments'][0]['source_ref'] for s in bundle.prompt_input['specialties']]
    for review, ref in zip(payload['judgment_reviews'], refs):
        review['source_ref'] = ref
    payload['problems'][0]['source_refs'] = refs
    payload['judgment_boundaries'][0]['source_refs'] = refs
    target = next(ref for ref in refs if bundle.source_registry[ref].specialty == 'thoracic_radiology')
    payload['issue_dispositions'] = [dict(issue_id=question, outcome='covered',
        rationale='影像科已明确说明判断边界', source_refs=[target])]
    payload['evidence_needs'] = [dict(need_id='N1', problem_id='P1', information='病变分布文字记录',
        source_refs=[need_ref], status='missing', available_information='文字报告',
        missing_information='病变分布文字记录', action='retain_boundary', branches=[],
        feasibility_and_burden='本轮不可得', if_unavailable='保留模式判断边界')]
    previous = run_expert(bundle, [payload])
    later = build_chair_prompt_bundle('100-IPF', outputs, discussion_round=1, source_seed=bundle)
    assert not later.question_refs_to_classify and not later.evidence_need_refs_to_classify
    assert not any(s['interspecialty_questions'] for s in later.prompt_input['specialties'])
    agent = MDTChairAgent(Responses([payload]), 
        prompt_path='src/prompts/mdt_chair/expert_synthesis.md', max_attempts=1)
    result, _ = agent.integrate(later, discussion_previous=previous)
    assert result.team_synthesis.issue_dispositions[0].issue_id == question
    assert result.team_synthesis.source_catalog[question]['target_specialty'] == 'thoracic_radiology'
    assert result.team_synthesis.evidence_needs[0].need_id == 'N1'
    assert result.team_synthesis.evidence_needs[0].status == 'missing'
    missing = deepcopy(payload)
    missing['evidence_needs'] = []
    with pytest.raises(StructuredGenerationError, match='Every specialty evidence need'):
        run_expert(later, [missing])
    missing = deepcopy(payload)
    missing['issue_dispositions'][0]['source_refs'] = [next(r for r in refs if r != target)]
    with pytest.raises(StructuredGenerationError, match="target specialty's actual judgment"):
        run_expert(later, [missing])
    payload['issues'] = [dict(issue_id='open', origin='specialty', kind='clarification',
        question='明确影像判断边界', raised_by=['rheumatology'],
        target_specialties=['thoracic_radiology'],
        assignments=[dict(specialty='thoracic_radiology', question='说明影像判断边界')],
        source_refs=[question], decision_impact='影像分型', closure_criterion='说明依据')]
    with pytest.raises(StructuredGenerationError) as failure:
        run_expert(bundle, [payload])
    error = failure.value.attempts[0]['validation_error']
    assert '/issues/0/raised_by' in error
    assert "allowed requesters=['pulmonology']" in error
    payload['issues'][0]['raised_by'] = ['pulmonology']
    assert run_expert(bundle, [payload]).team_synthesis.issues[0].raised_by == ['pulmonology']
    direct = deepcopy(payload)
    direct['issues'][0].update(issue_id=question, source_refs=[target])
    assert question in run_expert(bundle, [direct]).team_synthesis.issues[0].source_refs
    direct['issue_dispositions'][0].update(outcome='continue', linked_issue_ids=[])
    direct_result = run_expert(bundle, [direct])
    assert direct_result.team_synthesis.issue_dispositions[0].linked_issue_ids == [question]
    direct['issue_dispositions'][0]['outcome'] = 'covered'
    direct['issues'][0]['raised_by'] = ['rheumatology']
    with pytest.raises(StructuredGenerationError, match='requesters must match'):
        run_expert(bundle, [direct])
    payload['issue_dispositions'][0].update(outcome='merged', linked_issue_ids=['open'])
    payload['issues'][0]['source_refs'] = [target]
    assert question in run_expert(bundle, [payload]).team_synthesis.issues[0].source_refs
    payload['issues'][0]['target_specialties'] = ['pathology']
    payload['issues'][0]['assignments'][0]['specialty'] = 'pathology'
    with pytest.raises(StructuredGenerationError, match='must preserve its original question source'):
        run_expert(bundle, [payload])


def test_new_schema_rejects_self_answer_and_legacy_chair_fields():
    from src.agents.common.team_synthesis import ClinicalIssue, MDTChairResult
    issue = dict(issue_id='I1', origin='specialty', kind='clarification', question='说明依据',
        raised_by=['pulmonology'], target_specialties=['pulmonology'],
        assignments=[dict(specialty='pulmonology', question='说明依据')], source_refs=['S1'],
        decision_impact='明确判断', closure_criterion='给出依据')
    with pytest.raises(ValueError, match='cannot answer its own question'):
        ClinicalIssue.model_validate(issue)
    issue['origin'] = 'chair'
    with pytest.raises(ValueError, match='leave raised_by empty'):
        ClinicalIssue.model_validate(issue)
    issue['raised_by'] = []
    ClinicalIssue.model_validate(issue)
    _, bundle, payload = setup_case()
    result = run_expert(bundle, [payload])
    with pytest.raises(ValueError, match='Extra inputs'):
        MDTChairResult.model_validate({**result.model_dump(), 'questions': []})
    payload['judgment_boundaries'] = []
    with pytest.raises(StructuredGenerationError, match='judgment boundary'):
        run_expert(bundle, [payload])


def test_facet_confidence_errors_identify_every_invalid_array_item():
    from pydantic import ValidationError
    _, _, payload = setup_case()
    payload['diagnostic_facets'] = [dict(
        dimension=dimension, problem_id='P1', statement='当前资料不足',
        status=status, confidence='low', rationale='现有资料限制', source_refs=['S001'],
    ) for dimension, status in (
        ('radiologic_pattern', 'not_assessable'),
        ('histopathologic_pattern', 'not_applicable'),
    )]
    with pytest.raises(ValidationError) as failure:
        TeamSynthesis.model_validate(payload)
    assert [error['loc'] for error in failure.value.errors()] == [
        ('diagnostic_facets', 0), ('diagnostic_facets', 1),
    ]
    for facet, confidence in zip(payload['diagnostic_facets'], ('unknown', 'not_applicable')):
        facet['confidence'] = confidence
    assert TeamSynthesis.model_validate(payload).diagnostic_facets[0].confidence == 'unknown'


def test_chair_reports_missing_reviews_needs_and_boundaries_together():
    state, _, payload = setup_case()
    outputs = project_active_specialty_outputs(state)
    outputs['pulmonology']['specialty_assessments']['evidence_gaps'] = [dict(
        missing_information='病原结果', available_information='症状记录',
        why_it_matters='确定病原', decision_unlocked='病因归属')]
    bundle = build_chair_prompt_bundle('100-IPF', outputs)
    refs = [s['specialty_assessments'][0]['source_ref'] for s in bundle.prompt_input['specialties']]
    for review, ref in zip(payload['judgment_reviews'], refs):
        review['source_ref'] = ref
    payload['problems'][0]['source_refs'] = refs
    payload['judgment_reviews'].pop()
    payload['judgment_boundaries'] = []
    with pytest.raises(StructuredGenerationError) as failure:
        run_expert(bundle, [payload])
    error = failure.value.attempts[0]['validation_error']
    assert 'Review every active formal judgment' in error
    assert 'Every specialty evidence need' in error
    assert next(iter(bundle.evidence_need_refs_to_classify)) in error
    assert 'judgment boundary for problem P1' in error


def test_chair_reports_all_dispositions_without_open_routes_together():
    _, bundle, payload = setup_case()
    missing_review = payload['judgment_reviews'].pop()
    payload['issue_dispositions'] = [dict(issue_id=ref, outcome=outcome,
        rationale='需要进一步讨论', linked_issue_ids=[])
        for ref, outcome in [('request1', 'continue'), ('request2', 'merged')]]
    with pytest.raises(StructuredGenerationError) as failure:
        run_expert(bundle, [payload])
    error = failure.value.attempts[0]['validation_error']
    assert 'request1' in error and 'request2' in error
    assert 'Review every active formal judgment' in error
    payload['judgment_reviews'].append(missing_review)
    assert '/issue_dispositions/0/linked_issue_ids' in error
    assert '/issue_dispositions/1/linked_issue_ids' in error
    payload['issues'] = [dict(issue_id='open', origin='chair', kind='clarification',
        question='说明判断边界', target_specialties=['pulmonology'],
        assignments=[dict(specialty='pulmonology', question='说明判断边界')],
        source_refs=[payload['judgment_reviews'][0]['source_ref']],
        decision_impact='明确归因边界', closure_criterion='给出解释')]
    for disposition in payload['issue_dispositions']:
        disposition['linked_issue_ids'] = ['open']
    previous = run_expert(bundle, [payload])
    assert previous.team_synthesis.issues[0].issue_id == 'open'
    payload['issue_dispositions'].append(dict(issue_id='open', outcome='continue',
        rationale='继续处理同一议题', linked_issue_ids=[]))
    agent = MDTChairAgent(Responses([payload]),
        prompt_path='src/prompts/mdt_chair/expert_synthesis.md', max_attempts=1)
    result, _ = agent.integrate(bundle, discussion_previous=previous)
    assert result.team_synthesis.issue_dispositions[-1].linked_issue_ids == ['open']


def test_chair_reports_all_missing_requests_with_other_coverage_errors():
    state, _, payload = setup_case()
    outputs = project_active_specialty_outputs(state)
    outputs['pulmonology']['interspecialty_questions']['questions'] = [dict(
        question=question, target_specialty='thoracic_radiology',
        why_it_matters='区分病因', decision_unlocked='诊断排序')
        for question in ('分布支持哪种模式？', '是否有小气道异常？', '是否存在急性变化？')]
    outputs['pulmonology']['specialty_assessments']['evidence_gaps'] = [dict(
        missing_information='影像描述', available_information='症状',
        why_it_matters='判断模式', decision_unlocked='病因归属')]
    bundle = build_chair_prompt_bundle('100-IPF', outputs)
    refs = [s['specialty_assessments'][0]['source_ref'] for s in bundle.prompt_input['specialties']]
    for review, ref in zip(payload['judgment_reviews'], refs):
        review['source_ref'] = ref
    payload['problems'][0]['source_refs'] = refs
    payload['judgment_boundaries'][0]['source_refs'] = refs
    questions = sorted(bundle.question_refs_to_classify)
    target = next(r for r in refs if bundle.source_registry[r].specialty == 'thoracic_radiology')
    existing = dict(issue_id=questions[0], outcome='covered', rationale='已提供专业意见', source_refs=[target])
    payload['issue_dispositions'] = [existing]
    with pytest.raises(StructuredGenerationError) as failure:
        run_expert(bundle, [payload])
    error = failure.value.attempts[0]['validation_error']
    assert 'Every specialty evidence need' in error
    assert f'Missing disposition for specialty requests: {questions[1:]}' in error
    assert 'append' in error and 'do not rename' in error
    assert payload['issue_dispositions'] == [existing]
