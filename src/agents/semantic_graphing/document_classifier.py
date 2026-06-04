from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.schemas.semantic_graphing import DocumentClassification
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
        )

    def classify(self, input_text: str) -> tuple[DocumentClassification, dict]:
        prompt = render_template(self.prompt_template, {"input_text": input_text})
        return self.generator.generate(
            schema_model=DocumentClassification,
            schema_name="document_classification",
            system_prompt="你为临床科研数据处理返回符合 schema 的严格 JSON。",
            user_prompt=prompt,
        )
