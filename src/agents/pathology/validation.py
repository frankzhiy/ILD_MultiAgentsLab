"""Pathology-specific validation built on shared specialty evidence rules."""

from pydantic import BaseModel

from src.agents.common.validation import case_units, require_specialty_input, resolve_evidence_pointers, validate_authorized_items, validate_pointers
from src.agents.pathology.models import EvidencePointer, PathologyClinicalState, PathologyInitialAssessment, InitialConsultFormulation, InitialSpecimenReconstruction
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import SpecialtyCaseInput


_NONASSESSABLE_MATERIAL = {
    "no_pathology_material",
    "pathology_mentioned_without_report",
    "uncertain_availability",
}


def require_pathology_input(case_input: SpecialtyCaseInput) -> None:
    require_specialty_input(case_input, MdtSpecialty.PATHOLOGY, "PathologyAgent")


def validate_initial_assessment(
    result: PathologyInitialAssessment,
    case_input: SpecialtyCaseInput,
    clinical_rules: dict | None = None,
) -> PathologyInitialAssessment:
    if result.case_id and result.case_id != case_input.case_id:
        raise ValueError(f"Assessment case_id {result.case_id} does not match {case_input.case_id}")
    result.case_id = case_input.case_id
    _resolve(result, case_input)
    _validate_state(result, case_input, {})
    del clinical_rules
    return result




def validate_initial_stage(
    result: BaseModel,
    case_input: SpecialtyCaseInput,
    clinical_rules: dict | None = None,
):
    _resolve(result, case_input)
    _validate_stage_items(result, case_input, {})
    _validate_material_consistency(result)
    del clinical_rules
    return result


def validate_material_plan(
    result: InitialConsultFormulation,
    reconstruction: InitialSpecimenReconstruction,
) -> InitialConsultFormulation:
    source = reconstruction.source_assessment
    if source is None or source.material_status not in _NONASSESSABLE_MATERIAL:
        return result
    if (
        result.pathology_formulation is None
        or result.pathology_formulation.classification_status
        != "no_pathology_material"
    ):
        raise ValueError(
            "No assessable pathology material requires no_pathology_material formulation"
        )
    return result










def _resolve(value: object, case_input: SpecialtyCaseInput) -> None:
    resolve_evidence_pointers(value, case_input, EvidencePointer)


def _validate_stage_items(result, case_input, opinions) -> None:
    items = [
        getattr(result, "source_assessment", None),
        getattr(result, "pathology_formulation", None),
        *getattr(result, "specimens", []),
        *getattr(result, "morphologic_features", []),
        *getattr(result, "pattern_assessments", []),
        *getattr(result, "etiologic_associations", []),
        *getattr(result, "ancillary_studies", []),
    ]
    _validate_items([item for item in items if item is not None], case_input, opinions)
    _validate_context_fields(result, case_input)


def _validate_state(state: PathologyClinicalState, case_input, opinions) -> None:
    state.case_id = case_input.case_id
    _validate_stage_items(state, case_input, opinions)
    _validate_material_consistency(state)


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








def _validate_material_consistency(value) -> None:
    source = getattr(value, "source_assessment", None)
    specimens = getattr(value, "specimens", [])
    patterns = getattr(value, "pattern_assessments", [])
    features = getattr(value, "morphologic_features", [])
    associations = getattr(value, "etiologic_associations", [])
    ancillary = getattr(value, "ancillary_studies", [])
    formulation = getattr(value, "pathology_formulation", None)

    no_material = (
        source is not None and source.material_status in _NONASSESSABLE_MATERIAL
    )
    if no_material and (specimens or patterns or features or associations or ancillary):
        raise ValueError(
            "No assessable pathology material cannot produce specimen or morphologic findings"
        )
    if (
        no_material
        and formulation
        and formulation.classification_status != "no_pathology_material"
    ):
        raise ValueError(
            "No assessable pathology material requires no_pathology_material formulation"
        )
    owns_specimens = "specimens" in type(value).model_fields
    if owns_specimens and patterns and not specimens:
        raise ValueError("Histopathologic pattern assessment requires at least one specimen record")

    for pattern in patterns:
        if pattern.status in {"supported", "probable"} and not pattern.supporting_evidence:
            raise ValueError("Supported or probable pathology pattern requires supporting evidence")

    representative = any(
        specimen.adequacy == "adequate"
        and specimen.representativeness in {"representative", "possibly_representative"}
        for specimen in specimens
    )
    if owns_specimens and specimens and not representative:
        overconfident = [
            item.pattern
            for item in patterns
            if item.status in {"supported", "probable"}
            and item.confidence in {"very_high", "high"}
        ]
        if overconfident:
            raise ValueError(
                "High-confidence pathology pattern requires an adequate, representative specimen"
            )


def _authorization_error(unit, pointer) -> str:
    return (
        f"{unit.evidence_role} 证据 {pointer.evidence_ids} 不能直接支持病理科诊断性判断；"
        "未经精确引用相同 evidence ID 的正式专科 claim 授权时，只能用于 related_evidence。"
    )
