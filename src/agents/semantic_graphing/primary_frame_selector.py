"""Select one primary event-nucleus frame for each graph unit."""

from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.schemas.semantic_graphing import (
    GraphUnit,
    GraphUnitPrimaryFrame,
    render_primary_frame_catalog,
)
from src.utils.config import load_text, render_template


class PrimaryFrameSelector:
    def __init__(
        self,
        llm: LLMClient,
        prompt_path: str,
        *,
        temperature: float,
        max_tokens: int,
    ) -> None:
        self.prompt_template = load_text(prompt_path)
        self.primary_frame_catalog = render_primary_frame_catalog()
        self.generator = StructuredLLMGenerator(
            llm,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format_mode="json_object",
        )

    def select_unit(self, unit: GraphUnit) -> tuple[GraphUnitPrimaryFrame, dict]:
        prompt = render_template(
            self.prompt_template,
            {
                "primary_frame_catalog": self.primary_frame_catalog,
                "graph_unit_id": unit.graph_unit_id,
                "source_type": str(unit.source_type),
                "temporal_anchor": unit.temporal_anchor or "null",
                "clinical_context": unit.clinical_context or "null",
                "unit_text": unit.text,
            },
        )
        return self.generator.generate(
            schema_model=GraphUnitPrimaryFrame,
            schema_name="graph_unit_primary_frame",
            system_prompt=(
                "你是严谨的 ILD primary frame selector，只返回符合 schema 的 JSON。"
            ),
            user_prompt=prompt,
            extra_validation=lambda result: validate_primary_frame_selection(result, unit),
        )


def validate_primary_frame_selection(
    result: GraphUnitPrimaryFrame,
    unit: GraphUnit,
) -> GraphUnitPrimaryFrame:
    if result.graph_unit_id != unit.graph_unit_id:
        raise ValueError(
            f"Primary-frame graph_unit_id {result.graph_unit_id} does not match "
            f"{unit.graph_unit_id}"
        )
    return result
