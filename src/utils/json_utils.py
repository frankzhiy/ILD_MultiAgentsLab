import json
import re
from typing import Any


def parse_llm_json(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = min(
            [idx for idx in [stripped.find("{"), stripped.find("[")] if idx != -1],
            default=-1,
        )
        if start == -1:
            raise
        opener = stripped[start]
        closer = "}" if opener == "{" else "]"
        end = stripped.rfind(closer)
        if end == -1 or end <= start:
            raise
        return json.loads(stripped[start : end + 1])


def to_pretty_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)

