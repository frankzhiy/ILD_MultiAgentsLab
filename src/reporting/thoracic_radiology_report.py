"""Human-readable HTML report for thoracic radiology agent outputs."""

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
    "mdt_decision_impact_and_gaps": "MDT 决策影响与缺口",
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
    state = result if is_initial else result.updated_state
    phase = "首轮评估" if is_initial else "会中响应"
    pattern = state.interpretation_state.morphologic_pattern
    longitudinal = state.interpretation_state.longitudinal_assessment
    body = "".join(
        [
            _hero(case_input.case_id, phase, state, pattern, longitudinal),
            _source_section(state, roles),
            _observation_section(state, roles),
            _interpretation_section(state, roles),
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
:root{{--ink:#172033;--muted:#64748b;--line:#dbe4f0;--panel:#fff;--soft:#f5f8fc;--accent:#4055a8;--accent2:#dbe4ff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--soft);color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
main{{width:min(1180px,94vw);margin:34px auto 70px}}header{{padding:30px;border-radius:20px;background:linear-gradient(135deg,#202d59,#5269c4);color:#fff;box-shadow:0 14px 35px #1f2d5940}}
h1{{margin:4px 0 8px;font-size:30px}}h2{{margin:0 0 14px;font-size:21px}}h3{{margin:0 0 7px;font-size:16px}}p{{margin:5px 0}}.eyebrow{{font-size:12px;letter-spacing:.12em;text-transform:uppercase;opacity:.8}}
.notice{{margin-top:18px;padding:10px 14px;border:1px solid #ffffff55;border-radius:10px;background:#ffffff12}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:20px}}.metric{{padding:14px;border:1px solid #ffffff33;border-radius:12px;background:#ffffff12}}.metric span{{display:block;font-size:12px;opacity:.75}}.metric strong{{display:block;margin-top:3px}}
section{{margin-top:22px;padding:24px;border:1px solid var(--line);border-radius:16px;background:var(--panel)}}.cards{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}}.card{{padding:15px;border:1px solid var(--line);border-radius:12px;background:#fff}}.wide{{grid-column:1/-1}}
.badge{{display:inline-block;margin-right:5px;padding:2px 8px;border-radius:999px;background:var(--accent2);color:#24377d;font-size:12px}}.muted{{color:var(--muted)}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{font-size:12px;color:var(--muted)}}
.evidence{{margin-top:10px;padding:10px;border-left:3px solid #93a4e8;background:#f7f8ff}}.evidence code{{font-size:11px;color:#526178}}.quote{{margin-top:4px;color:#38445a}}
.stack>*+*{{margin-top:10px}}ul{{margin:6px 0;padding-left:21px}}footer{{margin-top:22px;color:var(--muted);text-align:center}}
@media(max-width:800px){{.grid,.cards{{grid-template-columns:1fr}}.wide{{grid-column:auto}}}}
</style></head><body><main>{body}<footer>ILD 多学科团队 · 胸部影像科文字描述分析</footer></main></body></html>"""


def _hero(case_id, phase, state, pattern, longitudinal) -> str:
    pattern_text = pattern.primary_pattern if pattern and pattern.primary_pattern else "不可评价"
    confidence = pattern.confidence if pattern else "unknown"
    longitudinal_text = longitudinal.status if longitudinal else "not_assessable"
    return (
        f'<header><div class="eyebrow">Thoracic Radiology · {escape(phase)}</div>'
        f"<h1>{escape(case_id)} · 胸部影像科</h1>"
        '<div class="notice">本报告仅分析病例中的影像文字描述；未读取原始图像，不能替代影像科医师直接阅片。</div>'
        '<div class="grid">'
        f"{_metric('形态模式', pattern_text)}{_metric('模式信度', confidence)}"
        f"{_metric('纵向状态', longitudinal_text)}"
        f"{_metric('文字可评价性', state.source_state.overall_evaluability)}</div></header>"
    )


def _metric(label, value) -> str:
    return f'<div class="metric"><span>{escape(label)}</span><strong>{escape(_label(value))}</strong></div>'


def _source_section(state, roles) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(item.exam_id)}</td><td>{escape(item.temporal_anchor)}</td>"
        f"<td>{escape(_label(item.modality))}</td><td>{escape(_label(item.source_authority))}</td>"
        f"<td>{escape(_label(item.description_sufficiency))}</td>"
        f"<td>{escape(_label(item.technical_quality_status))}</td>"
        f"<td>{escape(_label(item.comparison_status))}</td></tr>"
        for item in state.source_state.examinations
    )
    details = "".join(
        _card(
            item.assessment,
            _badges(item.source_authority, item.description_sufficiency)
            + _evidence(item.supporting_evidence, roles),
        )
        for item in state.source_state.examinations
    )
    table = (
        "<table><thead><tr><th>检查</th><th>时间</th><th>类型</th><th>来源</th><th>描述充分性</th>"
        f"<th>技术质量</th><th>比较</th></tr></thead><tbody>{rows}</tbody></table>"
        if rows
        else '<p class="muted">未形成可识别的影像检查记录。</p>'
    )
    return _section(
        "来源与可评价性",
        table
        + f'<p class="muted">{escape(state.source_state.reasoning_summary)}</p>'
        + f'<div class="cards">{details}</div>',
    )


def _observation_section(state, roles) -> str:
    observations = "".join(
        _card(
            item.finding,
            _badges(item.status, item.category, item.confidence)
            + f"<p>头尾：{escape(item.craniocaudal_distribution)}；轴向：{escape(item.axial_distribution)}；"
            f"解剖：{escape(item.anatomic_distribution)}</p>"
            + _evidence(item.supporting_evidence, roles),
        )
        for item in state.observation_state.observations
    )
    assessments = [
        ("间质/肺泡性质", state.observation_state.interstitial_or_alveolar),
        ("纤维化", state.observation_state.fibrosis_assessment),
        ("范围与负荷", state.observation_state.extent_and_burden),
        ("急性叠加", state.observation_state.acute_overlay),
    ]
    cards = observations + "".join(
        _assessment_card(label, item, roles) for label, item in assessments if item
    )
    cards += "".join(
        _assessment_card("伴随征象", item, roles)
        for item in state.observation_state.ancillary_findings
    )
    return _section(
        "描述派生影像观察",
        f'<p class="muted">{escape(state.observation_state.reasoning_summary)}</p>'
        f'<div class="cards">{cards or _empty()}</div>',
    )


def _interpretation_section(state, roles) -> str:
    interpretation = state.interpretation_state
    pattern = interpretation.morphologic_pattern
    pattern_card = (
        _card(
            pattern.primary_pattern or "未形成主导模式",
            _badges(pattern.classification_status, pattern.confidence)
            + f"<p>{escape(pattern.reasoning_summary)}</p>"
            + _evidence(pattern.supporting_evidence, roles)
            + _evidence(pattern.conflicting_evidence, roles, "冲突证据"),
            wide=True,
        )
        if pattern
        else _empty()
    )
    conditionals = "".join(
        _card(
            f"条件分类 · {_label(item.protocol)}",
            _badges(item.applicability, item.category or "无分类")
            + f'<p>{escape(item.applicability_basis)}</p><p class="muted">{escape(item.reasoning_summary)}</p>'
            + _evidence([*item.supporting_evidence, *item.related_evidence], roles),
        )
        for item in interpretation.conditional_classifications
    )
    associations = "".join(
        _card(
            f"{item.rank}. {item.disease_or_context}",
            _badges(item.relationship, item.confidence)
            + f"<p>{escape(item.reasoning_summary)}</p>"
            + _evidence(item.supporting_evidence, roles),
        )
        for item in interpretation.disease_associations
    )
    longitudinal = interpretation.longitudinal_assessment
    longitudinal_card = (
        _card(
            "纵向影像判断",
            _badges(longitudinal.status, longitudinal.confidence)
            + f"<p>{escape(longitudinal.reasoning_summary)}</p>"
            + _list("进展征象", longitudinal.progression_features)
            + _evidence(longitudinal.supporting_evidence, roles)
            + _evidence(longitudinal.related_evidence, roles, "相关背景"),
            wide=True,
        )
        if longitudinal
        else ""
    )
    return _section(
        "影像解释与指南分类",
        f'<div class="cards">{pattern_card}{conditionals}{associations}{longitudinal_card}</div>',
    )


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
        f"<td>{escape(DOMAIN_LABELS[item.domain.value])}</td>"
        f"<td>{escape(_label(item.observation_delta))}</td>"
        f"<td>{escape(_label(item.interpretation_delta))}</td>"
        f"<td>{escape(_label(item.assessability_delta))}</td>"
        f"<td>{escape(item.reason)}</td></tr>"
        for item in result.domain_changes
    )
    table = (
        "<table><thead><tr><th>问题域</th><th>观察层</th><th>解释层</th><th>可评价性</th><th>原因</th>"
        f"</tr></thead><tbody>{changes}</tbody></table>"
    )
    return _section(
        "会中更新",
        f'<div class="cards">{answers or _empty()}</div><h3 style="margin-top:18px">七域变化</h3>{table}'
        + _list("影像科建议", result.imaging_recommendations)
        + _list("局限性", result.limitations),
    )


def _next_steps_section(state, roles) -> str:
    review_requests = "".join(
        _card(
            item.request,
            f"<p>{escape(item.reason)}</p><p><strong>可解锁：</strong>{escape(item.decision_unlocked)}</p>"
            + _evidence(item.related_evidence, roles, "相关背景"),
        )
        for item in state.direct_review_requests
    )
    gaps = "".join(
        _card(
            item.missing_information,
            _badges(item.gap_type)
            + f"<p><strong>现有：</strong>{escape(item.available_information)}</p>"
            f"<p>{escape(item.why_it_matters)}</p><p><strong>可解锁：</strong>{escape(item.decision_unlocked)}</p>"
            + _evidence(item.related_evidence, roles, "相关背景"),
        )
        for item in state.missing_data
    )
    questions = "".join(
        _card(
            f"向 {_label(item.specialty.value)} 提问",
            f'<p>{escape(item.question)}</p><p class="muted">{escape(item.why_it_matters)}</p>'
            + _evidence(item.related_evidence, roles, "相关背景"),
        )
        for item in state.specialist_dependencies
    )
    return _section(
        "直接阅片、数据缺口与专科协作",
        f'<div class="cards">{review_requests}{gaps}{questions or _empty()}</div>',
    )


def _coverage_section(state) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{escape(DOMAIN_LABELS[item.domain.value])}</td>"
        f'<td><span class="badge">{escape(_label(item.status))}</span></td>'
        f"<td>{escape(item.rationale)}</td></tr>"
        for item in state.domain_reviews
    )
    return _section(
        "七问处理状态",
        "<table><thead><tr><th>问题域</th><th>状态</th><th>理由</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>",
    )


def _assessment_card(label, item, roles) -> str:
    return _card(
        label,
        _badges(item.confidence)
        + f'<p>{escape(item.assessment)}</p><p class="muted">{escape(item.reasoning_summary)}</p>'
        + _evidence(item.supporting_evidence, roles)
        + _evidence(item.related_evidence, roles, "相关背景"),
    )


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
    blocks = "".join(
        '<div class="evidence">'
        f"<strong>{escape(title)}</strong> · <code>{escape(pointer.graph_unit_id)} · "
        f"{escape(roles.get(pointer.graph_unit_id, 'unknown'))} · "
        f"{escape(', '.join(pointer.evidence_ids))}</code>"
        f'<div class="quote">{escape(pointer.quote)}</div></div>'
        for pointer in pointers
    )
    return blocks


def _list(title: str, items: Iterable[str]) -> str:
    items = list(items)
    if not items:
        return ""
    return (
        f"<h3>{escape(title)}</h3><ul>{''.join(f'<li>{escape(item)}</li>' for item in items)}</ul>"
    )


def _empty() -> str:
    return '<p class="muted">当前没有可展示内容。</p>'


def _label(value: Any) -> str:
    labels = {
        "text_descriptions_only": "仅分析文字描述",
        "partially_sufficient": "部分充分",
        "sufficient_for_pattern_assessment": "足以评价模式",
        "insufficient_for_pattern_assessment": "不足以评价模式",
        "requires_comparator": "需要比较片",
        "not_assessable": "不可评价",
        "not_applicable": "不适用",
        "assessed": "已评价",
        "partially_assessable": "部分可评价",
        "requires_direct_image_review": "需要直接阅片",
        "unchanged": "不变",
        "updated": "已更新",
        "improved": "可评价性提高",
        "worsened": "可评价性降低",
        "resolved": "已解决",
        "thoracic_radiology": "胸部影像科",
        "pulmonology": "呼吸科",
        "pathology": "病理科",
        "rheumatology": "风湿免疫科",
    }
    return labels.get(str(value), str(value).replace("_", " "))
