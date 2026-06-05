from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from src.llm.base import LLMClient, LLMMessage
from src.utils.json_utils import parse_llm_json

T = TypeVar("T", bound=BaseModel)


def json_schema_response_format(model: type[BaseModel], name: str) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": model.model_json_schema(),
        },
    }


class StructuredLLMGenerator:
    def __init__(
        self,
        llm: LLMClient,
        *,
        temperature: float,
        max_tokens: int,
        max_attempts: int = 3,
        response_format_mode: str = "json_object",
    ) -> None:
        self.llm = llm
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_attempts = max_attempts
        self.response_format_mode = response_format_mode

    def generate(
        self,
        *,
        schema_model: type[T],
        schema_name: str,
        system_prompt: str,
        user_prompt: str,
        extra_validation: Callable[[T], T] | None = None,
    ) -> tuple[T, dict]:
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        attempts: list[dict] = []
        response_format = self._initial_response_format(schema_model, schema_name)

        last_error = None
        for attempt_index in range(1, self.max_attempts + 1):
            try:
                response = self.llm.complete(
                    messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    response_format=response_format,
                )
            except RuntimeError as exc:
                if response_format and response_format.get("type") == "json_schema":
                    response_format = {"type": "json_object"}
                    last_error = exc
                    attempts.append(
                        {
                            "attempt": attempt_index,
                            "transport_error": str(exc),
                            "fallback": "json_object",
                        }
                    )
                    continue
                raise
            attempt_record = {
                "attempt": attempt_index,
                "response_format": response_format.get("type") if response_format else None,
                "raw_response": response.raw,
                "content": response.content,
            }
            try:
                parsed = parse_llm_json(response.content)
                validated = schema_model.model_validate(parsed)
                if extra_validation:
                    validated = extra_validation(validated)
                attempt_record["validated"] = True
                attempts.append(attempt_record)
                return validated, {"prompt": user_prompt, "attempts": attempts}
            except (ValueError, ValidationError) as exc:
                last_error = exc
                attempt_record["validated"] = False
                attempt_record["validation_error"] = str(exc)
                attempts.append(attempt_record)
                messages = [
                    LLMMessage(
                        role="system",
                        content=system_prompt,
                    ),
                    LLMMessage(
                        role="user",
                        content=(
                            f"{user_prompt}\n\n"
                            "上一次输出没有通过程序校验。请只返回修正后的 JSON，"
                            "不要解释，不要使用 Markdown。\n\n"
                            f"校验错误：\n{exc}\n\n"
                            f"上一次输出：\n{response.content}"
                        ),
                    ),
                ]

        raise RuntimeError(
            f"Structured LLM generation failed after {self.max_attempts} attempts: {last_error}"
        )

    def _initial_response_format(self, schema_model: type[BaseModel], schema_name: str) -> dict:
        if self.response_format_mode == "json_schema":
            return json_schema_response_format(schema_model, schema_name)
        if self.response_format_mode == "json_object":
            return {"type": "json_object"}
        raise ValueError(f"Unsupported response_format_mode: {self.response_format_mode}")
