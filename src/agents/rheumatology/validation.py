"""Rheumatology-specific validation rules."""

from pydantic import BaseModel

from src.agents.common.validation import (
    case_units,
    require_specialty_input,
    resolve_evidence_pointers,
    validate_authorized_pointers,
    validate_chair_question_order,
    validate_pointers,
    validate_specialist_opinions as validate_common_specialist_opinions,
    validate_used_opinions,
)
from src.agents.rheumatology.models import (
    DiscussionConsultOutput,
    DiscussionEvidenceMap,
    DiscussionStateUpdate,
    EvidencePointer,
    RheumatologyClinicalState,
    RheumatologyDiscussionInput,
    RheumatologyDiscussionResponse,
    RheumatologyInitialAssessment,
)
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import SpecialtyCaseInput


def require_rheumatology_input(case_input: SpecialtyCaseInput) -> None:
    require_specialty_input(case_input, MdtSpecialty.RHEUMATOLOGY, "RheumatologyAgent")


def validate_initial_assessment(result: RheumatologyInitialAssessment, case_input: SpecialtyCaseInput, clinical_rules: dict | None = None):
    if result.case_id and result.case_id != case_input.case_id:
        raise ValueError(f"Assessment case_id {result.case_id} does not match {case_input.case_id}")
    result.case_id = case_input.case_id
    _resolve(result, case_input)
    _validate_state(result, case_input, {})
    del clinical_rules
    return result


def validate_discussion_response(result: RheumatologyDiscussionResponse, discussion_input: RheumatologyDiscussionInput, clinical_rules: dict | None = None):
    case = discussion_input.case_input
    if result.case_id and result.case_id != case.case_id:
        raise ValueError(f"Discussion case_id {result.case_id} does not match {case.case_id}")
    result.case_id = case.case_id
    _resolve(result, case)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    validate_used_opinions(result.specialist_opinions_used, opinions)
    _validate_state(result.updated_state, case, opinions)
    _validate_changes(result.domain_changes, case, opinions)
    _validate_mapped_findings(result.mapped_findings, case, opinions)
    _validate_items(result.unresolved_conflicts, case, opinions)
    _validate_answers(result.chair_answers, discussion_input, opinions)
    del clinical_rules
    return result


def validate_initial_stage(result: BaseModel, case_input: SpecialtyCaseInput, clinical_rules: dict | None = None):
    _resolve(result, case_input)
    _validate_stage_items(result, case_input, {})
    del clinical_rules
    return result


def validate_evidence_map(result: DiscussionEvidenceMap, discussion_input: RheumatologyDiscussionInput):
    _resolve(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    validate_used_opinions(result.specialist_opinions_used, opinions)
    _validate_mapped_findings(result.mapped_findings, discussion_input.case_input, opinions)
    _validate_items(result.unresolved_conflicts, discussion_input.case_input, opinions)
    return result


def validate_state_update(result: DiscussionStateUpdate, discussion_input: RheumatologyDiscussionInput, clinical_rules: dict | None = None):
    _resolve(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _validate_state(result.updated_state, discussion_input.case_input, opinions)
    _validate_changes(result.domain_changes, discussion_input.case_input, opinions)
    del clinical_rules
    return result


def validate_consult_output(result: DiscussionConsultOutput, discussion_input: RheumatologyDiscussionInput):
    _resolve(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _validate_answers(result.chair_answers, discussion_input, opinions)
    _validate_items(result.unresolved_conflicts, discussion_input.case_input, opinions)
    return result


def validate_specialist_opinions(discussion_input: RheumatologyDiscussionInput) -> None:
    validate_common_specialist_opinions(discussion_input, EvidencePointer)


def _resolve(value: object, case_input: SpecialtyCaseInput) -> None:
    resolve_evidence_pointers(value, case_input, EvidencePointer)


def _validate_stage_items(result, case_input, opinions) -> None:
    fields = [
        getattr(result, "case_orientation", None),
        getattr(result, "rheumatic_disease_formulation", None),
        getattr(result, "ild_attribution", None),
        getattr(result, "activity_and_risk", None),
        *getattr(result, "autoimmune_manifestations", []),
        *getattr(result, "serologic_findings", []),
    ]
    formulation = getattr(result, "rheumatic_disease_formulation", None)
    if formulation:
        fields.extend(formulation.differential_diagnoses)
    _validate_items([item for item in fields if item is not None], case_input, opinions)
    _validate_context_fields(result, case_input)


def _validate_state(state: RheumatologyClinicalState, case_input: SpecialtyCaseInput, opinions: dict) -> None:
    state.case_id = case_input.case_id
    fields = [
        state.case_orientation,
        state.rheumatic_disease_formulation,
        state.ild_attribution,
        state.activity_and_risk,
        *state.autoimmune_manifestations,
        *state.serologic_findings,
    ]
    if state.rheumatic_disease_formulation:
        fields.extend(state.rheumatic_disease_formulation.differential_diagnoses)
    _validate_items([item for item in fields if item is not None], case_input, opinions)
    _validate_context_fields(state, case_input)


def _validate_context_fields(value, case_input) -> None:
    units = case_units(case_input)
    for gap in getattr(value, "missing_data", []):
        validate_pointers(gap.related_evidence, units)
    for question in getattr(value, "specialist_dependencies", []):
        if question.specialty == MdtSpecialty.SHARED_CONTEXT:
            raise ValueError("A specialist question cannot target shared_context")
        validate_pointers(question.related_evidence, units)
    for observation in getattr(value, "reference_observations", []):
        validate_pointers(observation.related_evidence, units)


def _validate_items(items, case_input, opinions) -> None:
    units = case_units(case_input)
    for item in items:
        pointers = [*getattr(item, "supporting_evidence", []), *getattr(item, "conflicting_evidence", [])]
        validate_authorized_pointers(pointers, getattr(item, "specialist_opinion_ids", []), units, opinions, _authorization_error)
        validate_pointers(getattr(item, "related_evidence", []), units)


def _validate_changes(changes, case_input, opinions) -> None:
    units = case_units(case_input)
    for change in changes:
        validate_authorized_pointers(change.supporting_evidence, change.specialist_opinion_ids, units, opinions, _authorization_error)


def _validate_mapped_findings(findings, case_input, opinions) -> None:
    units = case_units(case_input)
    for finding in findings:
        if finding.opinion_id not in opinions:
            raise ValueError(f"Unknown mapped opinion_id: {finding.opinion_id}")
        validate_authorized_pointers(finding.evidence, [finding.opinion_id], units, opinions, _authorization_error)


def _validate_answers(answers, discussion_input, opinions) -> None:
    validate_chair_question_order(answers, discussion_input.chair_questions)
    _validate_items(answers, discussion_input.case_input, opinions)


def _authorization_error(unit, pointer) -> str:
    return (
        f"{unit.evidence_role} 证据 {pointer.evidence_ids} 不能直接支持风湿科诊断性判断；"
        "未经精确引用相同 evidence ID 的正式专科 claim 授权时，只能用于 related_evidence。"
    )
