"""Stage 1 of segment graph construction: frame triage.

Reads each graph unit's own raw text and decides which event frames it triggers.
source_type is only a weak hint; segment-level contained_source_types is forbidden.
"""

from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.schemas.semantic_graphing import (
    GraphFrame,
    GraphUnit,
    GraphUnitFrameTriage,
    SegmentFrameTriage,
    SegmentGraphUnits,
    render_frame_catalog,
)
from src.utils.config import load_text, render_template


class FrameTriager:
    def __init__(
        self,
        llm: LLMClient,
        prompt_path: str,
        *,
        temperature: float,
        max_tokens: int,
    ) -> None:
        self.llm = llm
        self.prompt_template = load_text(prompt_path)
        self.frame_catalog = render_frame_catalog()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.generator = StructuredLLMGenerator(
            llm,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format_mode="json_object",
        )

    def triage_unit(self, unit: GraphUnit) -> tuple[GraphUnitFrameTriage, dict]:
        prompt = render_template(
            self.prompt_template,
            {
                "frame_catalog": self.frame_catalog,
                "graph_unit_id": unit.graph_unit_id,
                "source_type": str(unit.source_type),
                "temporal_anchor": unit.temporal_anchor or "null",
                "unit_text": unit.text,
            },
        )
        return self.generator.generate(
            schema_model=GraphUnitFrameTriage,
            schema_name="graph_unit_frame_triage",
            system_prompt=(
                "你是严谨的 ILD frame triage agent，只返回符合 schema 的 JSON。"
            ),
            user_prompt=prompt,
            extra_validation=lambda result: validate_unit_triage(result, unit),
        )

    def triage_segment(self, segment: SegmentGraphUnits) -> tuple[SegmentFrameTriage, dict]:
        unit_results: list[GraphUnitFrameTriage] = []
        unit_traces: list[dict] = []
        for unit in segment.graph_units:
            triage, trace = self.triage_unit(unit)
            unit_results.append(triage)
            unit_traces.append({"graph_unit_id": unit.graph_unit_id, "trace": trace})

        result = SegmentFrameTriage(segment_id=segment.segment_id, units=unit_results)
        return result, {"segment_id": segment.segment_id, "units": unit_traces}


def validate_unit_triage(
    result: GraphUnitFrameTriage,
    unit: GraphUnit,
) -> GraphUnitFrameTriage:
    if result.graph_unit_id != unit.graph_unit_id:
        raise ValueError(
            f"Triage graph_unit_id {result.graph_unit_id} does not match {unit.graph_unit_id}"
        )

    if not result.triggered_frames:
        raise ValueError(f"Graph unit {unit.graph_unit_id} must trigger at least one frame")

    allowed = {frame for frame in GraphFrame}
    seen: set[GraphFrame] = set()
    deduped = []
    for triaged in result.triggered_frames:
        if triaged.frame not in allowed:
            raise ValueError(
                f"Frame {triaged.frame} for {unit.graph_unit_id} is not in the controlled enum"
            )
        if triaged.frame in seen:
            continue
        seen.add(triaged.frame)
        deduped.append(triaged)

    return result.model_copy(update={"triggered_frames": deduped})
