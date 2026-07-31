"""Validation for the shared formal initial specialty output."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict, create_model, model_validator

from src.agents.common.initial_output import (
    CaseEvidencePointer,
    EvidenceBundle,
    EvidenceDirection,
    EvidenceFunction,
    EvidenceRelation,
    SpecialtyAtomicClaim,
    SpecialtyInitialOutput,
)
from src.agents.common.validation import (
    case_units,
    resolve_evidence_pointers,
    validate_pointers,
)
from src.llm.prompting import prompt_json
from src.llm.structured import StructuredLLMGenerator
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget
from src.schemas.specialty_agent_input import SpecialtyCaseInput


_CROSS_SPECIALTY_CONFLICT = re.compile(
    r"(?:跨专科冲突|与(?:呼吸|影像|风湿|病理)科(?:的)?(?:结论|意见)(?:存在|构成)?冲突)"
)
_MAX_EVIDENCE_SLOTS_PER_CALL = 64


class _EvidenceSlotDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: EvidenceDirection
    function: EvidenceFunction

    @model_validator(mode="after")
    def validate_dimensions(self):
        if self.function == "background" and self.direction != "neutral":
            raise ValueError("background evidence must have direction='neutral'")
        if self.function == "foundational" and self.direction == "neutral":
            raise ValueError("foundational evidence must support or weaken the claim")
        return self


class _BackgroundEvidenceSlotDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["neutral"]
    function: Literal["background"]


_EVIDENCE_DIMENSION_SCHEMA_CONSTRAINTS = {
    "_EvidenceSlotDecision": [
        {"direction": {"neutral"}, "function": {"background"}},
        {
            "direction": {"supports", "weakens"},
            "function": {"foundational"},
        },
        {
            "direction": {"supports", "weakens", "neutral"},
            "function": {"discriminating", "qualifying"},
        },
    ]
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


def assign_specialty_initial_evidence(
    result: SpecialtyInitialOutput,
    case_input: SpecialtyCaseInput,
    specialty: SpecialistTarget,
    generator: StructuredLLMGenerator,
    internal_state: BaseModel | None = None,
    diagnostic_evidence_ids: set[str] | None = None,
) -> tuple[SpecialtyInitialOutput, dict]:
    """Annotate one fixed slot per atomic claim and evidence block."""

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
    blocks = {
        block.evidence_id: {
            "quote": block.text,
            "diagnostic_eligible": block.evidence_id in diagnostic_ids,
        }
        for unit in units.values()
        for block in unit.clinical_propositions.evidence_blocks
    }
    claims = {}
    slots = {}
    slot_number = 1
    for assessment in result.specialty_assessments.assessments:
        if not assessment.claims:
            assessment.claims = [SpecialtyAtomicClaim(statement=assessment.statement)]
        for claim_number, claim in enumerate(assessment.claims, 1):
            claim.claim_id = f"{assessment.assessment_id}_c{claim_number:03d}"
            claims[claim.claim_id] = {
                "assessment_id": assessment.assessment_id,
                "assessment": assessment.statement,
                "claim": claim.statement,
            }
            for evidence_id in blocks:
                slot_id = f"slot_{slot_number:04d}"
                slot_number += 1
                slots[slot_id] = {
                    "assessment_id": assessment.assessment_id,
                    "claim_id": claim.claim_id,
                    "evidence_id": evidence_id,
                }
    if not slots:
        return validate_specialty_initial_output(
            result,
            case_input,
            specialty,
            internal_state,
            diagnostic_evidence_ids,
        ), {"skipped": True, "reason": "no evidence slots"}

    assessments = {
        assessment.assessment_id: assessment
        for assessment in result.specialty_assessments.assessments
    }
    for assessment in assessments.values():
        assessment.evidence = EvidenceBundle()
    slot_items = list(slots.items())
    traces = []
    for batch_number, start in enumerate(
        range(0, len(slot_items), _MAX_EVIDENCE_SLOTS_PER_CALL),
        1,
    ):
        batch_slots = dict(
            slot_items[start : start + _MAX_EVIDENCE_SLOTS_PER_CALL]
        )
        batch_claim_ids = {slot["claim_id"] for slot in batch_slots.values()}
        batch_evidence_ids = {
            slot["evidence_id"] for slot in batch_slots.values()
        }
        batch_prompt = {
            "claims": {
                claim_id: claim
                for claim_id, claim in claims.items()
                if claim_id in batch_claim_ids
            },
            "evidence": {
                evidence_id: evidence
                for evidence_id, evidence in blocks.items()
                if evidence_id in batch_evidence_ids
            },
            "slots": batch_slots,
        }
        assignment_model = create_model(
            f"SpecialtyEvidenceAssignments{batch_number:03d}",
            __config__=ConfigDict(extra="forbid"),
            **{
                slot_id: (
                    (
                        _EvidenceSlotDecision
                        if blocks[slot["evidence_id"]]["diagnostic_eligible"]
                        else _BackgroundEvidenceSlotDecision
                    )
                    | None,
                    ...,
                )
                for slot_id, slot in batch_slots.items()
            },
        )
        assignments, trace = generator.generate(
            schema_model=assignment_model,
            schema_name=(
                f"{specialty.value}_initial_evidence_assignment_{batch_number:03d}"
            ),
            system_prompt=(
                "你是专科正式输出的证据标注器。只判断固定证据槽位是否用于对应原子"
                " claim，不改变医学判断。"
            ),
            user_prompt=(
                "每个字段对应程序生成的唯一 claim × evidence 槽位。使用该证据时填写"
                " direction 和 function；不使用时填写 null。不得因为同一证据用于其他"
                " claim 而省略当前槽位。diagnostic_eligible=false 的槽位若使用，只能是"
                " neutral/background。每个槽位必须恰好返回一次。\n\n"
                f"{prompt_json(batch_prompt)}"
            ),
            dependent_field_constraints=_EVIDENCE_DIMENSION_SCHEMA_CONSTRAINTS,
        )
        traces.append(trace)
        for slot_id, decision in assignments.model_dump(mode="json").items():
            if decision is None:
                continue
            slot = batch_slots[slot_id]
            assessments[slot["assessment_id"]].evidence.evidence_relations.append(
                EvidenceRelation(
                    evidence_ids=[slot["evidence_id"]],
                    target_claim_id=slot["claim_id"],
                    direction=decision["direction"],
                    function=decision["function"],
                )
            )
    return validate_specialty_initial_output(
        result,
        case_input,
        specialty,
        internal_state,
        diagnostic_evidence_ids,
    ), {
        "slot_count": len(slots),
        "batch_count": len(traces),
        "batches": traces,
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
                if item.status not in {"not_assessable", "not_applicable"}
            ]
            if invalid:
                raise ValueError(
                    "Pathology without assessable material cannot construct a pattern candidate"
                )
            if assessments.assessability != "not_assessable":
                raise ValueError(
                    "Pathology without assessable material must be not_assessable"
                )
            if not assessments.evidence_gaps:
                raise ValueError(
                    "Pathology without assessable material must specify what evidence to obtain"
                )
            if not questions:
                raise ValueError(
                    "Pathology without assessable material must assign a material-recovery question"
                )

    for text in _iter_text(result):
        if _CROSS_SPECIALTY_CONFLICT.search(text):
            raise ValueError("Formal initial output must not detect cross-specialty conflict")
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
