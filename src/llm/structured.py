import json
import socket
import ssl
import http.client
import time
import urllib.error
from copy import deepcopy
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from src.llm.base import LLMClient, LLMMessage
from src.llm.throttle import request_throttle
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
    dependent_field_constraints: (
        dict[str, list[dict[str, set[str]]]] | None
    ) = None,
    pointer_field_constraints: dict[str, list[dict[str, set[str]]]] | None = None,
    string_field_constraints: dict[str, dict[str, set[str]]] | None = None,
) -> dict:
    schema = model.model_json_schema()
    _remove_program_computed_offsets(schema)
    _prepare_strict_schema(schema)
    if pointer_field_constraints:
        _apply_pointer_field_constraints(schema, pointer_field_constraints)
    if string_field_constraints:
        _apply_string_field_constraints(schema, string_field_constraints)
    if dependent_field_constraints:
        _apply_dependent_field_constraints(schema, dependent_field_constraints)
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
        dependent_field_constraints: (
            dict[str, list[dict[str, set[str]]]] | None
        ) = None,
        pointer_field_constraints: dict[str, list[dict[str, set[str]]]] | None = None,
        string_field_constraints: dict[str, dict[str, set[str]]] | None = None,
        repair_on_validation_error: bool = False,
        max_attempts_override: int | None = None,
    ) -> tuple[T, dict]:
        attempt_limit = self.max_attempts if max_attempts_override is None else max_attempts_override
        if attempt_limit < 1:
            raise ValueError("max_attempts_override must be at least 1")
        stage_started = time.perf_counter()
        self._emit("stage_started", {"stage": schema_name})
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        attempts: list[dict] = []
        initial_response_format = self._initial_response_format(
            schema_model,
            schema_name,
            dependent_field_constraints,
            pointer_field_constraints,
            string_field_constraints,
        )
        response_format = initial_response_format
        repair_base: dict[str, Any] | None = None

        last_error = None
        for attempt_index in range(1, attempt_limit + 1):
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
                with request_throttle.slot():
                    self._emit("llm_request_ready", {"stage": schema_name})
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
                if _is_retryable_transport_error(exc) and attempt_index < attempt_limit:
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
            candidate = None
            try:
                parsed = parse_llm_json(response.content)
                candidate = (
                    _apply_repair_edits(repair_base, parsed)
                    if repair_base is not None
                    else parsed
                )
                validated = schema_model.model_validate(candidate)
                if repair_on_validation_error:
                    candidate = validated.model_dump(mode="json")
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
                        "will_retry": attempt_index < attempt_limit,
                    },
                )
                if (
                    repair_on_validation_error
                    and _finish_reason(response.raw) != "length"
                    and (
                        isinstance(candidate, dict)
                        or repair_base is not None
                    )
                ):
                    repair_feedback = (
                        "上一轮 edits 未消除错误，请勿重复无效修改："
                        f"{response.content[:2000]}\n"
                        if repair_base is not None
                        else ""
                    )
                    # Keep the exact candidate that produced this error, including schema errors.
                    if isinstance(candidate, dict):
                        repair_base = candidate
                    response_format = {"type": "json_object"}
                    messages = [
                        LLMMessage(
                            role="system",
                            content=(
                                "你是结构化 JSON 局部修复器。依据原始任务和校验错误，"
                                "只返回 edits JSON，不要重新生成完整结果。"
                            ),
                        ),
                        LLMMessage(role="user", content=user_prompt),
                        LLMMessage(
                            role="assistant",
                            content=json.dumps(repair_base, ensure_ascii=False),
                        ),
                        LLMMessage(
                            role="user",
                            content=(
                                "上面的 JSON 已生成完整；只修复程序指出的错误，"
                                "保留其他所有字段及数组成员。返回一个 JSON 对象，格式为 "
                                '{"edits":[{"op":"replace","path":"/items/0/status",'
                                '"value":"boundary"}]}。'
                                "所有 path 和数组下标以上面的当前 JSON 为准，不要重复应用已有修改。"
                                "path 是从 0 开始的 JSON Pointer；replace 可替换已存在的标量字段"
                                "或标量数组（例如 atomic_claim_ids），也可把可空对象改为 null；"
                                "不能替换为新的对象或对象数组。若把判断改为 maintain，"
                                "必须同时将 proposed_content 改为 null；"
                                "append 只能向已存在的数组追加一个新元素；"
                                'remove_indices 可从数组删除指定位置，格式为 '
                                '{"op":"remove_indices","path":"/items","value":[2,3]}。'
                                "如一个字段的修正影响其他字段，也要一并修正相关标量字段；"
                                "若错误包含 duplicates，必须减少重复的 source_ref 出现次数；"
                                "只修改 route 等分类字段不能消除重复。删除冗余项前，"
                                "先确认需保留的信息已存在于其他项目。"
                                "若同一原子判断的 evidence_links 对同一 evidence_ref "
                                "给出多个 relation，按该判断和证据内容保留一个主要关系；"
                                "可以用 remove_indices 删除多余链接，并将必要的限定信息"
                                "写入保留链接的 rationale。不要按固定关系优先级取舍。"
                                "若 supplement 改动了核心判断字段，依据真实变化选择："
                                "仅收紧确定度或适用范围用 qualify，实质修正判断用 revise；"
                                "若只是补充依据，则恢复原核心字段。不要为通过校验丢弃新信息。"
                                "不要返回完整台账，不要替换数组或对象，不要解释。\n"
                                f"{repair_feedback}"
                                f"校验错误：{exc}"
                            ),
                        ),
                    ]
                else:
                    repair_base = None
                    response_format = initial_response_format
                    messages = [
                        LLMMessage(role="system", content=system_prompt),
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
            f"Structured LLM generation failed after {attempt_limit} attempts: "
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
        dependent_field_constraints: (
            dict[str, list[dict[str, set[str]]]] | None
        ),
        pointer_field_constraints: dict[str, list[dict[str, set[str]]]] | None,
        string_field_constraints: dict[str, dict[str, set[str]]] | None,
    ) -> dict:
        if self.response_format_mode == "json_schema":
            return json_schema_response_format(
                schema_model,
                schema_name,
                dependent_field_constraints=dependent_field_constraints,
                pointer_field_constraints=pointer_field_constraints,
                string_field_constraints=string_field_constraints,
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


def _apply_repair_edits(base: dict[str, Any], patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict) or set(patch) != {"edits"}:
        raise ValueError("Repair response must contain only an edits array")
    edits = patch["edits"]
    if not isinstance(edits, list) or not edits:
        raise ValueError("Repair response must contain at least one edit")
    result = deepcopy(base)
    operations = {"replace": [], "remove_indices": [], "append": []}
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"op", "path", "value"}:
            raise ValueError("Each repair edit must contain op, path, and value")
        if edit["op"] not in operations:
            raise ValueError("Repair edit op must be replace, remove_indices, or append")
        if not isinstance(edit["path"], str) or not edit["path"].startswith("/"):
            raise ValueError("Repair edit path must be a JSON Pointer")
        operations[edit["op"]].append(edit)
    remove_paths = [edit["path"] for edit in operations["remove_indices"]]
    if len(remove_paths) != len(set(remove_paths)):
        raise ValueError("Use one remove_indices edit per array")
    for edit in (
        operations["replace"] + operations["remove_indices"] + operations["append"]
    ):
        path = edit["path"]
        current: Any = result
        for part in path[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, list):
                if not key.isdigit() or int(key) >= len(current):
                    raise ValueError(f"Repair edit path does not exist: {path}")
                parent, index = current, int(key)
            elif isinstance(current, dict) and key in current:
                parent, index = current, key
            else:
                raise ValueError(f"Repair edit path does not exist: {path}")
            current = parent[index]
        if edit["op"] == "remove_indices":
            indices = edit["value"]
            if (
                not isinstance(current, list)
                or not isinstance(indices, list)
                or not indices
                or any(type(index) is not int or index < 0 or index >= len(current) for index in indices)
                or len(indices) != len(set(indices))
            ):
                raise ValueError(f"Repair remove_indices requires valid array indices: {path}")
            for index_to_remove in sorted(indices, reverse=True):
                current.pop(index_to_remove)
        elif edit["op"] == "append":
            if not isinstance(current, list):
                raise ValueError(f"Repair append path must be an array: {path}")
            if isinstance(edit["value"], list):
                raise ValueError(f"Repair append must add one array element: {path}")
            current.append(edit["value"])
        else:
            value = edit["value"]
            scalar_array = (
                isinstance(current, list)
                and isinstance(value, list)
                and all(not isinstance(item, (dict, list)) for item in current)
                and all(not isinstance(item, (dict, list)) for item in value)
            )
            nullable_object = isinstance(current, dict) and value is None
            if not scalar_array and not nullable_object and (
                isinstance(current, (dict, list))
                or isinstance(value, (dict, list))
            ):
                raise ValueError(
                    f"Repair edit must replace a scalar field or scalar array: {path}"
                )
            parent[index] = value
    return result


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
    cause = exc.__cause__
    if isinstance(cause, (http.client.RemoteDisconnected, http.client.IncompleteRead, ConnectionError, TimeoutError, ssl.SSLEOFError)):
        return True
    if isinstance(cause, urllib.error.URLError):
        if isinstance(cause.reason, (ssl.SSLEOFError, ConnectionResetError, BrokenPipeError)):
            return True
        if isinstance(cause.reason, socket.gaierror):
            return cause.reason.errno == socket.EAI_AGAIN
    message = str(exc).lower()
    return any(
        marker in message
        for marker in ("http 429", "http 502", "http 503", "http 504", "timed out", "timeout", "temporarily unavailable")
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
        if "oneOf" in value and "discriminator" in value:
            value["anyOf"] = value.pop("oneOf")
            value.pop("discriminator")
        if "const" in value:
            value["enum"] = [value.pop("const")]
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
                    usable = []
                    for alternative in alternatives:
                        if not alternative:
                            continue
                        choice_properties = pointer_schema(item_schema).get("properties", {})
                        if all(
                            allowed
                            or choice_properties.get(property_name, {}).get("type") == "array"
                            for property_name, allowed in alternative.items()
                        ):
                            usable.append(alternative)
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
                                if allowed_values:
                                    property_schema.setdefault("items", {})["enum"] = allowed_values
                                    property_schema["minItems"] = 1
                                else:
                                    property_schema["maxItems"] = 0
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


def _apply_string_field_constraints(
    schema: dict[str, Any],
    constraints: dict[str, dict[str, set[str]]],
) -> None:
    """Restrict scalar or array string fields in named model definitions."""

    for model_name, fields in constraints.items():
        properties = schema.get("$defs", {}).get(model_name, {}).get("properties", {})
        for field_name, allowed in fields.items():
            field_schema = properties.get(field_name)
            if not isinstance(field_schema, dict):
                continue
            if not allowed and field_schema.get("type") == "array":
                field_schema.pop("minItems", None)
                field_schema["maxItems"] = 0
                continue
            target = field_schema.get("items", field_schema)
            if isinstance(target, dict):
                target["enum"] = sorted(allowed)


def _apply_dependent_field_constraints(
    schema: dict[str, Any],
    constraints: dict[str, list[dict[str, set[str]]]],
) -> None:
    """Bind fields in one model through request-specific schema alternatives."""

    definitions = schema.get("$defs", {})

    def inline(value: dict[str, Any]) -> dict[str, Any]:
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            return deepcopy(definitions[reference.removeprefix("#/$defs/")])
        return deepcopy(value)

    def restrict(
        value: dict[str, Any],
        path: list[str],
        allowed: set[str],
    ) -> None:
        if "anyOf" in value:
            value["anyOf"] = [inline(choice) for choice in value["anyOf"]]
            for choice in value["anyOf"]:
                restrict(choice, path, allowed)
            return
        field = value["properties"][path[0]]
        if len(path) == 1:
            target = field.get("items", field)
            target["enum"] = sorted(allowed)
            return
        if field.get("type") == "array":
            if not allowed:
                field["maxItems"] = 0
                return
            child = inline(field["items"])
            field["items"] = child
        else:
            child = inline(field)
            value["properties"][path[0]] = child
        restrict(child, path[1:], allowed)

    for model_name, alternatives in constraints.items():
        base = definitions.get(model_name)
        if not isinstance(base, dict) or not alternatives:
            continue
        choices = []
        for alternative in alternatives:
            choice = deepcopy(base)
            for field_path, allowed in alternative.items():
                restrict(choice, field_path.split("."), allowed)
            choices.append(choice)
        definitions[model_name] = (
            choices[0] if len(choices) == 1 else {"anyOf": choices}
        )
