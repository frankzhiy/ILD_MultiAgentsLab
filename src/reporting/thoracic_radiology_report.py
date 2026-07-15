"""Problem-oriented HTML report for thoracic-radiology v2 outputs."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Iterable

from src.agents.thoracic_radiology.models import (
    EvidencePointer,
    ThoracicRadiologyDiscussionResponse,
    ThoracicRadiologyInitialAssessment,
)
from src.schemas.specialty_agent_input import SpecialtyCaseInput


DOMAIN_LABELS = {
    "source_and_evaluability": "来源与可评价性",
    "imaging_phenotype": "影像表型",
    "nature_and_burden": "病变性质与负荷",
    "morphologic_pattern": "形态学模式",
    "disease_association_and_differential": "疾病关联与鉴别",
    "longitudinal_change_and_acute_overlay": "纵向变化与急性叠加",
    "mdt_decision_impact_and_gaps": "MDT决策影响与缺口",
}


def render_thoracic_radiology_report(
    result: ThoracicRadiologyInitialAssessment | ThoracicRadiologyDiscussionResponse,
    case_input: SpecialtyCaseInput,
    output: str | Path,
) -> Path:
    output = Path(output)
    roles = {
        unit.graph_unit.graph_unit_id: unit.evidence_role.value
        for segment in case_input.segments
        for unit in segment.units
    }
    is_initial = isinstance(result, ThoracicRadiologyInitialAssessment)
    state = result if is_initial else result.updated_assessment
    phase = "首轮评估" if is_initial else "会中响应"
    body = "".join(
        [
            _hero(case_input.case_id, phase, state),
            _orientation_section(state, roles),
            _source_section(state, roles),
            _reported_content_section(state, roles),
            _task_section(state, roles),
            _discussion_section(result, roles) if not is_initial else "",
            _next_steps_section(state, roles),
            _coverage_section(state),
        ]
    )
    output.write_text(
        _document(f"{case_input.case_id} · 胸部影像科 {phase}", body), encoding="utf-8"
    )
    return output


def _document(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>
:root{{--ink:#172033;--muted:#64748b;--line:#dbe4f0;--panel:#fff;--soft:#f5f8fc;--accent:#4055a8;--accent2:#dbe4ff;--warn:#fff4d6}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--soft);color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
main{{width:min(1120px,94vw);margin:34px auto 70px}}header{{padding:30px;border-radius:20px;background:linear-gradient(135deg,#202d59,#5269c4);color:#fff;box-shadow:0 14px 35px #1f2d5940}}
h1{{margin:4px 0 8px;font-size:30px}}h2{{margin:0 0 14px;font-size:21px}}h3{{margin:0 0 7px;font-size:16px}}p{{margin:5px 0}}.eyebrow{{font-size:12px;letter-spacing:.12em;text-transform:uppercase;opacity:.8}}
.notice{{margin-top:18px;padding:10px 14px;border:1px solid #ffffff55;border-radius:10px;background:#ffffff12}}.answer{{margin-top:18px;padding:18px;border-radius:14px;background:#fff;color:var(--ink)}}.answer strong{{color:#24377d}}
.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:16px}}.metric{{padding:12px;border:1px solid #ffffff33;border-radius:12px;background:#ffffff12}}.metric span{{display:block;font-size:12px;opacity:.75}}.metric strong{{display:block;margin-top:3px}}
section{{margin-top:22px;padding:24px;border:1px solid var(--line);border-radius:16px;background:var(--panel)}}.cards{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}}.card{{padding:15px;border:1px solid var(--line);border-radius:12px;background:#fff}}.wide{{grid-column:1/-1}}
.badge{{display:inline-block;margin-right:5px;padding:2px 8px;border-radius:999px;background:var(--accent2);color:#24377d;font-size:12px}}.muted{{color:var(--muted)}}.warning{{padding:10px 12px;border-radius:9px;background:var(--warn)}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{font-size:12px;color:var(--muted)}}
.evidence{{margin-top:10px;padding:10px;border-left:3px solid #93a4e8;background:#f7f8ff}}.evidence code{{font-size:11px;color:#526178}}.quote{{margin-top:4px;color:#38445a}}ul{{margin:6px 0;padding-left:21px}}footer{{margin-top:22px;color:var(--muted);text-align:center}}
@media(max-width:800px){{.grid,.cards{{grid-template-columns:1fr}}.wide{{grid-column:auto}}}}
</style></head><body><main>{body}<footer>ILD多学科团队 · 胸部影像科文字描述分析</footer></main></body></html>"""


def _hero(case_id, phase, state) -> str:
    core = state.core_answer
    return (
        f'<header><div class="eyebrow">Thoracic Radiology · {escape(phase)}</div>'
        f"<h1>{escape(case_id)} · 胸部影像科</h1>"
        '<div class="notice">本报告仅分析病例中的影像文字描述；未读取原始图像，不能替代影像科医师直接阅片。</div>'
        '<div class="answer"><p><strong>当前影像问题：</strong>'
        f"{escape(core.primary_question)}</p><p><strong>核心回答：</strong>{escape(core.answer)}</p>"
        f"<p><strong>MDT影响：</strong>{escape(core.decision_impact)}</p>"
        + (
            f"<p><strong>关键下一步：</strong>{escape(core.decisive_next_step)}</p>"
            if core.decisive_next_step
            else ""
        )
        + "</div><div class=\"grid\">"
        + _metric("核心信度", core.confidence)
        + _metric("已识别胸部检查", len(state.reconstruction.examinations))
        + _metric("已评估任务", len(state.task_assessments))
        + "</div></header>"
    )


