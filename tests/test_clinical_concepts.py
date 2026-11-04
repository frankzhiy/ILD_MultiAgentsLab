"""Separate clinical meaning from certainty, candidate selection and workflow."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.agents.common.decision_state import (
    JudgmentChangeProposal, JudgmentContent, active_judgments,
    prepare_judgment_change, record_discussion_answers, project_active_specialty_outputs,
)
from src.agents.common.diagnostic_resolution import CandidateDisposition, DiagnosticExploration
from src.agents.common.initial_output import SpecialtyAssessment
from src.agents.common.team_synthesis import ChairSynthesisDraft, DiagnosticFacet, TeamSynthesis
from src.agents.mdt_chair.agent import build_chair_prompt_bundle
from src.agents.mdt_chair.prompt_projection import build_chair_prompt_view
from src.agents.mdt_discussion.models import SpecialtyRoundResponse, SpecialtyTaskAnswer
from test_diagnostic_resolution import exploration, integrate
from test_expert_protocol import setup_case


def current_team():
    _, _, team = setup_case()
    team['diagnostic_exploration'] = exploration()
    team['problems'][0]['candidates'] = [dict(candidate_id='C1', position='preferred',
        confidence='low', rationale='现有病程支持。', source_refs=['S001'])]
    team['diagnostic_resolution'].update(primary_diagnosis='', primary_candidate_id='C1',
        candidate_dispositions=[dict(candidate_id='C1', outcome='preferred', rationale='当前首选。')])
    return team


def assessment():
    state, _, _ = setup_case()
    raw = active_judgments(state)[0].assessment.model_dump(mode='json')
    raw.pop('status')
    raw['conditions']['subject'] = '特定疾病'
    return raw


@pytest.mark.parametrize('direction', ['supports', 'insufficient_support', 'against', 'indeterminate'])
def test_judgment_direction_is_not_derived_from_high_confidence(direction):
    item = SpecialtyAssessment.model_validate({**assessment(), 'direction': direction, 'confidence': 'high'})
    assert item.direction == direction and item.confidence == 'high'
    assert 'status' not in item.model_dump(mode='json')


@pytest.mark.parametrize('model', [SpecialtyAssessment, JudgmentContent])
def test_exclusion_requires_explicit_nonbackground_patient_evidence(model):
    raw = assessment()
    if model is JudgmentContent:
        raw.pop('assessment_id')
    raw['direction'] = 'excluded'
    with pytest.raises(ValidationError, match='explicit patient evidence'):
        model.model_validate(raw)
    raw['claims'][0]['evidence_relations'] = [dict(evidence_ids=['ev1'], direction='supports', function='foundational')]
    assert model.model_validate(raw).direction == 'excluded'


def test_negative_answer_keeps_answer_confidence_without_becoming_a_positive_diagnosis():
    state, _, _ = setup_case()
    original = state.model_dump(mode='json')
    answer = SpecialtyTaskAnswer(answer_id='A1', task_id='T1', issue_id='I1', issue_type='question',
        answerability='answered', confidence='high', answer='当前不支持该归因。',
        medical_basis='尚缺特异支持。', changed_from_previous=False)
    response = SpecialtyRoundResponse(case_id=state.case_id, specialty='rheumatology', round_number=1, answers=[answer])
    updated = record_discussion_answers(state, [response], [], round_number=1)
    stored = updated.discussion_answers['rheumatology'][0]
    assert stored['answerability'] == 'answered' and stored['answer_confidence'] == 'high'
    assert not {'status', 'direction', 'confidence', 'clinical_role'} & stored.keys()
    bundle = build_chair_prompt_bundle(state.case_id, project_active_specialty_outputs(updated))
    projected = next(s for s in bundle.prompt_input['specialties'] if s['specialty'] == 'rheumatology')['discussion_answers'][0]
    assert projected['answer_confidence'] == 'high' and 'status' not in projected
    assert state.model_dump(mode='json') == original


def test_new_model_schemas_hide_overlapping_fields_but_preserve_history():
    for model in (SpecialtyAssessment, JudgmentContent, DiagnosticFacet):
        assert 'status' not in model.model_json_schema()['properties']
    facet_schema = DiagnosticFacet.model_json_schema()['properties']
    assert facet_schema['confidence']['enum'] == ['high', 'moderate', 'low', 'unknown']
    assert 'anyOf' not in facet_schema['direction']
    schema = ChairSynthesisDraft.model_json_schema()
    assert 'diagnosis' not in schema['$defs']['DiagnosticCandidate']['properties']
    assert 'primary_diagnosis' not in schema['$defs']['DiagnosticResolution']['properties']
    raw = {**assessment(), 'status': 'supported', 'direction': 'against'}
    assert SpecialtyAssessment.model_validate(raw).model_dump(mode='json')['status'] == 'supported'
    state, bundle, _ = setup_case()
    view = build_chair_prompt_view(bundle)
    assert 'legacy_semantics' in view['chair_input']['specialties'][0]['specialty_assessments'][0]
    assert active_judgments(state)[0].assessment.status == 'possible'


def test_retiring_legacy_summary_does_not_change_the_clinical_judgment():
    state, _, _ = setup_case()
    version = active_judgments(state)[0]
    raw = version.assessment.model_dump(mode='json')
    raw.pop('assessment_id')
    raw.pop('status')
    raw['medical_basis'] += '新增病例依据。'
    proposal = JudgmentChangeProposal(change_type='supplement', target_judgment_id=version.judgment_id,
        base_version_id=version.version_id, rationale='补充依据。', proposed_content=raw)
    item, changed = prepare_judgment_change(version, proposal)
    assert item.status is None and changed == ['medical_basis']
    assert version.assessment.status == 'possible'


def test_candidate_names_and_primary_title_are_resolved_from_one_registry():
    raw = current_team()
    raw['diagnostic_exploration']['candidates'][0]['aliases'] = ['Pneumonia']
    raw['problems'][0]['candidates'][0]['diagnosis'] = 'Pneumonia'
    team = TeamSynthesis.model_validate(raw)
    assert team.problems[0].candidates[0].diagnosis == team.diagnostic_resolution.primary_diagnosis == '肺炎'
    result, _ = integrate(exploration())
    assert result.team_synthesis.diagnostic_resolution.primary_diagnosis == '肺炎'


@pytest.mark.parametrize('conflict', ['duplicate', 'rejected_preferred', 'different_name', 'different_title', 'wrong_primary_id', 'unregistered'])
def test_conflicting_current_candidate_representations_are_rejected(conflict):
    raw = current_team()
    candidate = raw['problems'][0]['candidates'][0]
    if conflict == 'duplicate':
        raw['problems'][0]['candidates'].append({**candidate, 'position': 'alternative'})
    elif conflict == 'rejected_preferred':
        raw['diagnostic_resolution']['candidate_dispositions'][0].update(outcome='rejected', nonselection_reason='insufficient_support')
    elif conflict == 'different_name':
        candidate['diagnosis'] = '另一种疾病'
    elif conflict == 'different_title':
        raw['diagnostic_resolution']['primary_diagnosis'] = '另一种疾病'
    elif conflict == 'wrong_primary_id':
        raw['diagnostic_resolution']['primary_candidate_id'] = 'other'
    else:
        candidate['candidate_id'] = 'unregistered'
    with pytest.raises(ValidationError):
        TeamSynthesis.model_validate(raw)


def test_same_concept_or_declared_alias_cannot_have_two_ids():
    raw = exploration()
    raw['candidates'].append({**raw['candidates'][0], 'candidate_id': 'C2', 'diagnosis': ' 肺炎 '})
    with pytest.raises(ValidationError, match='multiple candidate IDs'):
        DiagnosticExploration.model_validate(raw)
    raw['candidates'][1].update(diagnosis='Pneumonia', aliases=['肺炎'])
    with pytest.raises(ValidationError, match='multiple candidate IDs'):
        DiagnosticExploration.model_validate(raw)


@pytest.mark.parametrize('change', ['drop', 'rename', 'change_level', 'drop_alias'])
def test_candidate_identity_cannot_disappear_or_be_reused_in_later_rounds(change):
    raw = exploration()
    raw['candidates'][0]['aliases'] = ['Pneumonia']
    previous = DiagnosticExploration.model_validate(raw)
    current = previous.model_copy(deep=True)
    if change == 'drop':
        current.candidates = []
    elif change == 'rename':
        current.candidates[0].diagnosis = '另一种疾病'
    elif change == 'change_level':
        current.candidates[0].level = 'pattern'
    else:
        current.candidates[0].aliases = []
    with pytest.raises(ValueError):
        current.validate_successor(previous)
    assert previous.candidates[0].diagnosis == '肺炎'


def test_nonretention_is_not_exclusion_and_exclusion_requires_evidence():
    item = CandidateDisposition(candidate_id='C1', outcome='rejected', rationale='正向支持不足。', nonselection_reason='insufficient_support')
    assert item.exclusion_evidence_refs == []
    with pytest.raises(ValidationError, match='explicit patient evidence'):
        CandidateDisposition(**{**item.model_dump(), 'nonselection_reason': 'excluded'})
    assert CandidateDisposition(**{**item.model_dump(), 'nonselection_reason': 'excluded', 'exclusion_evidence_refs': ['E1']}).nonselection_reason == 'excluded'


@pytest.mark.parametrize('missing', ['subject', 'assessability', 'direction'])
def test_current_facets_require_a_neutral_target_and_separate_dimensions(missing):
    from src.llm.structured import StructuredGenerationError
    def facet(team):
        team['diagnostic_facets'] = [dict(problem_id='P1', dimension='histopathologic_pattern',
            subject='组织学UIP模式', assessability='not_assessable', direction='indeterminate', confidence='unknown',
            statement='当前不能判断组织学UIP模式。', rationale='没有可评价切片。', source_refs=['S001'])]
    result, _ = integrate(exploration(), facet)
    item = result.team_synthesis.diagnostic_facets[0]
    assert item.subject == '组织学UIP模式' and item.status is None
    def missing_dimensions(team):
        facet(team)
        team['diagnostic_facets'][0].pop(missing)
    with pytest.raises(StructuredGenerationError, match='neutral subject and separate|indeterminate direction'):
        integrate(exploration(), missing_dimensions)


def test_current_nonselection_requires_a_reason_without_silently_assuming_exclusion():
    from src.llm.structured import StructuredGenerationError
    def reject(team):
        team['problems'][0]['candidates'] = []
        team['diagnostic_resolution'].update(primary_candidate_id=None, candidate_dispositions=[
            dict(candidate_id='C1', outcome='rejected', rationale='不保留。')])
    with pytest.raises(StructuredGenerationError, match='non-retention is not exclusion'):
        integrate(exploration(), reject)


def test_historical_candidate_names_are_not_guessed_or_merged():
    raw = current_team()
    raw['problems'][0]['candidates'][0].pop('candidate_id')
    raw['problems'][0]['candidates'][0]['diagnosis'] = '旧版原始名称'
    raw['diagnostic_resolution'].pop('primary_candidate_id')
    raw['diagnostic_resolution']['primary_diagnosis'] = '旧版标题'
    original = deepcopy(raw)
    parsed = TeamSynthesis.model_validate(raw)
    assert parsed.problems[0].candidates[0].diagnosis == '旧版原始名称'
    assert parsed.diagnostic_resolution.primary_diagnosis == '旧版标题'
    assert raw == original
