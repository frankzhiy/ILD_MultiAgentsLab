"""Pathology-specific validation built on shared specialty evidence rules."""

from pydantic import BaseModel

from src.agents.common.validation import (
    case_units,
    require_specialty_input,
    resolve_evidence_pointers,
    validate_authorized_items,
    validate_authorized_pointers,
    validate_chair_question_order,
    validate_pointers,
    validate_specialist_opinions as validate_common_specialist_opinions,
    validate_used_opinions,
)
from src.agents.pathology.models import (
    DiscussionConsultOutput,
    DiscussionEvidenceMap,
    DiscussionStateUpdate,
    EvidencePointer,
    PathologyClinicalState,
    PathologyDiscussionInput,
    PathologyDiscussionResponse,
    PathologyInitialAssessment,
    InitialConsultFormulation,
    InitialSpecimenReconstruction,
)
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


def validate_discussion_response(
    result: PathologyDiscussionResponse,
    discussion_input: PathologyDiscussionInput,
    clinical_rules: dict | None = None,
) -> PathologyDiscussionResponse:
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
    if not result.missing_data:
        raise ValueError(
            "No assessable pathology material requires decision-relevant material needs"
        )
    if not result.specialist_dependencies:
        raise ValueError(
            "No assessable pathology material requires a material-recovery dependency"
        )
    return result


def validate_evidence_map(
    result: DiscussionEvidenceMap,
    discussion_input: PathologyDiscussionInput,
) -> DiscussionEvidenceMap:
    _resolve(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    validate_used_opinions(result.specialist_opinions_used, opinions)
    _validate_mapped_findings(result.mapped_findings, discussion_input.case_input, opinions)
    _validate_items(result.unresolved_conflicts, discussion_input.case_input, opinions)
    return result


def validate_state_update(
    result: DiscussionStateUpdate,
    discussion_input: PathologyDiscussionInput,
    clinical_rules: dict | None = None,
) -> DiscussionStateUpdate:
    _resolve(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _validate_state(result.updated_state, discussion_input.case_input, opinions)
    _validate_changes(result.domain_changes, discussion_input.case_input, opinions)
    del clinical_rules
    return result


def validate_consult_output(
    result: DiscussionConsultOutput,
    discussion_input: PathologyDiscussionInput,
) -> DiscussionConsultOutput:
    _resolve(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _validate_answers(result.chair_answers, discussion_input, opinions)
    _validate_items(result.unresolved_conflicts, discussion_input.case_input, opinions)
    return result


def validate_specialist_opinions(discussion_input: PathologyDiscussionInput) -> None:
    validate_common_specialist_opinions(discussion_input, EvidencePointer)


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


def _validate_changes(changes, case_input, opinions) -> None:
    units = case_units(case_input)
    for change in changes:
        validate_authorized_pointers(
            change.supporting_evidence,
            change.specialist_opinion_ids,
            units,
            opinions,
            _authorization_error,
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
            _authorization_error,
        )


def _validate_answers(answers, discussion_input, opinions) -> None:
    validate_chair_question_order(answers, discussion_input.chair_questions)
    _validate_items(answers, discussion_input.case_input, opinions)


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
    if no_material and "missing_data" in type(value).model_fields:
        if not getattr(value, "missing_data", []):
            raise ValueError(
                "No assessable pathology material requires decision-relevant material needs"
            )
        if not getattr(value, "specialist_dependencies", []):
            raise ValueError(
                "No assessable pathology material requires a material-recovery dependency"
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
