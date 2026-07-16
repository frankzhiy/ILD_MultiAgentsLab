import time
from copy import deepcopy
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from src.llm.base import LLMClient, LLMMessage
from src.utils.json_utils import parse_llm_json

T = TypeVar("T", bound=BaseModel)


class StructuredGenerationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        attempts: list[dict[str, Any]],
        stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.stage = stage


def json_schema_response_format(
    model: type[BaseModel],
    name: str,
    *,
    pointer_field_constraints: dict[str, list[dict[str, set[str]]]] | None = None,
) -> dict:
    schema = model.model_json_schema()
    _remove_program_computed_offsets(schema)
    _prepare_strict_schema(schema)
    if pointer_field_constraints:
        _apply_pointer_field_constraints(schema, pointer_field_constraints)
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
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.llm = llm
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds
        self.response_format_mode = response_format_mode
        self.event_callback = event_callback

    def generate(
        self,
        *,
        schema_model: type[T],
        schema_name: str,
        system_prompt: str,
        user_prompt: str,
        extra_validation: Callable[[T], T] | None = None,
        pointer_field_constraints: dict[str, list[dict[str, set[str]]]] | None = None,
    ) -> tuple[T, dict]:
        stage_started = time.perf_counter()
        self._emit("stage_started", {"stage": schema_name})
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        attempts: list[dict] = []
        response_format = self._initial_response_format(
            schema_model,
            schema_name,
            pointer_field_constraints,
        )

        last_error = None
        for attempt_index in range(1, self.max_attempts + 1):
            attempt_started = time.perf_counter()
            format_name = response_format.get("type") if response_format else None
            self._emit(
                "llm_attempt_started",
                {
                    "stage": schema_name,
                    "attempt": attempt_index,
                    "response_format": format_name,
                },
            )
            try:
                response = self.llm.complete(
                    messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    response_format=response_format,
                )
            except RuntimeError as exc:
                llm_duration = time.perf_counter() - attempt_started
                self._emit(
                    "llm_attempt_failed",
                    {
                        "stage": schema_name,
                        "attempt": attempt_index,
                        "duration_seconds": round(llm_duration, 3),
                        "error": str(exc),
                    },
                )
                attempts.append(
                    {
                        "attempt": attempt_index,
                        "response_format": (
                            response_format.get("type") if response_format else None
                        ),
                        "transport_error": str(exc),
                        "llm_duration_seconds": round(llm_duration, 3),
                        "validation_duration_seconds": 0.0,
                        "duration_seconds": round(llm_duration, 3),
                    }
                )
                if _is_retryable_transport_error(exc) and attempt_index < self.max_attempts:
                    time.sleep(self.retry_backoff_seconds * attempt_index)
                    continue
                self._emit(
                    "stage_failed",
                    {"stage": schema_name, **_timing(stage_started, attempts)},
                )
                raise StructuredGenerationError(
                    f"Structured LLM request failed on attempt {attempt_index}: {exc}",
                    attempts=attempts,
                    stage=schema_name,
                ) from exc
            llm_duration = time.perf_counter() - attempt_started
            usage = _usage(response.raw)
            self._emit(
                "llm_attempt_completed",
                {
                    "stage": schema_name,
                    "attempt": attempt_index,
                    "duration_seconds": round(llm_duration, 3),
                    **usage,
                },
            )
            attempt_record = {
                "attempt": attempt_index,
                "response_format": format_name,
                "raw_response": response.raw,
                "content": response.content,
                "llm_duration_seconds": round(llm_duration, 3),
                **usage,
            }
            if not response.content.strip() and _finish_reason(response.raw) == "length":
                attempt_record["validated"] = False
                attempt_record["validation_duration_seconds"] = 0.0
                attempt_record["duration_seconds"] = round(
                    time.perf_counter() - attempt_started, 3
                )
                attempt_record["validation_error"] = (
                    "Model exhausted its output budget before producing response content."
                )
                attempts.append(attempt_record)
                self._emit(
                    "validation_failed",
                    {
                        "stage": schema_name,
                        "attempt": attempt_index,
                        "duration_seconds": 0.0,
                        "will_retry": False,
                    },
                )
                self._emit(
                    "stage_failed",
                    {"stage": schema_name, **_timing(stage_started, attempts)},
                )
                raise StructuredGenerationError(
                    "Structured LLM generation stopped because the model exhausted its output "
                    "budget before producing response content.",
                    attempts=attempts,
                    stage=schema_name,
                )
            validation_started = time.perf_counter()
            try:
                parsed = parse_llm_json(response.content)
                validated = schema_model.model_validate(parsed)
                if extra_validation:
                    validated = extra_validation(validated)
                validation_duration = time.perf_counter() - validation_started
                attempt_record["validated"] = True
                attempt_record["validation_duration_seconds"] = round(
                    validation_duration, 3
                )
                attempt_record["duration_seconds"] = round(
                    time.perf_counter() - attempt_started, 3
                )
                attempts.append(attempt_record)
                self._emit(
                    "validation_completed",
                    {
                        "stage": schema_name,
                        "attempt": attempt_index,
                        "duration_seconds": round(validation_duration, 3),
                    },
                )
                timing = _timing(stage_started, attempts)
                self._emit("stage_completed", {"stage": schema_name, **timing})
                return validated, {
                    "prompt": user_prompt,
                    "timing": timing,
                    "attempts": attempts,
                }
            except (ValueError, ValidationError) as exc:
                validation_duration = time.perf_counter() - validation_started
                last_error = exc
                attempt_record["validated"] = False
                attempt_record["validation_duration_seconds"] = round(
                    validation_duration, 3
                )
                attempt_record["duration_seconds"] = round(
                    time.perf_counter() - attempt_started, 3
                )
                attempt_record["validation_error"] = str(exc)
                attempts.append(attempt_record)
                self._emit(
                    "validation_failed",
                    {
                        "stage": schema_name,
                        "attempt": attempt_index,
                        "duration_seconds": round(validation_duration, 3),
                        "will_retry": attempt_index < self.max_attempts,
                    },
                )
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
        self._emit("stage_failed", {"stage": schema_name, **_timing(stage_started, attempts)})
        raise StructuredGenerationError(
            f"Structured LLM generation failed after {self.max_attempts} attempts: "
            f"{last_error}. Attempts: {summaries}",
            attempts=attempts,
            stage=schema_name,
        )

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self.event_callback is not None:
            self.event_callback(event, payload)

    def _initial_response_format(
        self,
        schema_model: type[BaseModel],
        schema_name: str,
        pointer_field_constraints: dict[str, list[dict[str, set[str]]]] | None,
    ) -> dict:
        if self.response_format_mode == "json_schema":
            return json_schema_response_format(
                schema_model,
                schema_name,
                pointer_field_constraints=pointer_field_constraints,
            )
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


