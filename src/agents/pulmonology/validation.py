"""Pulmonology-specific validation rules."""

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
from src.agents.pulmonology.models import (
    DiscussionConsultOutput,
    DiscussionEvidenceMap,
    DiscussionStateUpdate,
    EvidencePointer,
    PulmonologyClinicalState,
    PulmonologyDiscussionInput,
    PulmonologyDiscussionResponse,
    PulmonologyInitialAssessment,
)
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import SpecialtyCaseInput


def validate_initial_assessment(
    result: PulmonologyInitialAssessment,
    case_input: SpecialtyCaseInput,
    clinical_rules: dict | None = None,
) -> PulmonologyInitialAssessment:
    if result.case_id and result.case_id != case_input.case_id:
        raise ValueError(f"Assessment case_id {result.case_id} does not match {case_input.case_id}")
    result.case_id = case_input.case_id
    _resolve_evidence_pointers(result, case_input)
    _validate_clinical_state(result, case_input, {}, clinical_rules)
    return result


def validate_discussion_response(
    result: PulmonologyDiscussionResponse,
    discussion_input: PulmonologyDiscussionInput,
    clinical_rules: dict | None = None,
) -> PulmonologyDiscussionResponse:
    case_input = discussion_input.case_input
    if result.case_id and result.case_id != case_input.case_id:
        raise ValueError(f"Discussion case_id {result.case_id} does not match {case_input.case_id}")
    result.case_id = case_input.case_id
    _resolve_evidence_pointers(result, case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    validate_used_opinions(result.specialist_opinions_used, opinions)
    _validate_clinical_state(result.updated_state, case_input, opinions, clinical_rules)
    _validate_domain_changes(result.domain_changes, case_input, opinions)
    _validate_mapped_findings(result.mapped_findings, case_input, opinions)
    _validate_clinical_items(result.unresolved_conflicts, case_input, opinions)
    _validate_chair_answers(result.chair_answers, discussion_input, opinions)
    return result


def validate_initial_stage(
    result: BaseModel,
    case_input: SpecialtyCaseInput,
    clinical_rules: dict | None = None,
):
    _resolve_evidence_pointers(result, case_input)
    units = case_units(case_input)
    diagnostic_items: list[object] = []
    for field in (
        "clinical_phenotype",
        "pulmonary_severity",
        "bronchoscopy_assessment",
        "diagnostic_formulation",
    ):
        item = getattr(result, field, None)
        if item is not None:
            diagnostic_items.append(item)
    diagnostic_items.extend(getattr(result, "secondary_cause_assessment", []))
    diagnostic_items.extend(getattr(result, "respiratory_test_interpretation", []))
    progression = getattr(result, "progression_assessment", None)
    if progression:
        diagnostic_items.extend(progression.components)
    formulation = getattr(result, "diagnostic_formulation", None)
    if formulation:
        if formulation.morphologic_pattern:
            diagnostic_items.append(formulation.morphologic_pattern)
        diagnostic_items.extend(formulation.differential_diagnoses)
    _validate_clinical_items(diagnostic_items, case_input, {})
    for gap in getattr(result, "missing_data", []):
        validate_pointers(gap.related_evidence, units)
    for question in getattr(result, "specialist_dependencies", []):
        validate_pointers(question.related_evidence, units)
    for observation in getattr(result, "reference_observations", []):
        validate_pointers(observation.related_evidence, units)
    _validate_ppf_rule(progression, clinical_rules)
    for question in getattr(result, "specialist_dependencies", []):
        if question.specialty == MdtSpecialty.SHARED_CONTEXT:
            raise ValueError("A specialist question cannot target shared_context")
    return result


def validate_evidence_map(
    result: DiscussionEvidenceMap,
    discussion_input: PulmonologyDiscussionInput,
) -> DiscussionEvidenceMap:
    _resolve_evidence_pointers(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    validate_used_opinions(result.specialist_opinions_used, opinions)
    _validate_mapped_findings(result.mapped_findings, discussion_input.case_input, opinions)
    _validate_clinical_items(result.unresolved_conflicts, discussion_input.case_input, opinions)
    return result


def validate_state_update(
    result: DiscussionStateUpdate,
    discussion_input: PulmonologyDiscussionInput,
    clinical_rules: dict | None,
) -> DiscussionStateUpdate:
    _resolve_evidence_pointers(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _validate_clinical_state(
        result.updated_state,
        discussion_input.case_input,
        opinions,
        clinical_rules,
    )
    _validate_domain_changes(result.domain_changes, discussion_input.case_input, opinions)
    return result


def validate_consult_output(
    result: DiscussionConsultOutput,
    discussion_input: PulmonologyDiscussionInput,
) -> DiscussionConsultOutput:
    _resolve_evidence_pointers(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _validate_chair_answers(result.chair_answers, discussion_input, opinions)
    _validate_clinical_items(result.unresolved_conflicts, discussion_input.case_input, opinions)
    return result


def require_pulmonology_input(case_input: SpecialtyCaseInput) -> None:
    require_specialty_input(case_input, MdtSpecialty.PULMONOLOGY, "PulmonologyAgent")


def validate_specialist_opinions(discussion_input: PulmonologyDiscussionInput) -> None:
    validate_common_specialist_opinions(discussion_input, EvidencePointer)


def _resolve_evidence_pointers(value: object, case_input: SpecialtyCaseInput) -> None:
    resolve_evidence_pointers(value, case_input, EvidencePointer)


def _validate_clinical_state(
    state: PulmonologyClinicalState,
    case_input: SpecialtyCaseInput,
    opinions: dict,
    clinical_rules: dict | None,
) -> None:
    state.case_id = case_input.case_id
    items: list[object] = [
        state.clinical_phenotype,
        *state.secondary_cause_assessment,
        state.pulmonary_severity,
        *state.respiratory_test_interpretation,
        state.bronchoscopy_assessment,
    ]
    if state.progression_assessment:
        items.extend(state.progression_assessment.components)
    if state.diagnostic_formulation:
        items.extend(
            [
                state.diagnostic_formulation,
                state.diagnostic_formulation.morphologic_pattern,
                *state.diagnostic_formulation.differential_diagnoses,
            ]
        )
    _validate_clinical_items([item for item in items if item is not None], case_input, opinions)
    units = case_units(case_input)
    for gap in state.missing_data:
        validate_pointers(gap.related_evidence, units)
    for question in state.specialist_dependencies:
        if question.specialty == MdtSpecialty.SHARED_CONTEXT:
            raise ValueError("A specialist question cannot target shared_context")
        validate_pointers(question.related_evidence, units)
    for observation in state.reference_observations:
        validate_pointers(observation.related_evidence, units)
    _validate_ppf_rule(state.progression_assessment, clinical_rules)


def _validate_clinical_items(items, case_input, opinions) -> None:
    units = case_units(case_input)
    for item in items:
        pointers = list(getattr(item, "supporting_evidence", []))
        pointers.extend(getattr(item, "conflicting_evidence", []))
        validate_authorized_pointers(
            pointers,
            getattr(item, "specialist_opinion_ids", []),
            units,
            opinions,
            _pulmonology_authorization_error,
        )
        validate_pointers(getattr(item, "related_evidence", []), units)


def _validate_domain_changes(changes, case_input, opinions) -> None:
    units = case_units(case_input)
    for change in changes:
        validate_authorized_pointers(
            change.supporting_evidence,
            change.specialist_opinion_ids,
            units,
            opinions,
            _pulmonology_authorization_error,
        )


def _validate_mapped_findings(findings, case_input, opinions) -> None:
    units = case_units(case_input)
    for finding in findings:
        if finding.opinion_id not in opinions:
            raise ValueError(f"Unknown mapped opinion_id: {finding.opinion_id}")
        validate_authorized_pointers(
            finding.evidence,
            [finding.opinion_id],
            units,
            opinions,
            _pulmonology_authorization_error,
        )


def _validate_chair_answers(answers, discussion_input, opinions) -> None:
    validate_chair_question_order(answers, discussion_input.chair_questions)
    _validate_clinical_items(answers, discussion_input.case_input, opinions)


def _validate_ppf_rule(progression, clinical_rules: dict | None) -> None:
    if not progression or not clinical_rules:
        return
    configured = (clinical_rules.get("ppf") or {}).get("source")
    if configured and progression.rule_source != configured:
        raise ValueError(
            f"PPF rule_source {progression.rule_source!r} does not match configured {configured!r}"
        )


def _pulmonology_authorization_error(unit, pointer) -> str:
    return (
        f"{unit.evidence_role} 证据 {pointer.evidence_ids} 不能直接支持呼吸科诊断性判断；"
        "如无精确引用相同 evidence ID 的正式专科 claim，请将其放入 "
        "related_evidence，用于病例理解、局限性说明或等待专科确认"
    )
