"""HTML report for the first-pass MDT chair synthesis."""

from html import escape
from pathlib import Path

from src.agents.mdt_chair.models import (
    CaseEvidenceCitation,
    CitedChairStatement,
    MDTChairSynthesis,
    SpecialtySourceCitation,
)


SPECIALTY_LABELS = {
    "pulmonology": "呼吸科",
    "thoracic_radiology": "胸部影像科",
    "rheumatology": "风湿免疫科",
    "pathology": "病理科",
    "chair": "主持人",
    "case_data": "病例资料补充",
}

STATUS_LABELS = {
    "assessable": "可评价",
    "partially_assessable": "部分可评价",
    "not_assessable": "不可评价",
    "true_conclusion_conflict": "结论冲突",
    "source_conflict": "来源冲突",
    "evidence_boundary_conflict": "证据边界冲突",
    "interspecialty_question": "跨专科问题",
    "specialty_self_issue": "专科自身待解决",
    "missing_case_material": "缺失病例材料",
    "chair_identified_question": "主持人新识别",
}


def render_mdt_chair_report(result: MDTChairSynthesis, output: str | Path) -> Path:
    output = Path(output)
    summaries = "".join(_specialty_summary(item) for item in result.specialty_summaries)
    conflicts = "".join(_conflict(item) for item in result.conflicts) or _empty(
        "未识别到有充分双侧证据支持的跨专科冲突。"
    )
    issues = "".join(_issue(item) for item in result.open_issues) or _empty(
        "当前没有仍需其他专科回答的问题。"
    )
    output.write_text(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(result.case_id)} · MDT主持人首轮整理</title><style>{_CSS}</style></head><body>
