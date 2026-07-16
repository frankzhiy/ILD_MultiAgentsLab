"""Programmatic validation shared by specialty agents."""

from collections.abc import Callable, Iterable, Iterator

from pydantic import BaseModel

from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import SpecialtyCaseInput, SpecialtyUnitInput


def require_specialty_input(
    case_input: SpecialtyCaseInput,
    expected_specialty: MdtSpecialty,
    agent_name: str,
) -> None:
    if case_input.target_specialty != expected_specialty:
        raise ValueError(
            f"{agent_name} requires target_specialty={expected_specialty.value}, "
            f"got {case_input.target_specialty}"
        )
    if case_input.summary.unit_count < 1:
        raise ValueError(f"{agent_name} requires at least one graph unit")
    case_units(case_input)


def case_units(case_input: SpecialtyCaseInput) -> dict[str, SpecialtyUnitInput]:
    indexed = {}
    for segment in case_input.segments:
        segment_id = segment.segment.segment_id
        for unit in segment.units:
            unit_id = unit.graph_unit.graph_unit_id
            if unit_id in indexed:
                raise ValueError(f"Duplicate graph_unit_id in specialty input: {unit_id}")
            if unit.graph_unit.segment_id != segment_id:
                raise ValueError(
                    f"Specialty input unit {unit_id} is under the wrong segment {segment_id}"
                )
            indexed[unit_id] = unit
    if len(indexed) != case_input.summary.unit_count:
        raise ValueError("Specialty input summary.unit_count does not match its units")
    return indexed


def resolve_evidence_pointers(
    value: object,
    case_input: SpecialtyCaseInput,
    pointer_type: type[BaseModel],
) -> None:
    evidence_index: dict[str, tuple[SpecialtyUnitInput, str]] = {}
    for unit in case_units(case_input).values():
        for block in unit.clinical_propositions.evidence_blocks:
            if block.evidence_id in evidence_index:
                raise ValueError(f"Duplicate evidence_id in specialty input: {block.evidence_id}")
            evidence_index[block.evidence_id] = (unit, block.text)

    _split_evidence_pointers_by_unit(value, pointer_type, evidence_index)

    for pointer in iter_evidence_pointers(value, pointer_type):
        if not pointer.evidence_ids:
            raise ValueError("Evidence pointer must include at least one evidence_id")
        if len(pointer.evidence_ids) != len(set(pointer.evidence_ids)):
            raise ValueError("Evidence pointer contains duplicate evidence_ids")
        missing = sorted(set(pointer.evidence_ids) - set(evidence_index))
        if missing:
            raise ValueError(f"Evidence pointer has unknown evidence_ids: {missing}")
        referenced_units = {
            evidence_index[evidence_id][0].graph_unit.graph_unit_id
            for evidence_id in pointer.evidence_ids
        }
        if len(referenced_units) != 1:
            locations = ", ".join(
                f"{evidence_id}->{evidence_index[evidence_id][0].graph_unit.graph_unit_id}"
                for evidence_id in pointer.evidence_ids
            )
            raise ValueError(
                "Evidence pointer evidence_ids must belong to one graph unit; "
                f"split this pointer: {locations}"
            )
        unit = evidence_index[pointer.evidence_ids[0]][0]
        selected_ids = set(pointer.evidence_ids)
        blocks = [
            block
            for block in unit.clinical_propositions.evidence_blocks
            if block.evidence_id in selected_ids
        ]
        pointer.evidence_ids = [block.evidence_id for block in blocks]
        pointer.graph_unit_id = unit.graph_unit.graph_unit_id
        pointer.segment_id = unit.graph_unit.segment_id
        pointer.quote = "".join(block.text for block in blocks)
        pointer.node_ids = [
            node.node_id
            for node in unit.local_graph.nodes
            if selected_ids.intersection(node.evidence.evidence_ids)
        ]


