"""Validation for the shared formal initial specialty output."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel

from src.agents.common.initial_output import (
    CaseEvidencePointer,
    EvidenceBundle,
    SpecialtyInitialOutput,
    synchronize_claim_evidence,
    synchronize_judgment_conditions,
)
from src.agents.common.validation import (
    case_units,
    resolve_evidence_pointers,
    validate_pointers,
)
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget
from src.schemas.specialty_agent_input import SpecialtyCaseInput


# Encode the field relationship before generation; keep runtime validation for JSON fallback.
FORMAL_ASSESSMENT_CONSTRAINTS = {
    "SpecialtyAssessment": [
        {"assessability": {"assessable", "partially_assessable"}},
        {"assessability": {"not_assessable"}, "confidence": {"unknown"}, "direction": {"indeterminate"}},
    ],
}


def formal_evidence_schema_constraints(
    case_input: SpecialtyCaseInput,
    diagnostic_evidence_ids: set[str] | None = None,
) -> dict[str, list[dict[str, set[str]]]]:
    units = case_units(case_input).values()
    all_evidence = {
        block.evidence_id
        for unit in units
        for block in unit.clinical_propositions.evidence_blocks
    }
    diagnostic_evidence = (
        diagnostic_evidence_ids
        if diagnostic_evidence_ids is not None
        else {
            block.evidence_id
            for unit in units
            if unit.may_support_diagnostic_claim
            for block in unit.clinical_propositions.evidence_blocks
        }
    )
    return {
        "evidence_relations": [
            {
                "evidence_ids": diagnostic_evidence,
                "function": {"foundational", "discriminating", "qualifying"},
                "direction": {"supports", "weakens", "neutral"},
            },
            {
                "evidence_ids": all_evidence,
                "function": {"background"},
                "direction": {"neutral"},
            },
        ],
        "related_evidence": [{"evidence_ids": all_evidence}],
    }


def validate_specialty_initial_output(
    result: SpecialtyInitialOutput,
    case_input: SpecialtyCaseInput,
    specialty: SpecialistTarget,
    internal_state: BaseModel | None = None,
    diagnostic_evidence_ids: set[str] | None = None,
    *,
    require_claim_evidence: bool = False,
) -> SpecialtyInitialOutput:
    for assessment in result.specialty_assessments.assessments:
        for claim_number, claim in enumerate(assessment.claims, 1):
            if require_claim_evidence and "evidence_relations" not in claim.model_fields_set:
                raise ValueError("Every atomic claim must explicitly return evidence_relations")
            if require_claim_evidence or not claim.claim_id:
                claim.claim_id = f"{assessment.assessment_id}_c{claim_number:03d}"
        synchronize_claim_evidence(assessment, replace_evidence=require_claim_evidence)
    resolve_evidence_pointers(result, case_input, CaseEvidencePointer)
    units = case_units(case_input)
    diagnostic_ids = (
        diagnostic_evidence_ids
        if diagnostic_evidence_ids is not None
        else {
            block.evidence_id
            for unit in units.values()
            if unit.may_support_diagnostic_claim
            for block in unit.clinical_propositions.evidence_blocks
        }
    )
    assessments = result.specialty_assessments
    relation_conflicts = {}
    for assessment in assessments.assessments:
        groups = assessment.evidence
        claim_ids = {claim.claim_id for claim in assessment.claims}
        invalid_targets = sorted(
            {
                relation.target_claim_id
                for relation in groups.evidence_relations
                if relation.target_claim_id
                and relation.target_claim_id not in claim_ids
            }
        )
        if invalid_targets:
            raise ValueError(
                f"Evidence relations reference unknown claim IDs: {invalid_targets}"
            )
        validate_pointers(groups.evidence_relations, units)
        invalid = sorted(
            {
                evidence_id
                for relation in groups.evidence_relations
                if relation.function != "background"
                for evidence_id in relation.evidence_ids
                if evidence_id not in diagnostic_ids
            }
        )
        if invalid:
            raise ValueError(
                "Non-background evidence relations cannot use context-only "
                f"evidence IDs: {invalid}"
            )
        _merge_graph_unit_relations(groups)
        synchronize_judgment_conditions(assessment)
        conflicts = _conflicting_evidence_relations(groups)
        if conflicts:
            relation_conflicts[assessment.assessment_id] = conflicts
    if relation_conflicts:
        raise ValueError(
            "Each evidence locator must appear once per atomic claim; combine its "
            "direction and function in one evidence_relations item: "
            f"{relation_conflicts}"
        )

    questions = result.interspecialty_questions.questions
    assessment_ids = {item.assessment_id for item in assessments.assessments}
    for question in questions:
        if question.target_specialty == specialty:
            raise ValueError("An interspecialty question cannot target the issuing specialty")
        unknown = sorted(set(question.related_assessment_ids) - assessment_ids)
        if unknown:
            raise ValueError(
                f"Interspecialty question references unknown assessment_ids: {unknown}"
            )
        validate_pointers(question.related_evidence, units)
    for gap in assessments.evidence_gaps:
        unknown = sorted(set(gap.related_assessment_ids) - assessment_ids)
        if unknown:
            raise ValueError(f"Evidence gap references unknown assessment_ids: {unknown}")
        validate_pointers(gap.related_evidence, units)

    _require_unique(
        [item.assessment_id for item in assessments.assessments],
        "assessment_id",
    )
    for assessment in assessments.assessments:
        _require_unique(
            [claim.claim_id for claim in assessment.claims if claim.claim_id],
            f"{assessment.assessment_id} claim_id",
        )

    assessment_types = {
        item.assessment_type for item in assessments.assessments
    }
    if specialty == SpecialistTarget.RHEUMATOLOGY:
        required = {"rheumatic_disease", "ild_attribution"}
        missing = sorted(required - assessment_types)
        if missing:
            raise ValueError(
                f"Rheumatology formal output requires separate assessment types: {missing}"
            )
    if specialty == SpecialistTarget.PATHOLOGY and internal_state is not None:
        source = getattr(internal_state, "source_assessment", None)
        if source is not None and getattr(source, "material_status", None) in {
            "no_pathology_material",
            "pathology_mentioned_without_report",
            "uncertain_availability",
        }:
            invalid = [
                item.assessment_id
                for item in assessments.assessments
                if item.assessability != "not_assessable"
            ]
            if invalid:
                raise ValueError(
                    "Pathology without assessable material cannot construct a pattern candidate"
                )
            if assessments.assessability != "not_assessable":
                raise ValueError(
                    "Pathology without assessable material must be not_assessable"
                )
    return result


def _require_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} values must be unique")


def _merge_graph_unit_relations(groups: EvidenceBundle) -> None:
    """Merge graph pointers only when both evidence dimensions are identical."""

    merged: dict[tuple[str, str, str, str], EvidenceRelation] = {}
    for relation in groups.evidence_relations:
        key = (
            relation.target_claim_id,
            relation.graph_unit_id,
            relation.direction,
            relation.function,
        )
        if key not in merged:
            merged[key] = relation.model_copy(deep=True)
            continue
        current = merged[key]
        current.evidence_ids = list(dict.fromkeys([
            *current.evidence_ids,
            *relation.evidence_ids,
        ]))
        current.node_ids = list(dict.fromkeys([*current.node_ids, *relation.node_ids]))
        current.quote = _merge_quotes(current.quote, relation.quote)
    groups.evidence_relations = list(merged.values())


def _conflicting_evidence_relations(
    groups: EvidenceBundle,
) -> dict[str, list[dict[str, str]]]:
    relations_by_locator: dict[tuple[str, str, str], set[tuple[str, str]]] = {}
    for relation in groups.evidence_relations:
        for evidence_id in relation.evidence_ids:
            relations_by_locator.setdefault(
                (relation.target_claim_id, relation.graph_unit_id, evidence_id), set()
            ).add((relation.direction, relation.function))
    return {
        f"{claim_id or 'assessment'}:{graph_unit_id}:{evidence_id}": [
            {"direction": direction, "function": function}
            for direction, function in sorted(relations)
        ]
        for (
            claim_id,
            graph_unit_id,
            evidence_id,
        ), relations in relations_by_locator.items()
        if len(relations) > 1
    }


def _merge_quotes(*values: str) -> str:
    quotes = [value for value in dict.fromkeys(values) if value]
    return "\n".join(
        quote
        for quote in quotes
        if not any(quote != other and quote in other for other in quotes)
    )


def _iter_models(value: object, model_type: type[BaseModel]) -> Iterator[BaseModel]:
    if isinstance(value, model_type):
        yield value
    if isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            yield from _iter_models(getattr(value, field_name), model_type)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_models(item, model_type)
