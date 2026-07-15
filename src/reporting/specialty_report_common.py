"""Shared clinical reasoning and evidence components for specialty HTML reports."""

from __future__ import annotations

from html import escape
import os
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel

from src.guidelines.models import GuidelineEvidencePointer


COMMON_CSS = """
:root{--ink:#172033;--muted:#64748b;--line:#dbe5ef;--surface:#fff;--blue:#2563eb;
--navy:#0f2440;--cyan:#0e7490;--amber:#d97706;--violet:#7c3aed;--green:#047857}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f3f6fa;color:var(--ink);overflow-x:hidden;
font:15px/1.68 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
main{width:auto;max-width:1180px;margin:30px auto 80px}header,.hero{border-radius:0;color:#fff;
background:linear-gradient(125deg,#0b1f38 0%,#123d68 62%,#0e7490 100%);box-shadow:none}
section,.report-section{border:1px solid var(--line);border-radius:14px;background:var(--surface);
box-shadow:0 5px 18px rgba(15,35,58,.045);min-width:0}
.audit-grid{display:grid;gap:14px}.audit-card{overflow:hidden;border:1px solid var(--line);border-radius:13px;background:#fff}
.audit-head{display:flex;gap:9px;align-items:center;flex-wrap:wrap;padding:15px 17px;border-bottom:1px solid #edf1f5}
.audit-head h3{flex:1;margin:0;font-size:16px}.audit-body{padding:16px 17px}.audit-reason{margin:0;padding:13px 15px;
border-left:4px solid var(--cyan);border-radius:9px;background:#f0f9ff;color:#24415f}
.audit-columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px;margin-top:12px}
.audit-panel{padding:12px;border:1px solid var(--line);border-radius:10px;background:#f8fafc}.audit-panel h4{margin:0 0 8px;font-size:13px}
.audit-panel.support{border-left:4px solid #0284c7}.audit-panel.conflict{border-left:4px solid #dc2626}
.audit-panel.context{border-left:4px solid var(--violet)}.audit-panel.guide{border-left:4px solid var(--green);background:#f0fdf4}
.audit-evidence{padding:9px 10px;margin-top:7px;border-radius:8px;background:#fff}.audit-evidence blockquote{margin:6px 0 0}
.audit-locator{display:block;color:var(--muted);font-size:11px;overflow-wrap:anywhere}.guide-title{font-weight:750;color:#065f46}
.guide-quote{margin:8px 0;padding:9px 11px;border-radius:7px;background:#fff;color:#26463b}.guide-link{color:#047857;font-weight:700}
.card,.audit-card,.audit-panel,p,blockquote,code{min-width:0;overflow-wrap:anywhere}table{display:block;max-width:100%;overflow-x:auto}
.audit-empty{color:var(--muted);font-size:13px}.report-jump{position:sticky;top:0;z-index:5;display:flex;gap:8px;overflow:auto;
padding:10px max(16px,calc((100% - 1180px)/2));border-bottom:1px solid var(--line);background:#fffffff2;backdrop-filter:blur(12px)}
.report-jump a{white-space:nowrap;padding:5px 10px;border-radius:8px;color:#334155;text-decoration:none;font-size:13px;font-weight:700}
.report-jump a:hover{color:var(--blue);background:#eff6ff}
@media(max-width:760px){main{width:auto;margin:22px 11px 60px}.audit-columns{grid-template-columns:1fr}}
"""


def report_nav() -> str:
    return (
        '<nav class="report-jump"><a href="#results">核心结果</a>'
        '<a href="#reasoning-audit">推理与证据</a><a href="#guideline-audit">指南引用</a>'
        '<a href="#gaps">数据缺口</a><a href="#collaboration">跨专科协作</a></nav>'
    )


def render_reasoning_audit(result: BaseModel, roles: dict[str, str], report_path: str | Path) -> str:
    cards = []
    for item in _reasoning_items(result):
        title = _first_text(item, "assessment", "conclusion", "answer", "updated_view") or type(item).__name__
        confidence = _first_text(item, "confidence", "answerability", "change_status") or "未标注"
        reasoning = _first_text(item, "reasoning_summary", "reason", "rationale") or "未提供推理摘要。"
        support = _case_panel("支持病例证据", getattr(item, "supporting_evidence", []), roles, "support")
        conflict = _case_panel("冲突病例证据", getattr(item, "conflicting_evidence", []), roles, "conflict")
        context = _case_panel(
            "相关上下文（不直接支持结论）", getattr(item, "related_evidence", []), roles, "context"
        )
        guides = _guide_panel(getattr(item, "guideline_evidence", []), report_path)
        cards.append(
            '<article class="audit-card"><div class="audit-head">'
            f'<h3>{escape(title)}</h3><span class="badge">{escape(confidence)}</span></div>'
            f'<div class="audit-body"><p class="audit-reason"><strong>推理摘要</strong><br>{escape(reasoning)}</p>'
            f'<div class="audit-columns">{support}{conflict}{context}{guides}</div></div></article>'
        )
    return (
        '<section id="reasoning-audit" style="margin-top:22px;padding:24px">'
        '<h2>统一推理、病例证据与指南依据</h2>'
        '<p class="muted">病例支持证据、冲突证据、相关上下文和指南知识严格分层；定位信息由程序补全。</p>'
        f'<div class="audit-grid">{"".join(cards) or _empty()}</div></section>'
    )