def _orientation_section(state, roles) -> str:
    orientation = state.reconstruction.orientation
    content = (
        f"<p><strong>临床触发：</strong>{escape(orientation.clinical_trigger)}</p>"
        f"<p><strong>主问题：</strong>{escape(orientation.primary_imaging_question)}</p>"
        + _list("次级问题", orientation.secondary_imaging_questions)
        + _list("必要临床背景", orientation.relevant_clinical_context)
        + _evidence(orientation.context_evidence, roles, "病例定向证据")
    )
    return _section("当前问题与病例定向", content)


def _source_section(state, roles) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(item.exam_id)}</td><td>{escape(item.temporal_anchor)}</td>"
        f"<td>{escape(_label(item.modality))}</td><td>{escape(_label(item.purpose))}</td>"
        f"<td>{escape(_label(item.source_authority))}</td>"
        f"<td>{escape(_label(item.evidence_level))}</td>"
        f"<td>{escape(item.relationship_note or '—')}</td></tr>"
        for item in state.reconstruction.examinations
    )
    table = (
        "<table><thead><tr><th>检查</th><th>时间</th><th>模态</th><th>目的</th>"
        f"<th>来源</th><th>文字等级</th><th>关系</th></tr></thead><tbody>{rows}</tbody></table>"
        if rows
        else '<p class="muted">未识别出可用胸部影像资料。</p>'
    )
    details = "".join(
        _card(
            item.exam_id,
            f"<p>{escape(item.description)}</p>"
            + _evidence(item.source_evidence, roles, "检查来源"),
        )
        for item in state.reconstruction.examinations
    )
    exclusions = _list(
        "排除或降级的候选资料", state.reconstruction.excluded_candidate_notes
    )
    return _section(
        "胸部影像资料与可用等级",
        table + f'<div class="cards">{details}</div>' + exclusions,
    )


def _reported_content_section(state, roles) -> str:
    exam_by_id = {item.exam_id: item for item in state.reconstruction.examinations}
    cards = "".join(
        _card(
            item.text,
            _badges(
                item.statement_type,
                item.origin,
                item.assertion_status,
                item.certainty,
            )
            + f'<p class="muted">检查：{escape(exam_by_id.get(item.exam_id).description if item.exam_id in exam_by_id else item.exam_id)}</p>'
            + _evidence(item.evidence, roles, "原文证据"),
        )
        for item in state.reconstruction.reported_statements
    )
    return _section(
        "原报告内容（所见与印象分层）",
        f'<div class="cards">{cards or _empty()}</div>',
    )


def _task_section(state, roles) -> str:
    cards = "".join(
        _card(
            _label(item.task),
            _badges(item.priority, item.answerability, item.confidence)
            + f"<p><strong>结论：</strong>{escape(item.conclusion)}</p>"
            + f'<p class="muted">{escape(item.reasoning_summary)}</p>'
            + f"<p><strong>MDT影响：</strong>{escape(item.decision_impact)}</p>"
            + _list("边界", item.limitations)
            + _evidence(item.supporting_evidence, roles)
            + _evidence(item.conflicting_evidence, roles, "冲突证据")
            + _evidence(item.related_evidence, roles, "相关背景"),
            wide=item.priority == "primary",
        )
        for item in state.task_assessments
    )
    return _section("按当前问题激活的影像任务", f'<div class="cards">{cards}</div>')


def _discussion_section(result, roles) -> str:
    answers = "".join(
        _card(
            item.question_id,
            _badges(item.confidence)
            + f"<p>{escape(item.answer)}</p>"
            + _evidence(item.supporting_evidence, roles),
            wide=True,
        )
        for item in result.chair_answers
    )
    changes = "".join(
        "<tr>"
        f"<td>{escape(_label(item.task))}</td><td>{escape(_label(item.change))}</td>"
        f"<td>{escape(item.previous_summary)}</td><td>{escape(item.updated_assessment.conclusion)}</td>"
        f"<td>{escape(item.reason)}</td></tr>"
        for item in result.task_changes
    )
    table = (
        "<table><thead><tr><th>任务</th><th>变化</th><th>首轮</th><th>更新后</th><th>原因</th></tr></thead>"
        f"<tbody>{changes}</tbody></table>"
        if changes
        else '<p class="muted">没有需要重写的影像任务。</p>'
    )
    return _section(
        "会中选择性更新",
        f'<div class="cards">{answers or _empty()}</div>{table}'
        + _list("影像科建议", result.imaging_recommendations)
        + _list("会中局限", result.limitations),
    )