def _split_evidence_pointers_by_unit(
    value: object,
    pointer_type: type[BaseModel],
    evidence_index: dict[str, tuple[SpecialtyUnitInput, str]],
) -> None:
    """Normalize a model's pointer lists so each pointer addresses one graph unit."""

    if isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            _split_evidence_pointers_by_unit(
                getattr(value, field_name), pointer_type, evidence_index
            )
        return
    if isinstance(value, dict):
        for item in value.values():
            _split_evidence_pointers_by_unit(item, pointer_type, evidence_index)
        return
    if not isinstance(value, list):
        return

    normalized = []
    for item in value:
        if not isinstance(item, pointer_type):
            _split_evidence_pointers_by_unit(item, pointer_type, evidence_index)
            normalized.append(item)
            continue
        if not item.evidence_ids:
            normalized.append(item)
            continue
        grouped: dict[str, list[str]] = {}
        for evidence_id in item.evidence_ids:
            indexed = evidence_index.get(evidence_id)
            key = indexed[0].graph_unit.graph_unit_id if indexed else f"unknown:{evidence_id}"
            grouped.setdefault(key, []).append(evidence_id)
        normalized.extend(
            item.model_copy(deep=True, update={"evidence_ids": evidence_ids})
            for evidence_ids in grouped.values()
        )
    value[:] = normalized


def iter_evidence_pointers(value: object, pointer_type: type[BaseModel]) -> Iterator[BaseModel]:
    if isinstance(value, pointer_type):
        yield value
    elif isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            yield from iter_evidence_pointers(getattr(value, field_name), pointer_type)
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_evidence_pointers(item, pointer_type)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_evidence_pointers(item, pointer_type)


def iter_named_lists(value: object, field_name: str) -> Iterator[list]:
    if isinstance(value, BaseModel):
        if field_name in type(value).model_fields:
            yield getattr(value, field_name)
        for name in type(value).model_fields:
            yield from iter_named_lists(getattr(value, name), field_name)
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_named_lists(item, field_name)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_named_lists(item, field_name)


def validate_pointers(pointers: Iterable[BaseModel], units: dict[str, SpecialtyUnitInput]) -> None:
    for pointer in pointers:
        unit = units.get(pointer.graph_unit_id)
        if unit is None:
            raise ValueError(f"Unknown graph_unit_id in evidence pointer: {pointer.graph_unit_id}")
        if pointer.segment_id != unit.graph_unit.segment_id:
            raise ValueError(
                f"Evidence pointer {pointer.graph_unit_id} has segment_id {pointer.segment_id}; "
                f"expected {unit.graph_unit.segment_id}"
            )
        known_nodes = {node.node_id for node in unit.local_graph.nodes}
        missing_nodes = sorted(set(pointer.node_ids) - known_nodes)
        if missing_nodes:
            raise ValueError(
                f"Evidence pointer {pointer.graph_unit_id} has unknown node_ids: {missing_nodes}"
            )
        known_evidence = {block.evidence_id for block in unit.clinical_propositions.evidence_blocks}
        missing_evidence = sorted(set(pointer.evidence_ids) - known_evidence)
        if missing_evidence:
            raise ValueError(
                f"Evidence pointer {pointer.graph_unit_id} has unknown evidence_ids: "
                f"{missing_evidence}"
            )


def authorized_evidence(
    opinion_ids: Iterable[str], opinions: dict, *, radiology_only: bool = False
) -> set[str]:
    opinion_ids = list(opinion_ids)
    unknown = set(opinion_ids) - set(opinions)
    if unknown:
        raise ValueError(f"Unknown specialist_opinion_ids: {sorted(unknown)}")
    return {
        evidence_id
        for opinion_id in opinion_ids
        if not radiology_only or opinions[opinion_id].specialty == MdtSpecialty.THORACIC_RADIOLOGY
        for claim in opinions[opinion_id].claims
        for pointer in claim.evidence
        for evidence_id in pointer.evidence_ids
    }