def render_guideline_audit(result: BaseModel, report_path: str | Path) -> str:
    pointers = list(_guideline_pointers(result))
    unique = {item.chunk_id: item for item in pointers}
    content = "".join(_guide(item, report_path) for item in unique.values()) or _empty(
        "本次输出没有采用指南片段；不能据此宣称结论符合某项指南。"
    )
    return (
        '<section id="guideline-audit" style="margin-top:22px;padding:24px">'
        '<h2>指南引用审计</h2><p class="muted">仅列出本轮检索后被模型实际采用、并经本地校验通过的片段。</p>'
        f'<div class="audit-grid">{content}</div></section>'
    )


def _reasoning_items(value: object) -> Iterator[BaseModel]:
    if isinstance(value, BaseModel):
        fields = type(value).model_fields
        if "reasoning_summary" in fields or (
            "reason" in fields and ("updated_view" in fields or "answer" in fields)
        ):
            yield value
        for field_name in fields:
            yield from _reasoning_items(getattr(value, field_name))
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _reasoning_items(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _reasoning_items(item)


def _guideline_pointers(value: object) -> Iterator[GuidelineEvidencePointer]:
    if isinstance(value, GuidelineEvidencePointer):
        yield value
    elif isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            yield from _guideline_pointers(getattr(value, field_name))
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _guideline_pointers(item)


def _case_panel(title: str, pointers, roles: dict[str, str], kind: str) -> str:
    content = "".join(_case_evidence(item, roles) for item in pointers) or _empty()
    return f'<div class="audit-panel {kind}"><h4>{escape(title)}</h4>{content}</div>'


def _case_evidence(pointer, roles: dict[str, str]) -> str:
    unit = getattr(pointer, "graph_unit_id", "")
    segment = getattr(pointer, "segment_id", "")
    ids = getattr(pointer, "proposition_ids", []) or getattr(pointer, "evidence_ids", [])
    quotes = getattr(pointer, "resolved_quotes", [])
    quote = " ".join(item.quote for item in quotes) or getattr(pointer, "quote", "")
    locator = f"{segment} · {unit} · {roles.get(unit, 'unknown')} · {', '.join(ids)}"
    return (
        '<div class="audit-evidence">'
        f'<code class="audit-locator">{escape(locator)}</code>'
        f'<blockquote>{escape(quote or "未解析到原文")}</blockquote></div>'
    )


def _guide_panel(pointers, report_path: str | Path) -> str:
    content = "".join(_guide(item, report_path) for item in pointers) or _empty()
    return f'<div class="audit-panel guide"><h4>指南依据</h4>{content}</div>'


def _guide(pointer: GuidelineEvidencePointer, report_path: str | Path) -> str:
    source = Path(__file__).resolve().parents[2] / "data/guidelines" / pointer.source_file
    href = os.path.relpath(source, Path(report_path).resolve().parent)
    if pointer.page:
        href += f"#page={pointer.page}"
    section = " › ".join(pointer.section_path) or "未识别章节"
    return (
        '<article class="audit-evidence">'
        f'<div class="guide-title">{escape(pointer.title or pointer.guideline_id)}</div>'
        f'<code class="audit-locator">{escape(pointer.organization)} · {pointer.year or ""} · '
        f'{escape(section)} · PDF 第 {pointer.page or "?"} 页 · {escape(pointer.chunk_id)}</code>'
        f'<blockquote class="guide-quote">{escape(pointer.quote)}</blockquote>'
        f'<p><strong>关联：</strong>{escape(pointer.relevance)}<br><strong>用于本病例：</strong>{escape(pointer.application)}</p>'
        f'<a class="guide-link" href="{escape(href, quote=True)}">打开指南对应页</a></article>'
    )


def _first_text(item: BaseModel, *names: str) -> str:
    for name in names:
        value = getattr(item, name, None)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def _empty(text: str = "当前无可呈现内容。") -> str:
    return f'<p class="audit-empty">{escape(text)}</p>'
