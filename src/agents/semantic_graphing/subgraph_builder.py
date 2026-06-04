from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.schemas.graph import SemanticSubgraph
from src.schemas.semantic_graphing import ClassifiedSegment, ModalityGraphSchema
from src.utils.config import load_text, render_template
from src.utils.json_utils import to_pretty_json


class SubgraphBuilder:
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
        )

    def build(
        self,
        segment: ClassifiedSegment,
        modality_schema: ModalityGraphSchema,
    ) -> tuple[SemanticSubgraph, dict]:
        prompt = render_template(
            self.prompt_template,
            {
                "source_type": str(segment.source_type),
                "segment_id": segment.segment_id,
                "segment_text": segment.text,
                "modality_schema_json": to_pretty_json(modality_schema.model_dump()),
            },
        )
        return self.generator.generate(
            schema_model=SemanticSubgraph,
            schema_name="semantic_subgraph",
            system_prompt="你是严谨的 ILD semantic graph construction agent，只返回符合 schema 的 JSON。",
            user_prompt=prompt,
        )
