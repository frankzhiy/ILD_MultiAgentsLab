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
    def __init__(self, items):
        self.items = iter(items)
        self.calls = 0
    def complete(self, messages, **kwargs):
        self.calls += 1
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
                evidence={'evidence_relations':[]})], boundaries=['有资料限制']),
            'interspecialty_questions': {'questions': []}}
    state = initialize_decision_state('100-IPF', outputs)
    bundle = build_chair_prompt_bundle('100-IPF', project_active_specialty_outputs(state))
    refs = [s['specialty_assessments'][0]['source_ref'] for s in bundle.prompt_input['specialties']]
    team = dict(judgment_reviews=[dict(source_ref=r, disposition='adopt', rationale='原文支持有限判断',
                                     evidence_refs=[], required_response=False) for r in refs],
        problems=[dict(problem_id='P1', title='急性肺部病变', relationship='primary', statement='首先考虑肺炎',
                       assessability='partially_assessable', confidence='moderate', rationale='症状与文字报告符合',
                       source_refs=refs, evidence_refs=[], candidates=[], limitations=['病原未定'])],
        judgment_boundaries=[dict(boundary_id='B1', problem_id='P1', established='支持肺炎',
            undetermined='病原未定', reason='缺乏病原结果', decision_impact='不能确定病原', source_refs=refs)],
        disagreements=[], issues=[], issue_dispositions=[], evidence_needs=[], conditional_contributions=[], dissent=[],
        synthesis_rationale='按同一问题整合', coverage_review='无足够证据预设ILD', guideline_evidence=[])
    return state,bundle,team


def run_expert(bundle, payloads):
    llm = Responses(payloads)
    agent = MDTChairAgent(llm, 
        prompt_path='src/prompts/mdt_chair/expert_synthesis.md', max_attempts=1)
    return agent.integrate(bundle)[0]


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
    assert llm.calls == 1


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
        diagnosis='解释' + name,
        position=('preferred' if index == 0 else 'alternative') if has_preference else 'unresolved',
        confidence='low', rationale=problem['rationale'], source_refs=problem['source_refs'],
        limitations=['具体病因未证实'],
    ) for index, name in enumerate(('A', 'B'))]
    payload['dissent'] = ['某专科保留不同倾向，主持人维持上述工作判断。']
    result = run_expert(bundle, [payload])
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


def test_catalog_recognizes_expert_snapshot_and_presenter_cannot_rewrite_it(tmp_path):
    from src.workbench.catalog import RunCatalog
    from src.workbench.presentation import present
    _,bundle,payload=setup_case()
    result=run_expert(bundle,[payload]).model_dump(mode='json')
    assert RunCatalog._is_current_chair_output(result)
    value=present(tmp_path,tmp_path,'chair',{'result':result})
    assert value['result'] == result
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
    assert run_expert(bundle, [payload]).team_synthesis.issues[0].issue_id == 'open'
