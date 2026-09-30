"""Pulmonology-specific validation rules."""

from pydantic import BaseModel

from src.agents.common.validation import case_units, require_specialty_input, resolve_evidence_pointers, validate_authorized_items, validate_pointers
from src.agents.pulmonology.models import EvidencePointer, PulmonologyClinicalState, PulmonologyInitialAssessment
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
    del clinical_rules
    _validate_clinical_state(result, case_input, {})
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
    del clinical_rules
    return result








def require_pulmonology_input(case_input: SpecialtyCaseInput) -> None:
    require_specialty_input(case_input, MdtSpecialty.PULMONOLOGY, "PulmonologyAgent")




def _resolve_evidence_pointers(value: object, case_input: SpecialtyCaseInput) -> None:
    resolve_evidence_pointers(value, case_input, EvidencePointer)


def _validate_clinical_state(
    state: PulmonologyClinicalState,
    case_input: SpecialtyCaseInput,
    opinions: dict,
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
        validate_pointers(question.related_evidence, units)
    for observation in state.reference_observations:
        validate_pointers(observation.related_evidence, units)
def _validate_clinical_items(items, case_input, opinions) -> None:
    validate_authorized_items(
        items,
        case_input,
        opinions,
        _pulmonology_authorization_error,
    )








def _pulmonology_authorization_error(unit, pointer) -> str:
    return (
        f"{unit.evidence_role} 证据 {pointer.evidence_ids} 不能直接支持呼吸科诊断性判断；"
        "如无精确引用相同 evidence ID 的正式专科 claim，请将其放入 "
        "related_evidence，用于病例理解、局限性说明或等待专科确认"
    )
