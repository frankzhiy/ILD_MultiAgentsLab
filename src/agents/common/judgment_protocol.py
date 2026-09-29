"""One runtime source for the shared clinical judgment protocol."""

from functools import cache
from pathlib import Path


@cache
def _protocol() -> str:
    path = Path(__file__).resolve().parents[2] / "prompts/common/mdt_judgment_protocol.md"
    return path.read_text(encoding="utf-8")


def judgment_system_prompt(role_prompt: str) -> str:
    return f"{role_prompt}\n\n{_protocol()}"
