"""Small, escaped HTML building blocks for the standalone MDT report."""

from html import escape


def text(value) -> str:
    return escape(str(value)) if value is not None else ""


def paragraph(value, label="") -> str:
    return f"<p><strong>{text(label)}</strong>{text(value)}</p>" if value else ""


def details(label: str, body: str, kind="") -> str:
    return f'<details class="{kind}"><summary>{label}</summary><div class="detail">{body}</div></details>'