def _usage(raw: dict[str, Any]) -> dict[str, int]:
    usage = raw.get("usage") or {}
    cached_tokens = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    values = {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cached_tokens": cached_tokens,
    }
    return {key: value for key, value in values.items() if isinstance(value, int)}


def _timing(stage_started: float, attempts: list[dict[str, Any]]) -> dict[str, float | int]:
    total = time.perf_counter() - stage_started
    llm = sum(float(item.get("llm_duration_seconds", 0.0)) for item in attempts)
    validation = sum(
        float(item.get("validation_duration_seconds", 0.0)) for item in attempts
    )
    return {
        "attempt_count": len(attempts),
        "duration_seconds": round(total, 3),
        "llm_duration_seconds": round(llm, 3),
        "validation_duration_seconds": round(validation, 3),
        "other_duration_seconds": round(max(0.0, total - llm - validation), 3),
    }


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


def _prepare_strict_schema(
    value: Any,
    definitions: dict[str, Any] | None = None,
) -> None:
    """Normalize Pydantic output for strict structured-output providers."""

    if isinstance(value, dict):
        if definitions is None:
            definitions = value.get("$defs", {})
        reference = value.get("$ref")
        if isinstance(reference, str) and len(value) > 1 and reference.startswith("#/$defs/"):
            referenced = definitions.get(reference.removeprefix("#/$defs/"))
            if isinstance(referenced, dict):
                siblings = {key: item for key, item in value.items() if key != "$ref"}
                value.clear()
                value.update(deepcopy(referenced))
                value.update(siblings)
        value.pop("default", None)
        properties = value.get("properties")
        if isinstance(properties, dict):
            value["required"] = list(properties)
            value["additionalProperties"] = False
        for item in value.values():
            _prepare_strict_schema(item, definitions)
    elif isinstance(value, list):
        for item in value:
            _prepare_strict_schema(item, definitions)


def _apply_pointer_field_constraints(
    schema: dict[str, Any],
    constraints: dict[str, list[dict[str, set[str]]]],
) -> None:
    """Inline pointer schemas with request-specific allowed locator values."""

    definitions = schema.get("$defs", {})

    def pointer_schema(value: dict[str, Any]) -> dict[str, Any]:
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            return deepcopy(definitions[reference.removeprefix("#/$defs/")])
        return deepcopy(value)

    def constrain(value: Any) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                for field_name, alternatives in constraints.items():
                    field_schema = properties.get(field_name)
                    if not isinstance(field_schema, dict):
                        continue
                    item_schema = field_schema.get("items")
                    if not isinstance(item_schema, dict):
                        continue
                    usable = [
                        alternative
                        for alternative in alternatives
                        if alternative and all(allowed for allowed in alternative.values())
                    ]
                    if not usable:
                        field_schema["maxItems"] = 0
                        continue
                    choices = []
                    for alternative in usable:
                        choice = pointer_schema(item_schema)
                        choice_properties = choice.get("properties", {})
                        for property_name, allowed in alternative.items():
                            property_schema = choice_properties[property_name]
                            allowed_values = sorted(allowed)
                            if property_schema.get("type") == "array":
                                property_schema.setdefault("items", {})["enum"] = allowed_values
                                property_schema["minItems"] = 1
                            else:
                                property_schema["enum"] = allowed_values
                        choices.append(choice)
                    field_schema["items"] = (
                        choices[0] if len(choices) == 1 else {"anyOf": choices}
                    )
            for item in value.values():
                constrain(item)
        elif isinstance(value, list):
            for item in value:
                constrain(item)

    constrain(schema)
