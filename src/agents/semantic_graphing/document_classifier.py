from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.schemas.semantic_graphing import ClassifiedSegment, DocumentClassification
from src.utils.config import load_text, render_template


class DocumentClassifier:
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
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.generator = StructuredLLMGenerator(
            llm,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format_mode="json_object",
        )

    def classify(self, input_text: str) -> tuple[DocumentClassification, dict]:
        prompt = render_template(self.prompt_template, {"input_text": input_text})
        return self.generator.generate(
            schema_model=DocumentClassification,
            schema_name="document_classification",
            system_prompt="你为临床科研数据处理返回符合 schema 的严格 JSON。",
            user_prompt=prompt,
            extra_validation=lambda result: normalize_and_validate_spans(result, input_text),
        )


def normalize_and_validate_spans(
    classification: DocumentClassification,
    input_text: str,
) -> DocumentClassification:
    cursor = 0
    normalized_segments: list[ClassifiedSegment] = []
    unmatched: list[str] = []

    for segment in classification.segments:
        text = segment.text
        start = input_text.find(text, cursor)
        if start == -1 and text.strip():
            text = text.strip()
            start = input_text.find(text, cursor)
        if start == -1:
            unmatched.append(segment.segment_id)
            continue

        end = start + len(text)
        normalized_segments.append(
            segment.model_copy(
                update={
                    "text": text,
                    "start_char": start,
                    "end_char": end,
                }
            )
        )
        cursor = end

    if unmatched:
        raise ValueError(
            "The following segments are not exact continuous substrings of the input text: "
            + ", ".join(unmatched)
        )

    for previous, current in zip(normalized_segments, normalized_segments[1:]):
        if current.start_char < previous.end_char:
            raise ValueError(
                f"Segments overlap or are out of order: {previous.segment_id}, {current.segment_id}"
            )

    detected_contained = []
    seen_contained = set()
    for segment in normalized_segments:
        for source_type in segment.contained_source_types:
            if source_type not in seen_contained:
                detected_contained.append(source_type)
                seen_contained.add(source_type)

    return classification.model_copy(
        update={
            "segments": normalized_segments,
            "detected_contained_source_types": detected_contained,
        }
    )