<header><div class="wrap"><div class="eyebrow">ILD MDT · RESPIRATORY CHAIR</div>
<h1>{escape(result.case_id)} · 主持人首轮整理</h1>
<p>专科初步判断整合、冲突识别与仍需其他专科回答的问题清单</p>
<div class="boundary">本阶段不裁决冲突，不输出最终 MDT 诊断或治疗方案。</div></div></header>
<nav><a href="#summaries">专科初步判断</a><a href="#conflicts">跨专科冲突</a><a href="#issues">仍需回答的问题</a></nav>
<main class="wrap"><section id="summaries"><h2>一、专科摘要</h2>{summaries}</section>
<section id="conflicts"><h2>二、跨专科冲突</h2>{conflicts}</section>
<section id="issues"><h2>三、仍需其他专科回答的问题</h2>{issues}</section></main></body></html>""",
        encoding="utf-8",
    )
    return output


def _specialty_summary(item) -> str:
    scope = item.evaluation_scope
    conclusions = "".join(
        '<article class="conclusion"><div class="card-head"><h4>核心专科初步判断</h4>'
        f'{_badge(conclusion.confidence)}</div><p>{escape(conclusion.conclusion)}</p>'
        f"{_citations(conclusion)}</article>"
        for conclusion in item.core_conclusions
    )
    return (
        '<article class="specialty"><div class="specialty-head"><h3>'
        f'{escape(SPECIALTY_LABELS[item.specialty])}</h3>{_badge(STATUS_LABELS[scope.assessability])}'
        f'{_badge(scope.confidence)}</div><div class="scope"><strong>评价范围</strong>'
        f'<p>{escape(scope.summary)}</p>{_citations(scope)}</div>{conclusions}</article>'
    )


def _conflict(item) -> str:
    positions = "".join(
        '<div class="position"><h4>'
        f'{escape(SPECIALTY_LABELS[position.specialty])}</h4><p>{escape(position.position)}</p>'
        f"{_citations(position)}</div>"
        for position in item.positions
    )
    return (
        '<article class="conflict"><div class="card-head"><h3>'
        f'{escape(item.conflict_id)} · {escape(item.topic)}</h3>'
        f'{_badge(STATUS_LABELS[item.conflict_nature])}{_badge("暂不裁决")}</div>'
        f'<div class="positions">{positions}</div><p class="analysis"><strong>主持人分析：</strong>'
        f'{escape(item.analysis)}</p></article>'
    )


def _issue(item) -> str:
    raised = SPECIALTY_LABELS.get(item.raised_by, item.raised_by)
    parties = "、".join(SPECIALTY_LABELS.get(value, value) for value in item.responsible_parties)
    relation = (
        f'<p><strong>关联冲突：</strong>{escape("、".join(item.related_conflict_ids))}</p>'
        if item.related_conflict_ids
        else ""
    )
    return (
        '<article class="issue"><div class="card-head"><h3>'
        f'{escape(item.issue_id)} · {escape(item.question)}</h3>'
        f'{_badge(STATUS_LABELS[item.issue_type])}</div>'
        f'<div class="issue-grid"><p><strong>提出方</strong><br>{escape(raised)}</p>'
        f'<p><strong>负责回答/补充</strong><br>{escape(parties)}</p></div>'
        f'<p><strong>当前障碍：</strong>{escape(item.current_barrier)}</p>'
        f'<p><strong>所需资料或回答：</strong>{escape(item.required_information_or_answer)}</p>'
        f'<p><strong>潜在 MDT 影响：</strong>{escape(item.potential_mdt_impact)}</p>'
        f'{relation}{_citations(item)}</article>'
    )


def _citations(statement: CitedChairStatement) -> str:
    sources = "".join(_source_citation(item) for item in statement.source_citations)
    evidence = "".join(_case_evidence(item) for item in statement.case_evidence)
    return (
        '<details><summary>查看专科原文与病例 graph 证据</summary>'
        f'<div class="citation-grid"><div><h5>专科输出原文</h5>{sources or _empty("无")}</div>'
        f'<div><h5>病例 graph 证据</h5>{evidence or _empty("无直接病例原文证据")}</div>'
        "</div></details>"
    )


def _source_citation(item: SpecialtySourceCitation) -> str:
    return (
        '<blockquote><code>'
        f'{escape(item.source_ref)} · {escape(SPECIALTY_LABELS[item.specialty])} · '
        f'{escape(item.source_path)}</code><p>{escape(item.quote)}</p></blockquote>'
    )


def _case_evidence(item: CaseEvidenceCitation) -> str:
    ids = ", ".join(item.proposition_ids or item.evidence_ids)
    nodes = ", ".join(item.node_ids)
    node_html = f'<small>nodes: {escape(nodes)}</small>' if nodes else ""
    return (
        '<blockquote class="evidence"><code>'
        f'{escape(item.evidence_ref)} · {escape(item.segment_id)} · '
        f'{escape(item.graph_unit_id)} · {escape(ids)}</code>'
        f'<p>{escape(item.quote or "未解析到原文")}</p>{node_html}</blockquote>'
    )


def _badge(value: str) -> str:
    return f'<span class="badge">{escape(str(value))}</span>'


def _empty(text: str) -> str:
    return f'<p class="empty">{escape(text)}</p>'


_CSS = """
:root{--ink:#172033;--muted:#64748b;--line:#dbe5ef;--surface:#fff;--navy:#102a43;--blue:#2563eb;--cyan:#0e7490;--amber:#b45309;--soft:#f4f7fb}
*{box-sizing:border-box}body{margin:0;background:var(--soft);color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}.wrap{width:min(1180px,94vw);margin:auto}
header{padding:42px 0 35px;color:#fff;background:linear-gradient(125deg,#0b1f38,#174e75 65%,#0e7490)}.eyebrow{font-size:12px;letter-spacing:.14em;opacity:.75}h1{margin:6px 0;font-size:32px}header p{margin:0;opacity:.9}.boundary{display:inline-block;margin-top:18px;padding:8px 12px;border:1px solid #ffffff42;border-radius:9px;background:#ffffff12}
nav{position:sticky;top:0;z-index:3;display:flex;gap:8px;padding:10px max(3vw,calc((100% - 1180px)/2));border-bottom:1px solid var(--line);background:#fffffff2;backdrop-filter:blur(10px)}nav a{padding:5px 10px;color:#334155;text-decoration:none;font-weight:700}
main{padding:28px 0 70px}section{margin-bottom:24px}h2{margin:0 0 14px}.specialty,.conflict,.issue{margin:13px 0;padding:20px;border:1px solid var(--line);border-radius:15px;background:var(--surface);box-shadow:0 5px 18px #0f233a0a}.specialty-head,.card-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.specialty-head h3,.card-head h3,.card-head h4{flex:1;margin:0}.badge{display:inline-block;padding:3px 9px;border-radius:999px;background:#e0f2fe;color:#075985;font-size:12px}.scope{margin:14px 0;padding:13px 15px;border-left:4px solid var(--cyan);border-radius:8px;background:#f0f9ff}.conclusion{margin-top:12px;padding:14px;border:1px solid var(--line);border-radius:11px}.positions,.citation-grid,.issue-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.position{padding:14px;border:1px solid #fed7aa;border-radius:10px;background:#fffbeb}.position h4{margin:0}.analysis{padding:12px;border-radius:9px;background:#f8fafc}.issue-grid p{padding:10px;border-radius:9px;background:#f8fafc}
details{margin-top:10px}summary{cursor:pointer;color:var(--blue);font-weight:700}h5{margin:10px 0 5px}blockquote{margin:7px 0;padding:10px 12px;border-left:3px solid #94a3b8;background:#f8fafc}blockquote.evidence{border-color:var(--cyan)}blockquote p{margin:5px 0;white-space:pre-wrap}code,small{display:block;color:var(--muted);font-size:11px;overflow-wrap:anywhere}.empty{color:var(--muted)}
@media(max-width:760px){.positions,.citation-grid,.issue-grid{grid-template-columns:1fr}h1{font-size:26px}}
"""
