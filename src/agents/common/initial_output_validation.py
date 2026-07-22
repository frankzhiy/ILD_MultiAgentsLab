"""Validation for the shared formal initial specialty output."""

from __future__ import annotations

import re
from collections.abc import Iterator

from pydantic import BaseModel

from src.agents.common.initial_output import (
    CaseEvidencePointer,
    EvidenceBundle,
    SpecialtyInitialOutput,
)
from src.agents.common.validation import (
    case_units,
    resolve_evidence_pointers,
    validate_pointers,
)
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget
from src.schemas.specialty_agent_input import SpecialtyCaseInput


_PROBABILITY_OR_CONFIDENCE = re.compile(
    r"(?:诊断)?概率|置信度|diagnostic\s+probability|confidence(?:\s+(?:score|level))?",
    re.I,
)
_PERCENTED_POSSIBILITY = re.compile(
    r"(?:可能性.{0,12}(?:\d+(?:\.\d+)?\s*%|百分之[零一二三四五六七八九十百千万两\d]+)"
    r"|(?:\d+(?:\.\d+)?\s*%|百分之[零一二三四五六七八九十百千万两\d]+)"
    r".{0,12}可能性)",
    re.I,
)
_CROSS_SPECIALTY_CONFLICT = re.compile(
    r"(?:跨专科冲突|与(?:呼吸|影像|风湿|病理)科(?:的)?(?:结论|意见)(?:存在|构成)?冲突)"
)


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
        "supporting": [{"evidence_ids": diagnostic_evidence}],
        "weakening": [{"evidence_ids": diagnostic_evidence}],
        "discriminating": [{"evidence_ids": diagnostic_evidence}],
        "background": [{"evidence_ids": all_evidence}],
        "related_evidence": [{"evidence_ids": all_evidence}],
    }


def validate_specialty_initial_output(
    result: SpecialtyInitialOutput,
    case_input: SpecialtyCaseInput,
    specialty: SpecialistTarget,
    internal_state: BaseModel | None = None,
    diagnostic_evidence_ids: set[str] | None = None,
) -> SpecialtyInitialOutput:
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
    for groups in _iter_models(result, EvidenceBundle):
        for field in ("supporting", "weakening", "discriminating"):
            pointers = getattr(groups, field)
            validate_pointers(pointers, units)
            invalid = sorted(
                {
                    evidence_id
                    for pointer in pointers
                    for evidence_id in pointer.evidence_ids
                    if evidence_id not in diagnostic_ids
                }
            )
            if invalid:
                raise ValueError(
                    f"{field} evidence cannot use context-only evidence IDs: {invalid}"
                )
        validate_pointers(groups.background, units)

    for question in result.professional_conclusions.interspecialty_questions:
        if question.target_specialty == specialty:
            raise ValueError("An interspecialty question cannot target the issuing specialty")
        validate_pointers(question.related_evidence, units)
    for gap in result.professional_conclusions.evidence_gaps:
        validate_pointers(gap.related_evidence, units)

    _require_unique(
        [item.conclusion_id for item in result.professional_conclusions.conclusions],
        "conclusion_id",
    )
    candidates = result.clinical_reasoning.candidate_explanations
    candidate_ids = [item.candidate_id for item in candidates]
    _require_unique(candidate_ids, "candidate_id")
    _require_unique(
        [item.comparison_id for item in result.clinical_reasoning.evidence_comparisons],
        "comparison_id",
    )
    _require_unique(
        [item.check_id for item in result.clinical_reasoning.consistency_checks],
        "check_id",
    )
    _require_unique(
        [item.review_id for item in result.clinical_reasoning.boundary_reviews],
        "review_id",
    )
    known_candidates = set(candidate_ids)
    for comparison in result.clinical_reasoning.evidence_comparisons:
        unknown = sorted(set(comparison.candidate_ids) - known_candidates)
        if unknown:
            raise ValueError(
                f"Evidence comparison references unknown candidate_ids: {unknown}"
            )
        if len(comparison.candidate_ids) != len(set(comparison.candidate_ids)):
            raise ValueError(
                f"Evidence comparison {comparison.comparison_id} repeats candidate_ids"
            )

    conclusion_types = {
        item.conclusion_type for item in result.professional_conclusions.conclusions
    }
    if specialty == SpecialistTarget.RHEUMATOLOGY:
        required = {"rheumatic_disease", "ild_attribution"}
        missing = sorted(required - conclusion_types)
        if missing:
            raise ValueError(
                f"Rheumatology formal output requires separate conclusion types: {missing}"
            )
    if specialty == SpecialistTarget.PATHOLOGY and internal_state is not None:
        source = getattr(internal_state, "source_assessment", None)
        if source is not None and getattr(source, "material_status", None) in {
            "no_pathology_material",
            "pathology_mentioned_without_report",
            "uncertain_availability",
        }:
            invalid = [
                item.conclusion_id
                for item in result.professional_conclusions.conclusions
                if item.status not in {"not_assessable", "not_applicable"}
            ]
            if (
                invalid
                or result.clinical_reasoning.candidate_explanations
                or result.clinical_reasoning.evidence_comparisons
            ):
                raise ValueError(
                    "Pathology without assessable material cannot construct a pattern candidate"
                )
            if result.professional_conclusions.assessability != "not_assessable":
                raise ValueError(
                    "Pathology without assessable material must be not_assessable"
                )
            if not result.professional_conclusions.evidence_gaps:
                raise ValueError(
                    "Pathology without assessable material must specify what evidence to obtain"
                )
            if not result.professional_conclusions.interspecialty_questions:
                raise ValueError(
                    "Pathology without assessable material must assign a material-recovery question"
                )

    for text in _iter_text(result):
        if _PROBABILITY_OR_CONFIDENCE.search(text) or _PERCENTED_POSSIBILITY.search(text):
            raise ValueError("Formal initial output must not express probability or confidence")
        if _CROSS_SPECIALTY_CONFLICT.search(text):
            raise ValueError("Formal initial output must not detect cross-specialty conflict")
    return result


def _require_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} values must be unique")


def _iter_models(value: object, model_type: type[BaseModel]) -> Iterator[BaseModel]:
    if isinstance(value, model_type):
        yield value
    if isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            yield from _iter_models(getattr(value, field_name), model_type)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_models(item, model_type)


def _iter_text(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            if field_name not in {"quote", "title", "source_file"}:
                yield from _iter_text(getattr(value, field_name))
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_text(item)
