"""One runtime source for the shared clinical judgment protocol."""

from pathlib import Path
import re


_NONCLINICAL_EXPLANATION = re.compile(
    r"(?:未提供|缺少|没有|无).{0,16}(?:原始图像|原始影像|原片)"
    r"|(?:不能|无法|未能).{0,16}(?:直接阅片|独立复核|读取原始图像|读取原始影像)"
    r"|未直接阅片|未提及的征象不能视为阴性|未提及不等于阴性"
    r"|这不(?:表示|代表)患者从未(?:接受|做过)活检"
    r"|影像文字不能替代组织学证据"
    r"|当前最强的工作判断|讨论前基线|讨论轮次摘要|当前输入|本轮输入"
)


def _protocol() -> str:
    path = Path(__file__).resolve().parents[2] / "prompts/common/mdt_judgment_protocol.md"
    return path.read_text(encoding="utf-8")


def judgment_system_prompt(role_prompt: str) -> str:
    return f"{role_prompt}\n\n{_protocol()}"


def validate_clinical_text(value: str) -> None:
    if _NONCLINICAL_EXPLANATION.search(value):
        raise ValueError("面向医生的结论含有资料形式或防御性解释，请依据现有文字改写临床判断")
