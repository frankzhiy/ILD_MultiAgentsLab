from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.schemas.semantic_graphing import ClassifiedSegment, SegmentGraphUnits
from src.utils.config import load_text, render_template


class SegmentGraphUnitExtractor:
    def __init__(
        self,
        llm: LLMClient,
        prompt_path: str,
        *,
        temperature: float,
        max_tokens: int,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.0,
    ) -> None:
        self.llm = llm
        self.prompt_template = load_text(prompt_path)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.generator = StructuredLLMGenerator(
            llm,
            temperature=temperature,
            max_tokens=max_tokens,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
            response_format_mode="json_object",
        )

    def extract(self, segment: ClassifiedSegment) -> tuple[SegmentGraphUnits, dict]:
        prompt = render_template(
            self.prompt_template,
            {
                "segment_id": segment.segment_id,
                "unit_type": str(segment.unit_type),
                "contained_source_types": ", ".join(
                    str(item) for item in segment.contained_source_types
                ),
                "clinical_frame": segment.clinical_frame,
                "temporal_anchor": segment.temporal_anchor or "null",
                "rationale": segment.rationale,
                "segment_text": segment.text,
            },
        )
        return self.generator.generate(
            schema_model=SegmentGraphUnits,
            schema_name="segment_graph_units",
            system_prompt="你是严谨的 ILD graph-unit extraction agent，只返回符合 schema 的 JSON。",
            user_prompt=prompt,
            extra_validation=lambda result: normalize_and_validate_graph_units(
                result,
                segment,
            ),
        )


def normalize_and_validate_graph_units(
    result: SegmentGraphUnits,
    segment: ClassifiedSegment,
) -> SegmentGraphUnits:
    if result.segment_id != segment.segment_id:
        raise ValueError(
            f"Graph-unit result segment_id {result.segment_id} does not match {segment.segment_id}"
        )

    cursor = 0
    normalized_units = []
    unmatched: list[str] = []

    for index, unit in enumerate(result.graph_units, start=1):
        if unit.segment_id != segment.segment_id:
            raise ValueError(
                f"Graph unit {unit.graph_unit_id} segment_id {unit.segment_id} "
                f"does not match {segment.segment_id}"
            )

        expected_prefix = f"{segment.segment_id}_gu_"
        if not unit.graph_unit_id.startswith(expected_prefix):
            raise ValueError(
                f"Graph unit id {unit.graph_unit_id} must start with {expected_prefix}"
            )

        text = unit.text
        start = segment.text.find(text, cursor)
        if start == -1 and text.strip():
            text = text.strip()
            start = segment.text.find(text, cursor)
        if start == -1:
            unmatched.append(unit.graph_unit_id)
            continue

        end = start + len(text)
        normalized_units.append(
            unit.model_copy(
                update={
                    "graph_unit_id": f"{segment.segment_id}_gu_{index:03d}",
                    "text": text,
                    "segment_start_char": start,
                    "segment_end_char": end,
                    "start_char": None if segment.start_char is None else segment.start_char + start,
                    "end_char": None if segment.start_char is None else segment.start_char + end,
                }
            )
        )
        cursor = end

    if unmatched:
        raise ValueError(
            "The following graph units are not exact continuous substrings of "
            f"{segment.segment_id}: " + ", ".join(unmatched)
        )

    for previous, current in zip(normalized_units, normalized_units[1:]):
        if current.segment_start_char is None or previous.segment_end_char is None:
            continue
        if current.segment_start_char < previous.segment_end_char:
            raise ValueError(
                "Graph units overlap or are out of order: "
                f"{previous.graph_unit_id}, {current.graph_unit_id}"
            )

    normalized = result.model_copy(update={"graph_units": normalized_units})
    require_complete_graph_unit_offsets(normalized)
    return normalized


def require_complete_graph_unit_offsets(result: SegmentGraphUnits) -> None:
    for unit in result.graph_units:
        offsets = (
            unit.start_char,
            unit.end_char,
            unit.segment_start_char,
            unit.segment_end_char,
        )
        if any(value is None for value in offsets):
            raise ValueError(f"Program-computed offsets are missing for {unit.graph_unit_id}")
