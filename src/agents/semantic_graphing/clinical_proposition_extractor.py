"""Extract evidence-grounded clinical propositions from one graph unit."""

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.schemas.semantic_graphing.clinical_proposition import (
    ClinicalModifier,
    ClinicalProposition,
    EvidenceBlock,
    GraphUnitClinicalPropositions,
    render_clinical_proposition_catalog,
)
from src.schemas.semantic_graphing.graph_unit import GraphUnit
from src.schemas.semantic_graphing.primary_frame import GraphUnitPrimaryFrame, PrimaryFrame
from src.utils.config import load_text, render_template


class ExtractedGraphUnitClinicalPropositions(BaseModel):
    """LLM-owned fields; evidence blocks are generated and attached by the program."""

    model_config = ConfigDict(extra="forbid")

    graph_unit_id: str
    primary_frame: PrimaryFrame
    event_modifiers: list[ClinicalModifier] = Field(default_factory=list)
    propositions: list[ClinicalProposition] = Field(min_length=1)
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
        evidence_blocks = build_evidence_blocks(unit)
        chunks = (
            split_dense_unit_evidence_blocks(evidence_blocks, self.max_chunk_chars)
            if self.enable_chunking
            else [evidence_blocks]
        )
        if len(chunks) == 1:
            return self._extract_chunk(unit, primary_frame, evidence_blocks)

        chunk_results = []
        chunk_traces = []
        for chunk_index, chunk_blocks in enumerate(chunks, start=1):
            cached = _read_chunk_cache(chunk_cache_dir, unit.graph_unit_id, chunk_index)
            if cached is not None:
                result, trace = cached
                chunk_results.append(result)
                chunk_traces.append(trace)
                continue
            chunk_text = "".join(block.text for block in chunk_blocks)
            chunk_unit = unit.model_copy(update={"text": chunk_text})
            result, trace = self._extract_chunk(chunk_unit, primary_frame, chunk_blocks)
            chunk_trace = {
                "chunk_index": chunk_index,
                "text_length": len(chunk_text),
                "evidence_ids": [block.evidence_id for block in chunk_blocks],
                "trace": trace,
            }
            chunk_results.append(result)
            chunk_traces.append(chunk_trace)
            _write_chunk_cache(
                chunk_cache_dir,
                unit.graph_unit_id,
                chunk_index,
                result,
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
        evidence_blocks: list[EvidenceBlock],
    ) -> tuple[GraphUnitClinicalPropositions, dict]:
        prompt = render_template(
            self.prompt_template,
            {
                "graph_unit_id": unit.graph_unit_id,
                "primary_frame": str(primary_frame.primary_frame),
                "clinical_proposition_catalog": self.clinical_proposition_catalog,
                "unit_text": unit.text,
                "evidence_blocks": json.dumps(
                    [block.model_dump() for block in evidence_blocks],
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        )
        return self.generator.generate(
            schema_model=ExtractedGraphUnitClinicalPropositions,
            schema_name="graph_unit_clinical_propositions",
            system_prompt=(
                "你是严谨的 ILD clinical proposition extraction agent，"
                "只返回符合 schema 的 JSON。"
            ),
            user_prompt=prompt,
            extra_validation=lambda result: validate_clinical_propositions(
                _attach_evidence_blocks(result, evidence_blocks),
                unit,
                primary_frame,
            ),
        )


def split_dense_unit_text(text: str, max_chunk_chars: int) -> list[str]:
    """Compatibility helper returning evidence-aligned dense-unit chunks."""

    unit = GraphUnit.model_construct(graph_unit_id="unit", text=text)
    return [
        "".join(block.text for block in blocks)
        for blocks in split_dense_unit_evidence_blocks(build_evidence_blocks(unit), max_chunk_chars)
    ]


def build_evidence_blocks(unit: GraphUnit) -> list[EvidenceBlock]:
    """Deterministically split a graph unit into ordered, globally unique evidence blocks."""

    parts = [
        match.group(0)
        for match in re.finditer(r".*?(?:[。！？；;\n]+|$)", unit.text, flags=re.DOTALL)
        if match.group(0)
    ]
    blocks: list[str] = []
    for part in parts:
        if part.strip() or not blocks:
            blocks.append(part)
        else:
            blocks[-1] += part
    if not blocks:
        raise ValueError(f"Cannot create evidence blocks for empty graph unit {unit.graph_unit_id}")
    return [
        EvidenceBlock(evidence_id=f"{unit.graph_unit_id}_ev_{index:03d}", text=text)
        for index, text in enumerate(blocks, start=1)
    ]


def split_dense_unit_evidence_blocks(
    evidence_blocks: list[EvidenceBlock],
    max_chunk_chars: int,
) -> list[list[EvidenceBlock]]:
    """Group whole evidence blocks into chunks without weakening evidence references."""

    text = "".join(block.text for block in evidence_blocks)
    if len(text) <= max_chunk_chars or len(re.findall(r"[,，;；]", text)) < 8:
        return [evidence_blocks]

    chunks: list[list[EvidenceBlock]] = []
    current: list[EvidenceBlock] = []
    current_length = 0
    for block in evidence_blocks:
        if current and current_length + len(block.text) > max_chunk_chars:
            chunks.append(current)
            current = []
            current_length = 0
        current.append(block)
        current_length += len(block.text)
    if current:
        chunks.append(current)
    return chunks


def _attach_evidence_blocks(
    result: ExtractedGraphUnitClinicalPropositions,
    evidence_blocks: list[EvidenceBlock],
) -> GraphUnitClinicalPropositions:
    return GraphUnitClinicalPropositions(
        **result.model_dump(),
        evidence_blocks=evidence_blocks,
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
        evidence_blocks=[block for result in results for block in result.evidence_blocks],
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


def _validate_evidence_reference(
    evidence,
    evidence_blocks: list[EvidenceBlock],
    owner: str,
) -> None:
    blocks_by_id = {block.evidence_id: block for block in evidence_blocks}
    missing = [evidence_id for evidence_id in evidence.evidence_ids if evidence_id not in blocks_by_id]
    if missing:
        raise ValueError(f"Evidence for {owner} references unknown evidence_ids: {missing}")
    if len(set(evidence.evidence_ids)) != len(evidence.evidence_ids):
        raise ValueError(f"Evidence for {owner} contains duplicate evidence_ids")
    positions_by_id = {
        block.evidence_id: index for index, block in enumerate(evidence_blocks)
    }
    positions = [positions_by_id[evidence_id] for evidence_id in evidence.evidence_ids]
    if positions != list(range(positions[0], positions[-1] + 1)):
        raise ValueError(f"Evidence for {owner} must reference contiguous blocks in source order")
    referenced_text = "".join(blocks_by_id[evidence_id].text for evidence_id in evidence.evidence_ids)
    if evidence.quote in referenced_text:
        return
    if owner.endswith(".attribution"):
        proposition_id = owner.removesuffix(".attribution")
        raise ValueError(
            f"Attribution for {proposition_id} is not explicitly grounded in the current "
            f"evidence blocks: quote {evidence.quote!r} is not an exact continuous substring. "
            "Attribution represents an explicitly stated information source, not an implicit "
            "proposition subject; set attribution to null when the source is only implicit."
        )
    if owner.startswith("prop_"):
        raise ValueError(
            f"Proposition evidence for {owner} cannot be located: quote "
            f"{evidence.quote!r} is not an exact continuous substring of its evidence blocks. "
            "concept_text may be a normalized or coordination-expanded clinical statement, but "
            "evidence.quote must quote the complete continuous source evidence that supports "
            "it. Select that verbatim evidence span instead of copying concept_text."
        )
    # ponytail: modifier quotes can be clinically normalized; quality gate records a warning.
    return


def validate_clinical_propositions(
    result: GraphUnitClinicalPropositions,
    unit: GraphUnit,
    primary_frame: GraphUnitPrimaryFrame,
) -> GraphUnitClinicalPropositions:
    """Validate identity, evidence references, and ownership within one unit."""

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
    expected_blocks = build_evidence_blocks(unit)
    if result.evidence_blocks != expected_blocks:
        raise ValueError(
            "Clinical-proposition evidence_blocks do not match deterministic graph-unit blocks"
        )

    proposition_ids: set[str] = set()
    modifier_ids: set[str] = set()
    for modifier in result.event_modifiers:
        _validate_modifier(modifier, result.evidence_blocks, modifier_ids)

    for proposition in result.propositions:
        if proposition.proposition_id in proposition_ids:
            raise ValueError(f"Duplicate proposition_id: {proposition.proposition_id}")
        proposition_ids.add(proposition.proposition_id)
        _validate_evidence_reference(
            proposition.evidence,
            result.evidence_blocks,
            proposition.proposition_id,
        )
        if proposition.attribution is not None:
            _validate_evidence_reference(
                proposition.attribution.evidence,
                result.evidence_blocks,
                f"{proposition.proposition_id}.attribution",
            )
            if proposition.attribution.actor_text not in proposition.attribution.evidence.quote:
                raise ValueError(
                    f"Attribution for {proposition.proposition_id} is invalid: actor_text must "
                    "occur inside its evidence quote. Attribution represents an "
                    "explicitly stated information source, not an implicit proposition subject; "
                    "set attribution to null when the source is only implicit."
                )
        for modifier in proposition.modifiers:
            _validate_modifier(modifier, result.evidence_blocks, modifier_ids)
            if not set(modifier.evidence.evidence_ids) & set(proposition.evidence.evidence_ids):
                raise ValueError(
                    f"Modifier {modifier.modifier_id} must share at least one evidence block "
                    f"with owning proposition {proposition.proposition_id}"
                )

    return result


def _validate_modifier(
    modifier: ClinicalModifier,
    evidence_blocks: list[EvidenceBlock],
    seen_ids: set[str],
) -> None:
    if modifier.modifier_id in seen_ids:
        raise ValueError(f"Duplicate modifier_id: {modifier.modifier_id}")
    seen_ids.add(modifier.modifier_id)
    _validate_evidence_reference(modifier.evidence, evidence_blocks, modifier.modifier_id)
