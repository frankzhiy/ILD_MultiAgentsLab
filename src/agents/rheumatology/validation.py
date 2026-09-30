"""Rheumatology-specific validation rules."""

from pydantic import BaseModel

from src.agents.common.validation import case_units, require_specialty_input, resolve_evidence_pointers, validate_authorized_items, validate_pointers
from src.agents.rheumatology.models import EvidencePointer, RheumatologyClinicalState, RheumatologyInitialAssessment
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




def validate_initial_stage(result: BaseModel, case_input: SpecialtyCaseInput, clinical_rules: dict | None = None):
    _resolve(result, case_input)
    _validate_stage_items(result, case_input, {})
    del clinical_rules
    return result










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
        validate_pointers(question.related_evidence, units)
    for observation in getattr(value, "reference_observations", []):
        validate_pointers(observation.related_evidence, units)


def _validate_items(items, case_input, opinions) -> None:
    validate_authorized_items(items, case_input, opinions, _authorization_error)








def _authorization_error(unit, pointer) -> str:
    return (
        f"{unit.evidence_role} 证据 {pointer.evidence_ids} 不能直接支持风湿科诊断性判断；"
        "未经精确引用相同 evidence ID 的正式专科 claim 授权时，只能用于 related_evidence。"
    )
