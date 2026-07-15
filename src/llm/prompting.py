"""Compact serialization for values sent to an LLM."""

import json
from enum import Enum
from functools import cache
from typing import Any

from pydantic import BaseModel


@cache
def _llm_fields(model: type[BaseModel]) -> frozenset[str]:
    """Fields visible in JSON Schema exclude program-filled SkipJsonSchema fields."""

    return frozenset(model.model_json_schema().get("properties", {}))


def llm_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        visible = _llm_fields(type(value))
        return {
            name: llm_value(getattr(value, name))
            for name in type(value).model_fields
            if name in visible
        }
    if isinstance(value, dict):
        return {key: llm_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [llm_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def prompt_json(value: Any) -> str:
    return json.dumps(
        llm_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def prompt_schema_json(schema_model: type[BaseModel]) -> str:
    schema = schema_model.model_json_schema()
    _drop_schema_noise(schema)
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))


def _drop_schema_noise(value: Any) -> None:
    if isinstance(value, dict):
        value.pop("title", None)
        value.pop("default", None)
        for item in value.values():
            _drop_schema_noise(item)
    elif isinstance(value, list):
        for item in value:
            _drop_schema_noise(item)
