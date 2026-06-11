import time
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from src.llm.base import LLMClient, LLMMessage
from src.utils.json_utils import parse_llm_json

T = TypeVar("T", bound=BaseModel)


class StructuredGenerationError(RuntimeError):
    def __init__(self, message: str, *, attempts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.attempts = attempts


def json_schema_response_format(model: type[BaseModel], name: str) -> dict:
    schema = model.model_json_schema()
    _remove_program_computed_offsets(schema)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }


class StructuredLLMGenerator:
    def __init__(
        self,
        llm: LLMClient,
        *,
        temperature: float,
        max_tokens: int,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.0,
        response_format_mode: str = "json_object",
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.llm = llm
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds
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
            attempt_started = time.perf_counter()
            try:
                response = self.llm.complete(
                    messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    response_format=response_format,
                )
            except RuntimeError as exc:
                if (
                    response_format
                    and response_format.get("type") == "json_schema"
                    and not _is_retryable_transport_error(exc)
                ):
                    response_format = {"type": "json_object"}
                    last_error = exc
                    attempts.append(
                        {
                            "attempt": attempt_index,
                            "transport_error": str(exc),
                            "fallback": "json_object",
                            "duration_seconds": round(time.perf_counter() - attempt_started, 3),
                        }
                    )
                    continue
                attempts.append(
                    {
                        "attempt": attempt_index,
                        "response_format": (
                            response_format.get("type") if response_format else None
                        ),
                        "transport_error": str(exc),
                        "duration_seconds": round(time.perf_counter() - attempt_started, 3),
                    }
                )
                if _is_retryable_transport_error(exc) and attempt_index < self.max_attempts:
                    time.sleep(self.retry_backoff_seconds * attempt_index)
                    continue
                raise StructuredGenerationError(
                    f"Structured LLM request failed on attempt {attempt_index}: {exc}",
                    attempts=attempts,
                ) from exc
            attempt_record = {
                "attempt": attempt_index,
                "response_format": response_format.get("type") if response_format else None,
                "raw_response": response.raw,
                "content": response.content,
                "duration_seconds": round(time.perf_counter() - attempt_started, 3),
            }
            if not response.content.strip() and _finish_reason(response.raw) == "length":
                attempt_record["validated"] = False
                attempt_record["validation_error"] = (
                    "Model exhausted its output budget before producing response content."
                )
                attempts.append(attempt_record)
                raise StructuredGenerationError(
                    "Structured LLM generation stopped because the model exhausted its output "
                    "budget before producing response content.",
                    attempts=attempts,
                )
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

        summaries = "; ".join(_summarize_attempt(item) for item in attempts)
        raise StructuredGenerationError(
            f"Structured LLM generation failed after {self.max_attempts} attempts: "
            f"{last_error}. Attempts: {summaries}",
            attempts=attempts,
        )

    def _initial_response_format(self, schema_model: type[BaseModel], schema_name: str) -> dict:
        if self.response_format_mode == "json_schema":
            return json_schema_response_format(schema_model, schema_name)
        if self.response_format_mode == "json_object":
            return {"type": "json_object"}
        raise ValueError(f"Unsupported response_format_mode: {self.response_format_mode}")


def _summarize_attempt(attempt: dict[str, Any]) -> str:
    if attempt.get("transport_error"):
        return f"#{attempt['attempt']} transport_error={attempt['transport_error']}"
    raw = attempt.get("raw_response") or {}
    choices = raw.get("choices") or []
    finish_reason = choices[0].get("finish_reason") if choices else None
    content = attempt.get("content")
    content_length = len(content) if isinstance(content, str) else None
    return (
        f"#{attempt['attempt']} content_length={content_length}, "
        f"finish_reason={finish_reason!r}, error={attempt.get('validation_error')}"
    )


def _finish_reason(raw: dict[str, Any]) -> str | None:
    choices = raw.get("choices") or []
    return choices[0].get("finish_reason") if choices else None


def _is_retryable_transport_error(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in ("http 429", "http 503", "timed out", "timeout", "temporarily unavailable")
    )


def _remove_program_computed_offsets(value: Any) -> None:
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict) and "text" in properties:
            properties.pop("start_char", None)
            properties.pop("end_char", None)
            required = value.get("required")
            if isinstance(required, list):
                value["required"] = [
                    item for item in required if item not in {"start_char", "end_char"}
                ]
        for item in value.values():
            _remove_program_computed_offsets(item)
    elif isinstance(value, list):
        for item in value:
            _remove_program_computed_offsets(item)
