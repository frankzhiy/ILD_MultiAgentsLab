import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.agents.semantic_graphing.span_validation import require_non_whitespace_coverage
from src.schemas.semantic_graphing.document import (
    ClassifiedSegment,
    DiscourseUnitType,
    DocumentClassification,
    SourceType,
)
from src.utils.config import load_text, render_template


@dataclass(frozen=True)
class SourceUnit:
    unit_id: int
    text: str
    start_char: int
    end_char: int


class UnitRangeClassifiedSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    end_unit: int = Field(
        ge=1,
        description="Inclusive source-unit number where this segment ends.",
    )
    unit_type: DiscourseUnitType
    contained_source_types: list[SourceType] = Field(default_factory=list)
    clinical_frame: str
    temporal_anchor: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class UnitRangeDocumentClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segments: list[UnitRangeClassifiedSegment] = Field(min_length=1)


class DocumentClassifier:
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

    def classify(self, input_text: str) -> tuple[DocumentClassification, dict]:
        source_units = build_source_units(input_text)
        rendered_units = render_source_units(source_units)
        prompt = render_template(
            self.prompt_template,
            {
                "unit_count": str(len(source_units)),
                "source_units": rendered_units,
            },
        )
        result, trace = self.generator.generate(
            schema_model=UnitRangeDocumentClassification,
            schema_name="document_classification",
            system_prompt="你为临床科研数据处理返回符合 schema 的严格 JSON。",
            user_prompt=prompt,
            extra_validation=lambda result: rebuild_document_classification(
                result,
                input_text,
                source_units,
            ),
        )
        trace["prompt_components"] = {
            "instruction_chars": len(prompt) - len(rendered_units),
            "source_text_chars": len(input_text),
            "rendered_source_unit_chars": len(rendered_units),
            "source_unit_count": len(source_units),
        }
        return result, trace


def build_source_units(input_text: str) -> list[SourceUnit]:
    """Partition the exact source at stable sentence/newline boundaries."""
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r".*?(?:[。！？；;?!\n]+|$)", input_text, flags=re.DOTALL):
        text = match.group(0)
        if not text:
            continue
        if text.strip():
            spans.append((match.start(), match.end()))
        elif spans:
            spans[-1] = (spans[-1][0], match.end())

    if not spans:
        raise ValueError("Cannot classify empty or whitespace-only source text")

    return [
        SourceUnit(
            unit_id=index,
            text=input_text[start:end],
            start_char=start,
            end_char=end,
        )
        for index, (start, end) in enumerate(spans, start=1)
    ]


def render_source_units(source_units: list[SourceUnit]) -> str:
    return "\n".join(
        f"[{unit.unit_id}] {json.dumps(unit.text, ensure_ascii=False)}"
        for unit in source_units
    )


def rebuild_document_classification(
    classification: UnitRangeDocumentClassification,
    input_text: str,
    source_units: list[SourceUnit],
) -> DocumentClassification:
    previous_end_unit = 0
    normalized_segments: list[ClassifiedSegment] = []

    for index, segment in enumerate(classification.segments, start=1):
        if segment.end_unit <= previous_end_unit:
            raise ValueError(
                f"segment {index} end_unit must be greater than {previous_end_unit}; "
                f"got {segment.end_unit}"
            )
        if segment.end_unit > len(source_units):
            raise ValueError(
                f"segment {index} end_unit exceeds source unit count "
                f"{len(source_units)}; got {segment.end_unit}"
            )

        start = source_units[previous_end_unit].start_char
        end = source_units[segment.end_unit - 1].end_char
        while start < end and input_text[start].isspace():
            start += 1
        while end > start and input_text[end - 1].isspace():
            end -= 1

        normalized_segments.append(
            ClassifiedSegment.model_validate(
                {
                    **segment.model_dump(exclude={"end_unit"}),
                    "segment_id": f"seg_{index:03d}",
                    "text": input_text[start:end],
                    "start_char": start,
                    "end_char": end,
                }
            )
        )
        previous_end_unit = segment.end_unit

    if previous_end_unit != len(source_units):
        raise ValueError(
            f"last segment must end at source unit {len(source_units)}; "
            f"got {previous_end_unit}"
        )

    require_non_whitespace_coverage(
        input_text,
        [(item.start_char, item.end_char) for item in normalized_segments],
        label="Discourse segments",
    )

    detected_contained = []
    seen_contained = set()
    for segment in normalized_segments:
        for source_type in segment.contained_source_types:
            if source_type not in seen_contained:
                detected_contained.append(source_type)
                seen_contained.add(source_type)

    normalized = DocumentClassification(
        segments=normalized_segments,
        detected_contained_source_types=detected_contained,
    )
    require_complete_classification_offsets(normalized)
    return normalized


def require_complete_classification_offsets(classification: DocumentClassification) -> None:
    for segment in classification.segments:
        if segment.start_char is None or segment.end_char is None:
            raise ValueError(f"Program-computed offsets are missing for {segment.segment_id}")
