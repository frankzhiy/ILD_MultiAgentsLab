"""Thoracic-radiology-specific validation rules."""

from collections.abc import Iterable

from src.agents.common.validation import (
    authorized_evidence,
    case_units,
    iter_named_lists,
    require_specialty_input,
    resolve_evidence_pointers,
    validate_authorized_pointers,
    validate_chair_question_order,
    validate_pointers,
    validate_specialist_opinions as validate_common_specialist_opinions,
    validate_used_opinions,
)
from src.agents.thoracic_radiology.models import (
    ALL_DOMAINS,
    ConditionalImagingClassification,
    DiscussionConsultOutput,
    DiscussionEvidenceMap,
    DiscussionStateUpdate,
    EvidencePointer,
    InitialImagingFormulation,
    InitialMorphologicAssessment,
    InitialSourceReconstruction,
    LongitudinalImagingAssessment,
    MorphologicPatternAssessment,
    ThoracicRadiologyClinicalState,
    ThoracicRadiologyDiscussionInput,
    ThoracicRadiologyDiscussionResponse,
    ThoracicRadiologyInitialAssessment,
)
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import SpecialtyCaseInput, SpecialtyUnitInput


def validate_initial_assessment(
    result: ThoracicRadiologyInitialAssessment,
    case_input: SpecialtyCaseInput,
    clinical_rules: dict | None = None,
) -> ThoracicRadiologyInitialAssessment:
    if result.case_id and result.case_id != case_input.case_id:
        raise ValueError(f"Assessment case_id {result.case_id} does not match {case_input.case_id}")
    result.case_id = case_input.case_id
    _resolve_evidence_pointers(result, case_input)
    _validate_no_initial_opinions(result)
    _validate_state(result, case_input, {}, clinical_rules)
    return result


