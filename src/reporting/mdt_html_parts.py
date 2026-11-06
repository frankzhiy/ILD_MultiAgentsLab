"""Small, escaped HTML building blocks for the standalone MDT report."""

from html import escape
import re


_INTERNAL_REF = r"(?:S[0-9]{3,}|[PB][0-9]+|boundary_[a-z0-9_]+)"
_INTERNAL_REFS = rf"(?<![A-Za-z0-9_]){_INTERNAL_REF}(?![A-Za-z0-9_])"
_REF_LIST = rf"{_INTERNAL_REFS}(?:[、，]|及|和|与|及需求|及资料需求|和边界|及边界)"
_LINKAGE = re.compile(
    rf"(?:条件关系)?关联(?:资料需求|需求|边界)?{_INTERNAL_REFS}"
    rf"(?:(?:[、]|及|和|与)(?:资料需求|需求|边界)?{_INTERNAL_REFS})*"
)


def clinical_prose(value: str | None) -> str:
    """Translate bookkeeping references in generated prose. Verbatim quotes use text()."""
    if not value:
        return ""
    if not re.search(_INTERNAL_REFS, value):
        return value
    value = re.sub(rf"对应P[0-9]+的?", "", value)
    value = re.sub(r"P[0-9]+所列", "", value)
    value = re.sub(rf"其结果的条件价值{_LINKAGE.pattern}", "该结果可用于鉴别判断", value)
    value = re.sub(rf"关联{_INTERNAL_REFS}的(?=[A-Za-z\u4e00-\u9fff])", "用于", value)
    value = re.sub(rf"({_LINKAGE.pattern})[。；：，]", "", value)
    value = re.sub(rf"(?:(?:对|实际回答|回答已覆盖)(?:{_REF_LIST})*{_INTERNAL_REFS})[：:]", "", value)
    value = re.sub(rf"为{_INTERNAL_REFS}(?=条件性)", "为", value)
    value = re.sub(
        rf"(?:{_REF_LIST})*S[0-9]{{3,}}(?=仅为条件|只具有条件|仅提供条件|只保留条件|仅保留条件)",
        "相应线索",
        value,
    )
    value = re.sub(rf"(?:{_REF_LIST})+{_INTERNAL_REFS}", "相关记录", value)
    value = re.sub(
        _INTERNAL_REFS,
        lambda match: "该临床问题" if match[0].startswith("P") else
        "该待明确事项" if match[0].startswith(("B", "boundary_")) else "相关记录",
        value,
    )
    value = value.lstrip("。；：， ")
    return value.rstrip("；：， ") + "。" if value.endswith(("；", "：", "，")) else value


def clinical_text(value: str | None) -> str:
    return text(clinical_prose(value))


def clinical_paragraph(value: str | None, label="") -> str:
    return paragraph(clinical_prose(value), label)


def text(value) -> str:
    return escape(str(value)) if value is not None else ""


def paragraph(value, label="") -> str:
    return f"<p><strong>{text(label)}</strong>{text(value)}</p>" if value else ""


def details(label: str, body: str, kind="") -> str:
    return f'<details class="{kind}"><summary>{label}</summary><div class="detail">{body}</div></details>'