def diagnostic_evidence_schema_constraints(
    case_input: SpecialtyCaseInput,
    specialist_opinions: Iterable[BaseModel] = (),
) -> dict[str, list[dict[str, set[str]]]]:
    units = case_units(case_input).values()
    all_evidence = {
        block.evidence_id
        for unit in units
        for block in unit.clinical_propositions.evidence_blocks
    }
    allowed = {
        block.evidence_id
        for unit in units
        if unit.may_support_diagnostic_claim
        for block in unit.clinical_propositions.evidence_blocks
    }
    allowed.update(
        evidence_id
        for opinion in specialist_opinions
        for claim in opinion.claims
        for pointer in claim.evidence
        for evidence_id in pointer.evidence_ids
    )
    alternatives = [{"evidence_ids": allowed}]
    return {
        "supporting_evidence": alternatives,
        "conflicting_evidence": alternatives,
        "related_evidence": [{"evidence_ids": all_evidence}],
    }


def validate_authorized_pointers(
    pointers: Iterable[BaseModel],
    specialist_opinion_ids: list[str],
    units: dict[str, SpecialtyUnitInput],
    opinions: dict,
    error_message: Callable[[SpecialtyUnitInput, BaseModel], str],
) -> None:
    pointers = list(pointers)
    validate_pointers(pointers, units)
    authorized = authorized_evidence(specialist_opinion_ids, opinions)
    errors = []
    for pointer in pointers:
        unit = units[pointer.graph_unit_id]
        if unit.may_support_diagnostic_claim:
            continue
        if not set(pointer.evidence_ids).issubset(authorized):
            errors.append(error_message(unit, pointer))
    if errors:
        raise ValueError("\n".join(dict.fromkeys(errors)))


def validate_authorized_items(
    items: Iterable[object],
    case_input: SpecialtyCaseInput,
    opinions: dict,
    error_message: Callable[[SpecialtyUnitInput, BaseModel], str],
) -> None:
    units = case_units(case_input)
    errors = []
    for item in items:
        pointers = [
            *getattr(item, "supporting_evidence", []),
            *getattr(item, "conflicting_evidence", []),
        ]
        try:
            validate_authorized_pointers(
                pointers,
                getattr(item, "specialist_opinion_ids", []),
                units,
                opinions,
                error_message,
            )
            validate_pointers(getattr(item, "related_evidence", []), units)
        except ValueError as exc:
            errors.extend(str(exc).splitlines())
    if errors:
        raise ValueError(
            "证据校验发现以下全部问题：\n- " + "\n- ".join(dict.fromkeys(errors))
        )


def validate_specialist_opinions(
    discussion_input: BaseModel, pointer_type: type[BaseModel]
) -> None:
    resolve_evidence_pointers(
        discussion_input.specialist_opinions,
        discussion_input.case_input,
        pointer_type,
    )
    units = case_units(discussion_input.case_input)
    opinion_ids = [item.opinion_id for item in discussion_input.specialist_opinions]
    if len(opinion_ids) != len(set(opinion_ids)):
        raise ValueError("Specialist opinions contain duplicate opinion_id values")
    for opinion in discussion_input.specialist_opinions:
        for claim in opinion.claims:
            validate_pointers(claim.evidence, units)
            for pointer in claim.evidence:
                specialties = units[pointer.graph_unit_id].graph_unit.mdt_specialty
                if (
                    opinion.specialty not in specialties
                    and MdtSpecialty.SHARED_CONTEXT not in specialties
                ):
                    raise ValueError(
                        f"Opinion {opinion.opinion_id} cites unit {pointer.graph_unit_id} "
                        f"outside {opinion.specialty}'s evidence scope"
                    )


def validate_used_opinions(used: list[str], opinions: dict) -> None:
    unknown = set(used) - set(opinions)
    if unknown:
        raise ValueError(f"Unknown specialist_opinions_used: {sorted(unknown)}")
    if len(used) != len(set(used)):
        raise ValueError("specialist_opinions_used contains duplicates")


def validate_chair_question_order(answers: list, questions: list) -> None:
    expected = [item.question_id for item in questions]
    actual = [item.question_id for item in answers]
    if len(actual) != len(set(actual)):
        raise ValueError("Chair answers contain duplicate question_id values")
    if actual != expected:
        raise ValueError("Chair answers must match every chair question in input order")