def validate_discussion_response(
    result: ThoracicRadiologyDiscussionResponse,
    discussion_input: ThoracicRadiologyDiscussionInput,
    clinical_rules: dict | None = None,
) -> ThoracicRadiologyDiscussionResponse:
    case_input = discussion_input.case_input
    if result.case_id and result.case_id != case_input.case_id:
        raise ValueError(f"Discussion case_id {result.case_id} does not match {case_input.case_id}")
    result.case_id = case_input.case_id
    _resolve_evidence_pointers(result, case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    validate_used_opinions(result.specialist_opinions_used, opinions)
    _validate_state(result.updated_state, case_input, opinions, clinical_rules)
    _validate_domain_changes(result.domain_changes, case_input, opinions)
    _validate_observation_immutability(result, discussion_input, opinions)
    _validate_mapped_findings(result.mapped_findings, case_input, opinions)
    _validate_generic_items(result.unresolved_conflicts, case_input, opinions)
    _validate_chair_answers(result.chair_answers, discussion_input, opinions)
    required_opinion_ids = {
        opinion_id
        for opinion_ids in iter_named_lists(result, "specialist_opinion_ids")
        for opinion_id in opinion_ids
    }
    required_opinion_ids.update(item.opinion_id for item in result.mapped_findings)
    missing_used = required_opinion_ids - set(result.specialist_opinions_used)
    if missing_used:
        raise ValueError(
            f"specialist_opinions_used omits opinions used by the response: {sorted(missing_used)}"
        )
    return result


def validate_source_stage(
    result: InitialSourceReconstruction, case_input: SpecialtyCaseInput
) -> InitialSourceReconstruction:
    _resolve_evidence_pointers(result, case_input)
    _validate_no_initial_opinions(result)
    _validate_source_state(result.source_state, case_input, {})
    _validate_related_items(result.direct_review_requests, case_input)
    return result


def validate_morphology_stage(
    result: InitialMorphologicAssessment,
    case_input: SpecialtyCaseInput,
    clinical_rules: dict | None,
) -> InitialMorphologicAssessment:
    _resolve_evidence_pointers(result, case_input)
    _validate_no_initial_opinions(result)
    _validate_observation_state(result.observation_state, case_input, {})
    if result.longitudinal_assessment:
        _validate_radiology_items([result.longitudinal_assessment], case_input, {})
        _validate_longitudinal_rule(result.longitudinal_assessment, clinical_rules)
    _validate_related_items(result.direct_review_requests, case_input)
    return result


def validate_formulation_stage(
    result: InitialImagingFormulation,
    case_input: SpecialtyCaseInput,
    clinical_rules: dict | None,
) -> InitialImagingFormulation:
    _resolve_evidence_pointers(result, case_input)
    _validate_no_initial_opinions(result)
    if result.morphologic_pattern:
        _validate_radiology_items([result.morphologic_pattern], case_input, {})
        _validate_pattern_rule(result.morphologic_pattern, clinical_rules)
    _validate_generic_items(result.conditional_classifications, case_input, {})
    _validate_conditional_rules(result.conditional_classifications, clinical_rules)
    _validate_generic_items(result.disease_associations, case_input, {})
    _validate_generic_items(result.discordances, case_input, {})
    _validate_related_items(
        [
            *result.specialist_dependencies,
            *result.direct_review_requests,
            *result.missing_data,
        ],
        case_input,
    )
    for question in result.specialist_dependencies:
        if question.specialty == MdtSpecialty.SHARED_CONTEXT:
            raise ValueError("A specialist question cannot target shared_context")
    return result


def validate_evidence_map(
    result: DiscussionEvidenceMap,
    discussion_input: ThoracicRadiologyDiscussionInput,
) -> DiscussionEvidenceMap:
    _resolve_evidence_pointers(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    validate_used_opinions(result.specialist_opinions_used, opinions)
    _validate_mapped_findings(result.mapped_findings, discussion_input.case_input, opinions)
    mapped_ids = {item.opinion_id for item in result.mapped_findings}
    if not mapped_ids.issubset(result.specialist_opinions_used):
        raise ValueError("specialist_opinions_used must include every mapped opinion_id")
    _validate_generic_items(result.unresolved_conflicts, discussion_input.case_input, opinions)
    return result


def validate_state_update(
    result: DiscussionStateUpdate,
    discussion_input: ThoracicRadiologyDiscussionInput,
    clinical_rules: dict | None,
) -> DiscussionStateUpdate:
    _resolve_evidence_pointers(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _validate_state(result.updated_state, discussion_input.case_input, opinions, clinical_rules)
    _validate_domain_changes(result.domain_changes, discussion_input.case_input, opinions)
    response = ThoracicRadiologyDiscussionResponse(
        updated_state=result.updated_state,
        domain_changes=result.domain_changes,
    )
    _validate_observation_immutability(response, discussion_input, opinions)
    return result


def validate_consult_output(
    result: DiscussionConsultOutput,
    discussion_input: ThoracicRadiologyDiscussionInput,
) -> DiscussionConsultOutput:
    _resolve_evidence_pointers(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _validate_chair_answers(result.chair_answers, discussion_input, opinions)
    _validate_generic_items(result.unresolved_conflicts, discussion_input.case_input, opinions)
    return result


def require_radiology_input(case_input: SpecialtyCaseInput) -> None:
    require_specialty_input(
        case_input,
        MdtSpecialty.THORACIC_RADIOLOGY,
        "ThoracicRadiologyAgent",
    )


def validate_specialist_opinions(
    discussion_input: ThoracicRadiologyDiscussionInput,
) -> None:
    validate_common_specialist_opinions(discussion_input, EvidencePointer)


def _resolve_evidence_pointers(value: object, case_input: SpecialtyCaseInput) -> None:
    resolve_evidence_pointers(value, case_input, EvidencePointer)


def _validate_state(
    state: ThoracicRadiologyClinicalState,
    case_input: SpecialtyCaseInput,
    opinions: dict,
    clinical_rules: dict | None,
) -> None:
    state.case_id = case_input.case_id
    _validate_source_state(state.source_state, case_input, opinions)
    _validate_observation_state(state.observation_state, case_input, opinions)
    interpretation = state.interpretation_state
    if interpretation.morphologic_pattern:
        _validate_radiology_items([interpretation.morphologic_pattern], case_input, opinions)
        _validate_pattern_rule(interpretation.morphologic_pattern, clinical_rules)
    if interpretation.longitudinal_assessment:
        _validate_radiology_items([interpretation.longitudinal_assessment], case_input, opinions)
        _validate_longitudinal_rule(interpretation.longitudinal_assessment, clinical_rules)
    _validate_generic_items(interpretation.conditional_classifications, case_input, opinions)
    _validate_conditional_rules(interpretation.conditional_classifications, clinical_rules)
    _validate_generic_items(interpretation.disease_associations, case_input, opinions)
    _validate_generic_items(interpretation.discordances, case_input, opinions)
    _validate_related_items(
        [*state.specialist_dependencies, *state.direct_review_requests, *state.missing_data],
        case_input,
    )
    for question in state.specialist_dependencies:
        if question.specialty == MdtSpecialty.SHARED_CONTEXT:
            raise ValueError("A specialist question cannot target shared_context")


def _validate_source_state(state, case_input, opinions) -> None:
    if state.overall_evaluability == "sufficient_for_pattern_assessment" and not state.examinations:
        raise ValueError("Sufficient source evaluability requires at least one examination")
    exam_ids = [item.exam_id for item in state.examinations]
    if len(exam_ids) != len(set(exam_ids)):
        raise ValueError("Imaging examinations contain duplicate exam_id values")
    units = case_units(case_input)
    for exam in state.examinations:
        _validate_radiology_pointers(exam.supporting_evidence, [], units, opinions)
        validate_pointers(exam.related_evidence, units)


def _validate_observation_state(state, case_input, opinions) -> None:
    items: list[object] = [
        *state.observations,
        state.interstitial_or_alveolar,
        state.fibrosis_assessment,
        state.extent_and_burden,
        *state.ancillary_findings,
        state.acute_overlay,
        *state.explicit_comparisons,
    ]
    _validate_radiology_items([item for item in items if item is not None], case_input, opinions)


def _validate_radiology_items(items, case_input, opinions) -> None:
    units = case_units(case_input)
    for item in items:
        opinion_ids = getattr(item, "specialist_opinion_ids", [])
        pointers = [
            *getattr(item, "supporting_evidence", []),
            *getattr(item, "conflicting_evidence", []),
        ]
        _validate_radiology_pointers(pointers, opinion_ids, units, opinions)
        validate_pointers(getattr(item, "related_evidence", []), units)


def _validate_generic_items(items, case_input, opinions) -> None:
    units = case_units(case_input)
    for item in items:
        opinion_ids = getattr(item, "specialist_opinion_ids", [])
        pointers = [
            *getattr(item, "supporting_evidence", []),
            *getattr(item, "conflicting_evidence", []),
        ]
        validate_authorized_pointers(
            pointers,
            opinion_ids,
            units,
            opinions,
            _reference_only_error,
        )
        validate_pointers(getattr(item, "related_evidence", []), units)


def _validate_related_items(items, case_input) -> None:
    units = case_units(case_input)
    for item in items:
        validate_pointers(getattr(item, "related_evidence", []), units)


def _validate_domain_changes(changes, case_input, opinions) -> None:
    domains = [item.domain for item in changes]
    if len(domains) != len(set(domains)) or set(domains) != set(ALL_DOMAINS):
        raise ValueError("domain_changes must cover each of the seven domains exactly once")
    units = case_units(case_input)
    for change in changes:
        if change.observation_delta == "updated":
            _validate_radiology_pointers(
                change.supporting_evidence,
                change.specialist_opinion_ids,
                units,
                opinions,
            )
        else:
            validate_authorized_pointers(
                change.supporting_evidence,
                change.specialist_opinion_ids,
                units,
                opinions,
                _reference_only_error,
            )


def _validate_observation_immutability(result, discussion_input, opinions) -> None:
    initial = discussion_input.initial_assessment
    updated = result.updated_state
    source_changed = initial.source_state.model_dump() != updated.source_state.model_dump()
    observations_changed = (
        initial.observation_state.model_dump() != updated.observation_state.model_dump()
    )
    interpretation_changed = (
        initial.interpretation_state.model_dump() != updated.interpretation_state.model_dump()
    )
    observation_updates = [
        item for item in result.domain_changes if item.observation_delta == "updated"
    ]
    interpretation_updates = [
        item for item in result.domain_changes if item.interpretation_delta == "updated"
    ]
    if source_changed or observations_changed:
        if not observation_updates:
            raise ValueError("Changed source/observation state requires observation_delta=updated")
        radiology_opinion_ids = {
            opinion_id
            for change in observation_updates
            for opinion_id in change.specialist_opinion_ids
            if opinion_id in opinions
            and opinions[opinion_id].specialty == MdtSpecialty.THORACIC_RADIOLOGY
        }
        if not radiology_opinion_ids:
            raise ValueError(
                "Source/observation state may change only with a formal thoracic radiology claim"
            )
        permitted_evidence = authorized_evidence(
            radiology_opinion_ids, opinions, radiology_only=True
        )
        changed_evidence = {
            evidence_id
            for change in observation_updates
            for pointer in change.supporting_evidence
            for evidence_id in pointer.evidence_ids
        }
        if not changed_evidence or not changed_evidence.issubset(permitted_evidence):
            raise ValueError(
                "Observation updates require evidence IDs cited by the formal thoracic "
                "radiology claim"
            )
    elif observation_updates:
        raise ValueError("observation_delta=updated but source/observation state is unchanged")
    if interpretation_changed and not interpretation_updates:
        raise ValueError("Changed interpretation state requires interpretation_delta=updated")
    if not interpretation_changed and interpretation_updates:
        raise ValueError("interpretation_delta=updated but interpretation state is unchanged")


def _validate_mapped_findings(findings, case_input, opinions) -> None:
    units = case_units(case_input)
    for finding in findings:
        opinion = opinions.get(finding.opinion_id)
        if opinion is None:
            raise ValueError(f"Unknown mapped opinion_id: {finding.opinion_id}")
        if (
            finding.target_layer == "observation"
            and opinion.specialty != MdtSpecialty.THORACIC_RADIOLOGY
        ):
            raise ValueError("Only a thoracic radiology opinion may target observation layer")
        validate_authorized_pointers(
            finding.evidence,
            [finding.opinion_id],
            units,
            opinions,
            _reference_only_error,
        )


def _validate_chair_answers(answers, discussion_input, opinions) -> None:
    validate_chair_question_order(answers, discussion_input.chair_questions)
    _validate_generic_items(answers, discussion_input.case_input, opinions)


def _validate_pattern_rule(
    pattern: MorphologicPatternAssessment, clinical_rules: dict | None
) -> None:
    morphology_rule = (clinical_rules or {}).get("morphology") or {}
    configured = morphology_rule.get("source")
    if configured and pattern.framework != configured:
        raise ValueError(
            f"Pattern framework {pattern.framework!r} does not match configured {configured!r}"
        )
    recognized = set(morphology_rule.get("recognized_patterns") or [])
    selected = [
        item for item in [pattern.primary_pattern, *pattern.coexisting_patterns] if item is not None
    ]
    unknown = sorted(set(selected) - recognized) if recognized else []
    if unknown:
        raise ValueError(f"Unknown patterns for configured morphology framework: {unknown}")
    if pattern.classification_status in {"confident_pattern", "provisional_pattern"}:
        if not pattern.primary_pattern:
            raise ValueError("Confident or provisional pattern requires primary_pattern")
    if pattern.classification_status == "not_assessable" and pattern.primary_pattern is not None:
        raise ValueError("not_assessable pattern cannot assign primary_pattern")


def _validate_conditional_rules(
    classifications: list[ConditionalImagingClassification], clinical_rules: dict | None
) -> None:
    ipf_rule = (clinical_rules or {}).get("ipf_hrct") or {}
    configured_source = ipf_rule.get("source")
    configured_categories = set(ipf_rule.get("categories") or [])
    if len([item for item in classifications if item.protocol == "ipf_hrct_2022"]) > 1:
        raise ValueError("ipf_hrct_2022 classification may appear at most once")
    for item in classifications:
        if configured_source and item.rule_source != configured_source:
            raise ValueError(
                f"IPF HRCT rule_source {item.rule_source!r} does not match configured "
                f"{configured_source!r}"
            )
        if item.applicability == "applicable":
            if not item.category:
                raise ValueError("Applicable IPF HRCT classification requires a category")
            if configured_categories and item.category not in configured_categories:
                raise ValueError(f"Unknown configured IPF HRCT category: {item.category}")
            if not item.supporting_evidence and not item.related_evidence:
                raise ValueError(
                    "Applicable IPF HRCT classification requires applicability evidence"
                )
        elif item.category is not None:
            raise ValueError("Non-applicable IPF HRCT classification cannot assign a category")


def _validate_longitudinal_rule(
    assessment: LongitudinalImagingAssessment, clinical_rules: dict | None
) -> None:
    configured = ((clinical_rules or {}).get("radiologic_progression") or {}).get("source")
    if configured and assessment.rule_source != configured:
        raise ValueError(
            f"Radiologic progression rule_source {assessment.rule_source!r} does not match "
            f"configured {configured!r}"
        )
    if assessment.status == "radiologic_progression":
        if not assessment.progression_features or not assessment.supporting_evidence:
            raise ValueError("Radiologic progression requires features and supporting evidence")
    if assessment.status == "requires_comparator" and assessment.progression_features:
        raise ValueError("requires_comparator cannot include confirmed progression features")
    if assessment.status in {"stable", "improved", "mixed_change"}:
        if not assessment.supporting_evidence:
            raise ValueError(f"{assessment.status} longitudinal assessment requires evidence")
    if assessment.acute_overlay_status == "absent" and not assessment.supporting_evidence:
        raise ValueError("Absent acute overlay requires explicit supporting imaging evidence")


def _validate_no_initial_opinions(value: object) -> None:
    for opinion_ids in iter_named_lists(value, "specialist_opinion_ids"):
        if opinion_ids:
            raise ValueError("Initial radiology outputs cannot cite specialist_opinion_ids")


def _validate_radiology_pointers(
    pointers: Iterable[EvidencePointer],
    specialist_opinion_ids: list[str],
    units: dict[str, SpecialtyUnitInput],
    opinions: dict,
) -> None:
    pointers = list(pointers)
    validate_pointers(pointers, units)
    permitted_evidence = authorized_evidence(specialist_opinion_ids, opinions, radiology_only=True)
    for pointer in pointers:
        unit = units[pointer.graph_unit_id]
        if MdtSpecialty.THORACIC_RADIOLOGY in unit.graph_unit.mdt_specialty:
            continue
        if not set(pointer.evidence_ids).issubset(permitted_evidence):
            raise ValueError(
                f"Unit {pointer.graph_unit_id} is not radiology-scoped and lacks an exact "
                "formal thoracic radiology claim"
            )


def _reference_only_error(unit, pointer) -> str:
    return (
        f"Reference-only unit {pointer.graph_unit_id} requires a formal specialist "
        "claim citing the exact same evidence IDs"
    )
