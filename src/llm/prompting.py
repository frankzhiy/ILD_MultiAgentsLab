"""Compact serialization for values sent to an LLM."""

import json
from collections import Counter
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
            name: ("case" if name in {"case_id", "source_run_dir"} else llm_value(getattr(value, name)))
            for name in type(value).model_fields
            if name in visible
        }
    if isinstance(value, dict):
        # Clinical filenames can encode a diagnosis. Preserve artifact IDs locally only.
        return {key: ("case" if key in {"case_id", "source_run_dir"} else llm_value(item)) for key, item in value.items()}
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


def build_shared_prompt_view(value: Any) -> dict[str, Any]:
    """Represent repeated large values once, with an exactly reversible reference."""

    counts = Counter()
    # ponytail: O(n²) on deeply nested JSON; cache encodings if prompt building gets slow.
    encode = lambda item: json.dumps(item, ensure_ascii=False, separators=(",", ":"))

    def collect(item):
        encoded = encode(item)
        if len(encoded) < 100:
            return
        counts[encoded] += 1
        for child in item.values() if isinstance(item, dict) else item if isinstance(item, list) else []:
            collect(child)

    collect(value)
    references = {encoded: f"V{index}" for index, encoded in enumerate(
        (encoded for encoded, count in counts.items() if count > 1), start=1)}

    def descend(item):
        if isinstance(item, dict):
            return {key: project(child) for key, child in item.items()}
        if isinstance(item, list):
            return [project(child) for child in item]
        return item

    shared_values = {}

    def project(item):
        reference = references.get(encode(item))
        if reference:
            if reference not in shared_values:
                shared_values[reference] = descend(item)
            return {"shared_value_ref": reference}
        return descend(item)

    projected = project(value)

    return {
        "shared_value_rule": "仅含 shared_value_ref 的对象代表 shared_values 中同编号的完整原值；递归展开后即为原始输入，引用不增加或删除证据。",
        "shared_values": shared_values,
        "input": projected,
    }


def shared_prompt_json(value: Any) -> str:
    original = prompt_json(value)
    shared = prompt_json(build_shared_prompt_view(llm_value(value)))
    return shared if len(shared) < len(original) else original


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
