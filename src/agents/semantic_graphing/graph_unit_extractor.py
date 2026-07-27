import re

from pydantic import BaseModel, ConfigDict, Field

from src.agents.semantic_graphing.span_validation import require_non_whitespace_coverage
from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.schemas.semantic_graphing.document import ClassifiedSegment, SourceType
from src.schemas.semantic_graphing.graph_unit import (
    GraphUnit,
    GraphUnitCertainty,
    GraphUnitStatus,
    MdtSpecialty,
    SegmentGraphUnits,
)
from src.schemas.semantic_graphing.primary_frame import PrimaryFrame, render_primary_frame_catalog
from src.utils.config import load_text, render_template


_THORACIC_MODALITY_RE = re.compile(
    r"(?:HRCT|CTPA|肺动脉CT|胸部CT|肺部CT|肺CT|胸片|胸部X线|高分辨率CT)",
    re.IGNORECASE,
)
_GENERIC_CT_WITH_CHEST_RE = re.compile(
    r"(?:CT|X线|影像).{0,30}(?:双肺|肺野|肺叶|肺段|胸膜|纵隔|肺门|支气管|肺动脉|胸腔)|"
    r"(?:双肺|肺野|肺叶|肺段|胸膜|纵隔|肺门|支气管|肺动脉|胸腔).{0,30}(?:CT|X线|影像)",
    re.IGNORECASE,
)
_NON_THORACIC_TEST_RE = re.compile(
    r"(?:肺功能|FEV1|FVC|DLCO|超声心动图|心脏彩超|下肢(?:动脉|静脉|血管)?超声|"
    r"双下肢|腹部彩超|甲状腺超声|关节超声)",
    re.IGNORECASE,
)
_SAFE_BOUNDARY_CHARS = frozenset(
    ",，。.;；:：、!?！？\"'“”‘’()（）[]【】{}《》〈〉"
)


class ExtractedGraphUnit(BaseModel):
    """Only fields that require clinical judgment belong to the LLM response."""

    model_config = ConfigDict(extra="forbid")

    text: str
    source_type: SourceType
    mdt_specialty: list[MdtSpecialty] = Field(min_length=1)
    temporal_anchor: str | None = None
    clinical_context: str | None = None
    primary_frame: PrimaryFrame
    primary_frame_rationale: str = Field(min_length=1)
    boundary_warning: str | None = None
    status: GraphUnitStatus = "unknown"
    certainty: GraphUnitCertainty = "unknown"
    rationale: str = Field(min_length=1)


class ExtractedSegmentGraphUnits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_units: list[ExtractedGraphUnit] = Field(min_length=1)


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
        self.primary_frame_catalog = render_primary_frame_catalog()
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
                "primary_frame_catalog": self.primary_frame_catalog,
                "segment_text": segment.text,
            },
        )
        result, trace = self.generator.generate(
            schema_model=ExtractedSegmentGraphUnits,
            schema_name="segment_graph_units",
            system_prompt="你是严谨的 ILD graph-unit extraction agent，只返回符合 schema 的 JSON。",
            user_prompt=prompt,
            extra_validation=lambda result: normalize_extracted_graph_units(result, segment),
        )
        trace["prompt_components"] = {
            "instruction_and_catalog_chars": len(prompt) - len(segment.text),
            "source_text_chars": len(segment.text),
        }
        return result, trace


def normalize_extracted_graph_units(
    result: ExtractedSegmentGraphUnits,
    segment: ClassifiedSegment,
) -> SegmentGraphUnits:
    graph_units = [
        GraphUnit(
            graph_unit_id=f"{segment.segment_id}_gu_{index:03d}",
            segment_id=segment.segment_id,
            **unit.model_dump(),
        )
        for index, unit in enumerate(result.graph_units, start=1)
    ]
    return normalize_and_validate_graph_units(
        SegmentGraphUnits(
            segment_id=segment.segment_id,
            graph_units=graph_units,
        ),
        segment,
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
            text = _drop_duplicated_boundary_prefix(text, segment.text, cursor)
            start = segment.text.find(text, cursor)
        if start == -1:
            expected = segment.text[cursor : cursor + max(20, len(text))]
            unmatched.append(
                f"{unit.graph_unit_id} text={text!r}, cursor={cursor}, "
                f"source_context={expected!r}"
            )
            continue

        gap = segment.text[cursor:start]
        if gap and _is_safe_boundary_text(gap):
            if normalized_units:
                previous = normalized_units[-1]
                normalized_units[-1] = previous.model_copy(
                    update={
                        "text": previous.text + gap,
                        "segment_end_char": start,
                        "end_char": (
                            None
                            if segment.start_char is None
                            else segment.start_char + start
                        ),
                    }
                )
            else:
                text = gap + text
                start = cursor

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

        _validate_thoracic_radiology_routing(normalized_units[-1])

    if unmatched:
        raise ValueError(
            "The following graph units are not exact continuous substrings of "
            f"{segment.segment_id}: " + "; ".join(unmatched)
        )

    tail = segment.text[cursor:]
    if tail and normalized_units and _is_safe_boundary_text(tail):
        previous = normalized_units[-1]
        normalized_units[-1] = previous.model_copy(
            update={
                "text": previous.text + tail,
                "segment_end_char": len(segment.text),
                "end_char": (
                    None
                    if segment.start_char is None
                    else segment.start_char + len(segment.text)
                ),
            }
        )

    for previous, current in zip(normalized_units, normalized_units[1:]):
        if current.segment_start_char is None or previous.segment_end_char is None:
            continue
        if current.segment_start_char < previous.segment_end_char:
            raise ValueError(
                "Graph units overlap or are out of order: "
                f"{previous.graph_unit_id}, {current.graph_unit_id}"
            )

    require_non_whitespace_coverage(
        segment.text,
        [
            (unit.segment_start_char, unit.segment_end_char)
            for unit in normalized_units
            if unit.segment_start_char is not None and unit.segment_end_char is not None
        ],
        label=f"Graph units in {segment.segment_id}",
    )

    normalized = result.model_copy(update={"graph_units": normalized_units})
    require_complete_graph_unit_offsets(normalized)
    return normalized


def _is_safe_boundary_text(text: str) -> bool:
    return bool(text) and all(
        char.isspace() or char in _SAFE_BOUNDARY_CHARS for char in text
    )


def _drop_duplicated_boundary_prefix(text: str, source: str, cursor: int) -> str:
    max_length = min(len(text) - 1, cursor)
    for length in range(max_length, 0, -1):
        prefix = text[:length]
        if not _is_safe_boundary_text(prefix):
            continue
        candidate = text[length:]
        if source[cursor - length : cursor] == prefix and source.startswith(candidate, cursor):
            return candidate
    return text


def _validate_thoracic_radiology_routing(unit) -> None:
    """Reject the observed false-positive route without guessing ambiguous cases."""

    if MdtSpecialty.THORACIC_RADIOLOGY not in unit.mdt_specialty:
        return
    has_chest_imaging = bool(
        _THORACIC_MODALITY_RE.search(unit.text)
        or _GENERIC_CT_WITH_CHEST_RE.search(unit.text)
    )
    if _NON_THORACIC_TEST_RE.search(unit.text) and not has_chest_imaging:
        raise ValueError(
            f"Graph unit {unit.graph_unit_id} routes non-thoracic tests to "
            "thoracic_radiology without chest CT/HRCT/CTPA/chest-radiograph evidence"
        )


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
