"""Extract evidence-grounded clinical propositions from one graph unit."""

import json
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.schemas.semantic_graphing import (
    AttributionType,
    ClinicalModifier,
    EvidenceSpan,
    GraphUnit,
    GraphUnitClinicalPropositions,
    GraphUnitPrimaryFrame,
    ModifierType,
    PrimaryFrame,
    PropositionType,
    render_clinical_proposition_catalog,
)
from src.schemas.semantic_graphing.graph_unit import GraphUnitCertainty, GraphUnitStatus
from src.utils.config import load_text, render_template


class UnlocatedEvidenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)


class UnlocatedClinicalAttribution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attribution_type: AttributionType
    actor_text: str = Field(min_length=1)
    source_span: UnlocatedEvidenceSpan


class UnlocatedClinicalModifier(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modifier_id: str = Field(pattern=r"^mod_\d{3,}$")
    modifier_type: ModifierType
    value_text: str = Field(min_length=1)
    source_span: UnlocatedEvidenceSpan


class UnlocatedClinicalProposition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposition_id: str = Field(pattern=r"^prop_\d{3,}$")
    proposition_type: PropositionType
    concept_text: str = Field(min_length=1)
    status: GraphUnitStatus = "unknown"
    certainty: GraphUnitCertainty = "unknown"
    attribution: UnlocatedClinicalAttribution | None = None
    modifiers: list[UnlocatedClinicalModifier] = Field(default_factory=list)
    source_span: UnlocatedEvidenceSpan
    rationale: str = Field(min_length=1)


class UnlocatedGraphUnitClinicalPropositions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    graph_unit_id: str
    primary_frame: PrimaryFrame
    event_modifiers: list[UnlocatedClinicalModifier] = Field(default_factory=list)
    propositions: list[UnlocatedClinicalProposition] = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClinicalPropositionExtractor:
    def __init__(
        self,
        llm: LLMClient,
        prompt_path: str,
        *,
        temperature: float,
        max_tokens: int,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.0,
        max_chunk_chars: int = 300,
        enable_chunking: bool = False,
    ) -> None:
        self.prompt_template = load_text(prompt_path)
        self.clinical_proposition_catalog = render_clinical_proposition_catalog()
        self.max_chunk_chars = max_chunk_chars
        self.enable_chunking = enable_chunking
        self.generator = StructuredLLMGenerator(
            llm,
            temperature=temperature,
            max_tokens=max_tokens,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
            response_format_mode=(
                "json_schema" if getattr(llm, "supports_json_schema", False) else "json_object"
            ),
        )

    def extract_unit(
        self,
        unit: GraphUnit,
        primary_frame: GraphUnitPrimaryFrame,
        chunk_cache_dir: str | Path | None = None,
    ) -> tuple[GraphUnitClinicalPropositions, dict]:
        chunks = (
            split_dense_unit_text(unit.text, self.max_chunk_chars)
            if self.enable_chunking
            else [(0, unit.text)]
        )
        if len(chunks) == 1:
            return self._extract_chunk(unit, primary_frame)

        chunk_results = []
        chunk_traces = []
        for chunk_index, (chunk_start, chunk_text) in enumerate(chunks, start=1):
            cached = _read_chunk_cache(chunk_cache_dir, unit.graph_unit_id, chunk_index)
            if cached is not None:
                result, trace = cached
                chunk_results.append(result)
                chunk_traces.append(trace)
                continue
            chunk_unit = unit.model_copy(update={"text": chunk_text})
            result, trace = self._extract_chunk(chunk_unit, primary_frame)
            shifted = _shift_result_spans(result, chunk_start)
            chunk_trace = {
                "chunk_index": chunk_index,
                "start_char": chunk_start,
                "text_length": len(chunk_text),
                "trace": trace,
            }
            chunk_results.append(shifted)
            chunk_traces.append(chunk_trace)
            _write_chunk_cache(
                chunk_cache_dir,
                unit.graph_unit_id,
                chunk_index,
                shifted,
                chunk_trace,
            )

        merged = _merge_chunk_results(chunk_results, unit.graph_unit_id, primary_frame)
        return validate_clinical_propositions(merged, unit, primary_frame), {
            "chunked": True,
            "chunks": chunk_traces,
        }

    def _extract_chunk(
        self,
        unit: GraphUnit,
        primary_frame: GraphUnitPrimaryFrame,
    ) -> tuple[GraphUnitClinicalPropositions, dict]:
        prompt = render_template(
            self.prompt_template,
            {
                "graph_unit_id": unit.graph_unit_id,
                "primary_frame": str(primary_frame.primary_frame),
                "clinical_proposition_catalog": self.clinical_proposition_catalog,
                "unit_text": unit.text,
            },
        )
        return self.generator.generate(
            schema_model=UnlocatedGraphUnitClinicalPropositions,
            schema_name="graph_unit_clinical_propositions",
            system_prompt=(
                "你是严谨的 ILD clinical proposition extraction agent，"
                "只返回符合 schema 的 JSON。"
            ),
            user_prompt=prompt,
            extra_validation=lambda result: validate_clinical_propositions(
                normalize_clinical_proposition_spans(result, unit),
                unit,
                primary_frame,
            ),
        )


def split_dense_unit_text(text: str, max_chunk_chars: int) -> list[tuple[int, str]]:
    """Split only at strong discourse boundaries so modifier ownership is not weakened."""

    if len(text) <= max_chunk_chars or len(re.findall(r"[,，;；]", text)) < 8:
        return [(0, text)]

    chunks: list[tuple[int, str]] = []
    start = 0
    while len(text) - start > max_chunk_chars:
        window_end = start + max_chunk_chars
        boundaries = [
            match.end()
            for match in re.finditer(r"[。；;\n]", text[start:window_end])
            if match.end() >= max_chunk_chars // 2
        ]
        if not boundaries:
            return [(0, text)]
        end = start + boundaries[-1]
        chunks.append((start, text[start:end]))
        start = end
    if start < len(text):
        chunks.append((start, text[start:]))
    return chunks


def _shift_result_spans(
    result: GraphUnitClinicalPropositions,
    offset: int,
) -> GraphUnitClinicalPropositions:
    def shift_span(span: EvidenceSpan) -> EvidenceSpan:
        if span.start_char is None or span.end_char is None:
            raise ValueError("Cannot shift an evidence span before offsets are computed")
        return span.model_copy(
            update={"start_char": span.start_char + offset, "end_char": span.end_char + offset}
        )

    def shift_modifier(modifier: ClinicalModifier) -> ClinicalModifier:
        return modifier.model_copy(update={"source_span": shift_span(modifier.source_span)})

    propositions = []
    for proposition in result.propositions:
        attribution = proposition.attribution
        if attribution is not None:
            attribution = attribution.model_copy(
                update={"source_span": shift_span(attribution.source_span)}
            )
        propositions.append(
            proposition.model_copy(
                update={
                    "source_span": shift_span(proposition.source_span),
                    "modifiers": [shift_modifier(item) for item in proposition.modifiers],
                    "attribution": attribution,
                }
            )
        )
    return result.model_copy(
        update={
            "event_modifiers": [shift_modifier(item) for item in result.event_modifiers],
            "propositions": propositions,
        }
    )


def _merge_chunk_results(
    results: list[GraphUnitClinicalPropositions],
    graph_unit_id: str,
    primary_frame: GraphUnitPrimaryFrame,
) -> GraphUnitClinicalPropositions:
    modifier_index = 1

    def renumber_modifier(modifier: ClinicalModifier) -> ClinicalModifier:
        nonlocal modifier_index
        updated = modifier.model_copy(update={"modifier_id": f"mod_{modifier_index:03d}"})
        modifier_index += 1
        return updated

    event_modifiers = [
        renumber_modifier(modifier)
        for result in results
        for modifier in result.event_modifiers
    ]
    propositions = []
    for proposition_index, proposition in enumerate(
        (item for result in results for item in result.propositions),
        start=1,
    ):
        propositions.append(
            proposition.model_copy(
                update={
                    "proposition_id": f"prop_{proposition_index:03d}",
                    "modifiers": [renumber_modifier(item) for item in proposition.modifiers],
                }
            )
        )
    return GraphUnitClinicalPropositions(
        graph_unit_id=graph_unit_id,
        primary_frame=primary_frame.primary_frame,
        event_modifiers=event_modifiers,
        propositions=propositions,
        notes=[note for result in results for note in result.notes],
        metadata={"chunk_count": len(results)},
    )


def _read_chunk_cache(
    cache_dir: str | Path | None,
    graph_unit_id: str,
    chunk_index: int,
) -> tuple[GraphUnitClinicalPropositions, dict] | None:
    if cache_dir is None:
        return None
    path = Path(cache_dir) / f"{graph_unit_id}_chunk_{chunk_index:03d}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return GraphUnitClinicalPropositions.model_validate(data["result"]), data["trace"]


def _write_chunk_cache(
    cache_dir: str | Path | None,
    graph_unit_id: str,
    chunk_index: int,
    result: GraphUnitClinicalPropositions,
    trace: dict,
) -> None:
    if cache_dir is None:
        return
    path = Path(cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{graph_unit_id}_chunk_{chunk_index:03d}.json").write_text(
        json.dumps({"result": result.model_dump(), "trace": trace}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def normalize_clinical_proposition_spans(
    result: UnlocatedGraphUnitClinicalPropositions | GraphUnitClinicalPropositions,
    unit: GraphUnit,
) -> GraphUnitClinicalPropositions:
    """Compute all evidence offsets from exact source text instead of trusting the model."""

    data = result.model_dump()
    proposition_cursor = 0
    for proposition in data["propositions"]:
        proposition["source_span"] = _locate_span_data(
            proposition["source_span"]["text"],
            unit.text,
            cursor=proposition_cursor,
            owner=proposition["proposition_id"],
        )
        proposition_cursor = proposition["source_span"]["end_char"]
        for modifier in proposition["modifiers"]:
            modifier["source_span"] = _locate_span_data(
                modifier["source_span"]["text"],
                unit.text,
                cursor=proposition["source_span"]["start_char"],
                end=proposition["source_span"]["end_char"],
                owner=modifier["modifier_id"],
            )
        attribution = proposition["attribution"]
        if attribution is not None:
            attribution["source_span"] = _locate_span_data(
                attribution["source_span"]["text"],
                unit.text,
                cursor=proposition["source_span"]["start_char"],
                end=proposition["source_span"]["end_char"],
                owner=f"{proposition['proposition_id']}.attribution",
            )

    for modifier in data["event_modifiers"]:
        modifier["source_span"] = _locate_span_data(
            modifier["source_span"]["text"],
            unit.text,
            owner=modifier["modifier_id"],
        )
    return GraphUnitClinicalPropositions.model_validate(data)


def _locate_span_data(
    span_text: str,
    source_text: str,
    *,
    cursor: int = 0,
    end: int | None = None,
    owner: str,
) -> EvidenceSpan:
    boundary = len(source_text) if end is None else end
    start = source_text.find(span_text, cursor, boundary)
    if start == -1:
        start = source_text.find(span_text)
    if start != -1:
        return {"text": span_text, "start_char": start, "end_char": start + len(span_text)}

    if owner.endswith(".attribution"):
        proposition_id = owner.removesuffix(".attribution")
        raise ValueError(
            f"Attribution for {proposition_id} is not explicitly grounded in the current "
            f"graph unit: source_span.text {span_text!r} is not an exact continuous substring. "
            "Attribution represents an explicitly stated information source, not an implicit "
            "proposition subject; set attribution to null when the source is only implicit."
        )
    if owner.startswith("prop_"):
        raise ValueError(
            f"Proposition evidence for {owner} cannot be located: source_span.text "
            f"{span_text!r} is not an exact continuous substring of the current graph unit. "
            "concept_text may be a normalized or coordination-expanded clinical statement, but "
            "source_span.text must quote the complete continuous source evidence that supports "
            "it. Select that verbatim evidence span instead of copying concept_text."
        )
    raise ValueError(
        f"Evidence for {owner} cannot be located: source_span.text {span_text!r} is not an exact "
        "continuous substring of the current graph unit. Select the exact continuous source "
        "text that expresses this item."
    )


def require_complete_clinical_proposition_offsets(
    result: GraphUnitClinicalPropositions,
) -> GraphUnitClinicalPropositions:
    for modifier in result.event_modifiers:
        _require_span_offsets(modifier.source_span, modifier.modifier_id)
    for proposition in result.propositions:
        _require_span_offsets(proposition.source_span, proposition.proposition_id)
        if proposition.attribution is not None:
            _require_span_offsets(
                proposition.attribution.source_span,
                f"{proposition.proposition_id}.attribution",
            )
        for modifier in proposition.modifiers:
            _require_span_offsets(modifier.source_span, modifier.modifier_id)
    return result


def _require_span_offsets(span: EvidenceSpan, owner: str) -> None:
    if span.start_char is None or span.end_char is None:
        raise ValueError(f"Program-computed evidence offsets are missing for {owner}")


def validate_clinical_propositions(
    result: GraphUnitClinicalPropositions,
    unit: GraphUnit,
    primary_frame: GraphUnitPrimaryFrame,
) -> GraphUnitClinicalPropositions:
    """Validate identity, evidence spans, and ownership within one unit."""

    if result.graph_unit_id != unit.graph_unit_id:
        raise ValueError(
            f"Clinical-proposition graph_unit_id {result.graph_unit_id} does not match "
            f"{unit.graph_unit_id}"
        )
    if result.primary_frame != primary_frame.primary_frame:
        raise ValueError(
            f"Clinical-proposition primary_frame {result.primary_frame} does not match "
            f"{primary_frame.primary_frame}"
        )

    proposition_ids: set[str] = set()
    modifier_ids: set[str] = set()
    for modifier in result.event_modifiers:
        _validate_modifier(modifier, unit.text, modifier_ids)

    for proposition in result.propositions:
        if proposition.proposition_id in proposition_ids:
            raise ValueError(f"Duplicate proposition_id: {proposition.proposition_id}")
        proposition_ids.add(proposition.proposition_id)
        _validate_span(proposition.source_span, unit.text, proposition.proposition_id)
        if proposition.attribution is not None:
            _validate_span(
                proposition.attribution.source_span,
                unit.text,
                f"{proposition.proposition_id}.attribution",
            )
            if proposition.attribution.actor_text not in proposition.attribution.source_span.text:
                raise ValueError(
                    f"Attribution for {proposition.proposition_id} is invalid: actor_text must "
                    "occur inside its attribution source_span. Attribution represents an "
                    "explicitly stated information source, not an implicit proposition subject; "
                    "set attribution to null when the source is only implicit."
                )
        for modifier in proposition.modifiers:
            _validate_modifier(modifier, unit.text, modifier_ids)

    return require_complete_clinical_proposition_offsets(result)


def _validate_modifier(
    modifier: ClinicalModifier,
    unit_text: str,
    seen_ids: set[str],
) -> None:
    if modifier.modifier_id in seen_ids:
        raise ValueError(f"Duplicate modifier_id: {modifier.modifier_id}")
    seen_ids.add(modifier.modifier_id)
    _validate_span(modifier.source_span, unit_text, modifier.modifier_id)


def _validate_span(span: EvidenceSpan, unit_text: str, owner: str) -> None:
    if span.start_char is None or span.end_char is None:
        raise ValueError(f"Evidence span offsets were not computed for {owner}")
    if span.end_char > len(unit_text):
        raise ValueError(
            f"Evidence span for {owner} ends at {span.end_char}, beyond unit length {len(unit_text)}"
        )
    actual = unit_text[span.start_char : span.end_char]
    if actual != span.text:
        raise ValueError(
            f"Evidence span mismatch for {owner}: offsets resolve to {actual!r}, "
            f"but span text is {span.text!r}"
        )