def _next_steps_section(state, roles) -> str:
    actions = "".join(
        _card(
            item.action,
            _badges(item.priority)
            + f"<p>{escape(item.reason)}</p>"
            + f"<p><strong>可解锁：</strong>{escape(item.decision_unlocked)}</p>"
            + _evidence(item.related_evidence, roles, "相关背景"),
        )
        for item in state.action_items
    )
    questions = "".join(
        _card(
            f"向{_label(item.specialty.value)}提问",
            f"<p>{escape(item.question)}</p>"
            f'<p class="muted">{escape(item.why_it_matters)}</p>'
            + _evidence(item.related_evidence, roles, "相关背景"),
        )
        for item in state.specialist_questions
    )
    return _section(
        "关键下一步与专科协作",
        f'<div class="cards">{actions}{questions or _empty()}</div>'
        + _list("总体局限", state.limitations),
    )


def _coverage_section(state) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(DOMAIN_LABELS[item.domain.value])}</td>"
        f'<td><span class="badge">{escape(_label(item.status))}</span></td>'
        f"<td>{escape(item.rationale)}</td></tr>"
        for item in state.review_coverage
    )
    return _section(
        "影像审阅覆盖（内部审计）",
        (
            "<table><thead><tr><th>审阅问题域</th><th>状态</th><th>理由</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
            if rows
            else '<p class="muted">没有额外审阅覆盖记录。</p>'
        ),
    )


def _metric(label, value) -> str:
    return f'<div class="metric"><span>{escape(str(label))}</span><strong>{escape(_label(value))}</strong></div>'


def _section(title: str, content: str) -> str:
    return f"<section><h2>{escape(title)}</h2>{content}</section>"


def _card(title: str, content: str, *, wide: bool = False) -> str:
    class_name = "card wide" if wide else "card"
    return f'<article class="{class_name}"><h3>{escape(title)}</h3>{content}</article>'


def _badges(*values: Any) -> str:
    return "".join(
        f'<span class="badge">{escape(_label(value))}</span>'
        for value in values
        if value is not None
    )


def _evidence(
    pointers: Iterable[EvidencePointer], roles: dict[str, str], title: str = "支持证据"
) -> str:
    blocks = []
    for pointer in pointers:
        quotes = pointer.resolved_quotes or []
        quote_html = "".join(
            f'<div class="quote">{escape(item.quote)}</div>' for item in quotes
        )
        if not quote_html and pointer.quote:
            quote_html = f'<div class="quote">{escape(pointer.quote)}</div>'
        locator = ", ".join(pointer.proposition_ids or pointer.evidence_ids)
        blocks.append(
            '<div class="evidence">'
            f"<strong>{escape(title)}</strong> · <code>{escape(pointer.graph_unit_id)} · "
            f"{escape(roles.get(pointer.graph_unit_id, 'unknown'))} · {escape(locator)}</code>"
            f"{quote_html}</div>"
        )
    return "".join(blocks)


def _list(title: str, items: Iterable[str]) -> str:
    items = list(items)
    if not items:
        return ""
    return f"<h3>{escape(title)}</h3><ul>{''.join(f'<li>{escape(item)}</li>' for item in items)}</ul>"


def _empty() -> str:
    return '<p class="muted">当前没有可展示内容。</p>'


def _label(value: Any) -> str:
    labels = {
        "urgent": "紧急",
        "expedited": "尽快",
        "routine": "常规",
        "primary": "主任务",
        "secondary": "次级任务",
        "conditional": "条件任务",
        "background": "背景任务",
        "answered": "可回答",
        "partially_answered": "部分可回答",
        "not_answerable": "不可回答",
        "not_applicable": "不适用",
        "requires_direct_review": "需要直接阅片",
        "requires_comparator": "需要比较资料",
        "feature_level": "征象级文字",
        "impression_level": "印象级文字",
        "label_only": "仅诊断标签",
        "formal_report": "正式报告",
        "report_excerpt": "报告摘录",
        "clinician_paraphrase": "临床转述",
        "ctpa": "CTPA",
        "ct": "CT",
        "hrct": "HRCT",
        "thoracic_radiology": "胸部影像科",
        "pulmonology": "呼吸科",
        "pathology": "病理科",
        "rheumatology": "风湿免疫科",
        "targeted_pulmonary_vascular": "肺血管定向问题",
        "acute_parenchymal_overlay": "急性肺实质叠加",
        "ild_phenotype": "ILD影像表型",
        "ild_morphologic_pattern": "ILD形态模式",
        "conditional_ipf_hrct": "条件性IPF HRCT分类",
        "longitudinal_change": "纵向变化",
        "actionable_ancillary_findings": "可行动伴随发现",
        "source_reconciliation": "影像来源归一",
        "addressed_by_active_task": "已由当前任务处理",
        "reviewed_not_applicable": "已审阅但不适用",
        "not_assessable": "不可评价",
        "reported_present": "原文报告存在",
        "reported_absent": "原文明示未见",
        "reported_possible": "原文报告可能",
        "updated": "已更新",
        "resolved": "已解决",
        "newly_activated": "新激活",
    }
    return labels.get(str(value), str(value).replace("_", " "))
