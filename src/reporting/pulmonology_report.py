"""Human-readable HTML report for pulmonology agent outputs."""

from html import escape
from pathlib import Path
from typing import Any

from src.agents.pulmonology.models import (
    EvidencePointer,
    PulmonologyDiscussionResponse,
    PulmonologyInitialAssessment,
)
from src.schemas.specialty_agent_input import SpecialtyCaseInput


ROLE_LABELS = {
    "owned": "呼吸科主责",
    "shared_context": "共享背景",
    "collaborative_context": "跨专科上下文",
    "reference_only": "其他专科参考",
}

SPECIALTY_LABELS = {
    "pulmonology": "呼吸科",
    "thoracic_radiology": "胸部影像",
    "pathology": "病理科",
    "rheumatology": "风湿免疫",
    "shared_context": "共享背景",
}

CONFIDENCE_LABELS = {
    "very_high": "很高置信度",
    "high": "高置信度",
    "moderate": "中等置信度",
    "low": "低置信度",
    "unknown": "置信度未知",
}

GAP_LABELS = {
    "not_provided": "当前资料未提供",
    "insufficient_detail": "已有记录但细节不足",
    "no_longitudinal_comparator": "缺少历史对照",
    "not_performed": "原文明示未完成",
    "uncertain_availability": "是否完成尚不明确",
}

DOMAIN_LABELS = {
    "clinical_phenotype": "1. 临床表型与病程",
    "secondary_causes": "2. 继发病因或相关状态",
    "pulmonary_severity": "3. 肺损害严重度",
    "respiratory_tests_and_bronchoscopy": "4. 呼吸科检查与支气管镜",
    "specialist_integration": "5. 专科意见进入判断",
    "progression": "6. 进展与 PPF",
    "diagnostic_formulation": "7. 工作诊断与鉴别",
    "decision_relevant_gaps": "8. 决策相关数据缺口",
}

REVIEW_STATUS_LABELS = {
    "assessed": "已评价",
    "partially_assessable": "部分可评价",
    "not_assessable": "不可评价",
    "deferred_to_specialist": "等待专科",
    "not_applicable": "不适用",
    "updated": "已更新",
    "reviewed_unchanged": "复核后不变",
    "still_not_assessable": "仍不可评价",
    "still_deferred": "仍等待专科",
    "resolved": "已解决",
}


def render_pulmonology_report(
    result: PulmonologyInitialAssessment | PulmonologyDiscussionResponse,
    case_input: SpecialtyCaseInput,
    output_path: str | Path,
) -> Path:
    roles = {
        unit.graph_unit.graph_unit_id: str(unit.evidence_role)
        for segment in case_input.segments
        for unit in segment.units
    }
    is_initial = isinstance(result, PulmonologyInitialAssessment)
    phase_label = "首轮评估" if is_initial else "会中响应"
    body = _render_initial(result, roles) if is_initial else _render_discussion(result, roles)
    html = _page(result.case_id, phase_label, case_input, body)
    path = Path(output_path)
    path.write_text(html, encoding="utf-8")
    return path


def _page(
    case_id: str,
    phase_label: str,
    case_input: SpecialtyCaseInput,
    body: str,
) -> str:
    summary = case_input.summary
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(case_id)} · 呼吸科 {phase_label}</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, "PingFang SC", "Microsoft YaHei", sans-serif;
      --ink:#172033; --muted:#64748b; --line:#dbe5ef; --surface:#fff; --blue:#2563eb;
      --navy:#0f2440; --cyan:#0ea5e9; --amber:#d97706; --violet:#7c3aed; }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{ margin:0; background:#f3f6fa; color:var(--ink); line-height:1.65; }}
    .hero {{ color:#fff; background:linear-gradient(125deg,#0b1f38 0%,#123d68 62%,#0e7490 100%); }}
    .hero-inner, main {{ max-width:1180px; margin:0 auto; padding-left:24px; padding-right:24px; }}
    .hero-inner {{ padding-top:42px; padding-bottom:38px; }}
    .eyebrow {{ text-transform:uppercase; letter-spacing:.13em; font-size:12px; font-weight:750; opacity:.72; }}
    h1 {{ margin:8px 0 4px; font-size:34px; line-height:1.2; }}
    .hero-subtitle {{ opacity:.78; }}
    .metrics {{ display:flex; flex-wrap:wrap; gap:9px; margin-top:22px; }}
    .metric {{ padding:5px 11px; border:1px solid rgba(255,255,255,.22); border-radius:999px;
      background:rgba(255,255,255,.09); font-size:12px; }}
    .jump {{ position:sticky; top:0; z-index:4; display:flex; gap:8px; overflow-x:auto; padding:11px 24px;
      border-bottom:1px solid var(--line); background:rgba(255,255,255,.94); backdrop-filter:blur(12px); }}
    .jump a {{ white-space:nowrap; color:#334155; text-decoration:none; font-size:13px; font-weight:700;
      padding:6px 11px; border-radius:8px; }}
    .jump a:hover {{ color:var(--blue); background:#eff6ff; }}
    main {{ padding-top:30px; padding-bottom:80px; }}
    .zone {{ margin-bottom:42px; scroll-margin-top:70px; }}
    .zone-head {{ display:flex; justify-content:space-between; gap:20px; align-items:end; margin-bottom:16px; }}
    .zone h2 {{ margin:3px 0 0; font-size:25px; line-height:1.25; }}
    .zone-note {{ max-width:560px; color:var(--muted); font-size:13px; text-align:right; }}
    .result-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
    .result-card, .detail-card, .gap-card, .plain-card {{ border:1px solid var(--line); border-radius:14px;
      background:var(--surface); box-shadow:0 5px 18px rgba(15,35,58,.045); }}
    .result-card {{ padding:19px 20px; border-top:4px solid var(--blue); }}
    .result-card.wide {{ grid-column:1 / -1; }}
    .card-label {{ color:var(--muted); font-size:12px; font-weight:750; letter-spacing:.06em; }}
    .result-text {{ margin:7px 0 0; font-size:16px; font-weight:720; line-height:1.55; }}
    .badge {{ display:inline-flex; align-items:center; padding:3px 9px; border-radius:999px;
      background:#eef2f7; color:#475569; font-size:11px; font-weight:750; }}
    .confidence-very_high, .confidence-high {{ background:#dcfce7; color:#166534; }}
    .confidence-moderate {{ background:#fef3c7; color:#92400e; }}
    .confidence-low, .confidence-unknown {{ background:#f1f5f9; color:#64748b; }}
    .rank-list {{ display:grid; gap:10px; margin-top:14px; }}
    .rank-item {{ display:grid; grid-template-columns:34px 1fr auto; gap:12px; align-items:center;
      padding:13px 15px; border:1px solid var(--line); border-radius:11px; background:#fff; }}
    .rank {{ display:grid; place-items:center; width:31px; height:31px; border-radius:9px;
      background:#e8f0ff; color:#1d4ed8; font-weight:800; }}
    .detail-stack, .gap-stack {{ display:grid; gap:14px; }}
    .detail-card {{ overflow:hidden; }}
    .detail-head {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; padding:17px 19px;
      border-bottom:1px solid #edf1f5; }}
    .detail-head h3 {{ flex:1; min-width:240px; margin:0; font-size:17px; }}
    .detail-body {{ padding:17px 19px 19px; }}
    .reasoning {{ margin:0; padding:14px 16px; border-radius:10px; border-left:4px solid #38bdf8;
      background:#f0f9ff; color:#24415f; }}
    .reasoning strong {{ display:block; margin-bottom:4px; color:#075985; font-size:12px; }}
    .evidence-panel {{ margin-top:13px; border:1px solid var(--line); border-radius:10px; background:#f8fafc; }}
    .evidence-panel summary {{ cursor:pointer; padding:10px 13px; color:#334155; font-size:13px; font-weight:720; }}
    .evidence-list {{ display:grid; gap:9px; padding:0 11px 11px; }}
    .evidence {{ padding:12px 13px; border-left:4px solid #0284c7; border-radius:8px; background:#fff; }}
    .role-shared_context {{ border-left-color:#94a3b8; }}
    .role-collaborative_context {{ border-left-color:#f59e0b; }}
    .role-reference_only {{ border-left-color:var(--violet); }}
    blockquote {{ margin:9px 0; padding:8px 11px; border-radius:7px; background:#f8fafc; }}
    code {{ display:block; color:#64748b; overflow-wrap:anywhere; font-size:11px; line-height:1.55; }}
    .gap-card {{ overflow:hidden; }}
    .gap-head {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; padding:16px 19px;
      background:linear-gradient(90deg,#fff7ed,#fff); border-bottom:1px solid #ffedd5; }}
    .gap-head h3 {{ flex:1; margin:0; font-size:17px; }}
    .gap-type {{ background:#ffedd5; color:#9a3412; }}
    .gap-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; padding:16px 19px 10px; }}
    .gap-side {{ padding:13px 14px; border-radius:10px; }}
    .gap-side strong {{ display:block; margin-bottom:5px; font-size:12px; }}
    .available {{ background:#eff6ff; color:#1e3a5f; }}
    .missing {{ background:#fff7ed; color:#7c2d12; }}
    .gap-why {{ margin:0 19px 16px; padding:11px 13px; border-radius:9px; background:#f8fafc; color:#475569; }}
    .gap-card .evidence-panel {{ margin:0 19px 17px; }}
    .plain-card {{ padding:17px 19px; }}
    .plain-card h3 {{ margin:0 0 6px; font-size:16px; }}
    .plain-card p {{ margin:0; color:#475569; }}
    .empty {{ padding:16px 18px; border:1px dashed #cbd5e1; border-radius:12px; color:var(--muted); background:#fff; }}
    .change {{ display:grid; grid-template-columns:1fr auto 1fr; gap:12px; align-items:center; margin-bottom:13px; }}
    .change div {{ padding:12px 13px; border-radius:9px; background:#f8fafc; }}
    ul.clean {{ margin:0; padding-left:20px; }}
    @media (max-width:760px) {{
      .hero-inner, main {{ padding-left:15px; padding-right:15px; }}
      h1 {{ font-size:28px; }} .jump {{ padding-left:10px; padding-right:10px; }}
      .result-grid, .gap-grid {{ grid-template-columns:1fr; }}
      .result-card.wide {{ grid-column:auto; }}
      .zone-head {{ display:block; }} .zone-note {{ text-align:left; margin-top:5px; }}
      .rank-item {{ grid-template-columns:34px 1fr; }} .rank-item > :last-child {{ grid-column:2; justify-self:start; }}
      .change {{ grid-template-columns:1fr; }} .change-arrow {{ display:none; }}
    }}
  </style>
</head>
<body>
  <header class="hero"><div class="hero-inner">
    <div class="eyebrow">ILD Multidisciplinary Team · Pulmonology</div>
    <h1>{escape(case_id)} · 呼吸科{phase_label}</h1>
    <div class="hero-subtitle">先读结果，再按需展开推理和原始证据</div>
    <div class="metrics">
      <span class="metric">segment {summary.segment_count}</span>
      <span class="metric">unit {summary.unit_count}</span>
      <span class="metric">主责 {summary.owned_unit_count}</span>
      <span class="metric">共享 {summary.shared_context_unit_count}</span>
      <span class="metric">跨专科 {summary.collaborative_context_unit_count}</span>
      <span class="metric">参考 {summary.reference_only_unit_count}</span>
    </div>
  </div></header>
  <nav class="jump">
    <a href="#results">核心结果</a><a href="#reasoning">推理与证据</a>
    <a href="#gaps">数据缺口</a><a href="#collaboration">跨专科协作</a>
    <a href="#coverage">八问覆盖</a>
  </nav>
  <main>{body}</main>
</body>
</html>
"""


def _render_initial(result: PulmonologyInitialAssessment, roles: dict[str, str]) -> str:
    cards = []
    if result.clinical_phenotype:
        cards.append(
            _result_card(
                "临床表型",
                result.clinical_phenotype.assessment,
                result.clinical_phenotype.confidence,
                wide=True,
            )
        )
    if result.pulmonary_severity:
        cards.append(
            _result_card(
                "肺损害严重度",
                result.pulmonary_severity.assessment,
                result.pulmonary_severity.confidence,
            )
        )
    if result.progression_assessment:
        cards.append(
            _result_card(
                "进展 / PPF",
                result.progression_assessment.reasoning_summary,
                "unknown",
            )
        )
    formulation = result.diagnostic_formulation
    if formulation:
        cards.append(
            _result_card(
                "呼吸科工作诊断",
                formulation.leading_diagnosis or "当前不足以形成主导诊断",
                formulation.confidence,
            )
        )
    overview = "".join(cards)
    differentials = "".join(
        _rank_item(item) for item in (formulation.differential_diagnoses if formulation else [])
    )
    secondary = "".join(
        _result_card(
            f"继发因素 · {item.cause}",
            item.status,
            item.confidence,
        )
        for item in result.secondary_cause_assessment
    )
    reasoning = "".join(
        [
            _detail_section(
                "临床表型",
                [_clinical_detail(result.clinical_phenotype, roles)]
                if result.clinical_phenotype
                else [],
            ),
            _detail_section(
                "肺损害严重度",
                [_clinical_detail(result.pulmonary_severity, roles)]
                if result.pulmonary_severity
                else [],
            ),
            _detail_section(
                "呼吸科检查解释",
                [_clinical_detail(item, roles) for item in result.respiratory_test_interpretation],
            ),
            _progression_detail(result.progression_assessment, roles),
            _bronchoscopy_detail(result.bronchoscopy_assessment, roles),
            _detail_section(
                "继发因素",
                [_secondary_detail(item, roles) for item in result.secondary_cause_assessment],
            ),
            _detail_section(
                "鉴别诊断",
                [
                    _differential_detail(item, roles)
                    for item in (formulation.differential_diagnoses if formulation else [])
                ],
            ),
        ]
    )
    gaps = "".join(_gap_card(item, roles) for item in result.missing_data)
    collaboration = "".join(
        [
            _subsection(
                "给其他专科的问题",
                [_question_card(item, roles) for item in result.specialist_dependencies],
            ),
            _subsection(
                "需要其他专科确认的观察",
                [_reference_card(item, roles) for item in result.reference_observations],
            ),
            _list_block("局限性", result.limitations),
        ]
    )
    return "".join(
        [
            _zone(
                "results",
                "结果",
                "核心结果",
                "本区只呈现结论，不混入推理过程。",
                f'<div class="result-grid">{overview}</div>{_rank_block(differentials)}<div class="result-grid">{secondary}</div>',
            ),
            _zone(
                "reasoning",
                "依据",
                "推理与证据",
                "每项判断先显示推理摘要，原始证据和定位默认折叠。",
                reasoning,
            ),
            _zone(
                "gaps",
                "下一步",
                "数据缺口",
                "区分已有资料与真正缺失；相关证据只说明当前已有内容。",
                f'<div class="gap-stack">{gaps or _empty()}</div>',
            ),
            _zone(
                "collaboration",
                "协作",
                "跨专科协作",
                "需要影像、病理或风湿免疫进一步回答的问题。",
                collaboration,
            ),
            _coverage_zone(result.domain_reviews),
        ]
    )


def _render_discussion(result: PulmonologyDiscussionResponse, roles: dict[str, str]) -> str:
    state = result.updated_state
    overview = "".join(
        _result_card(f"主席问题 · {item.question_id}", item.answer, item.confidence)
        for item in result.chair_answers
    )
    formulation = state.diagnostic_formulation
    if formulation:
        overview += _result_card(
            "更新后的呼吸科工作诊断",
            formulation.leading_diagnosis or "当前不足以形成主导诊断",
            formulation.confidence,
            wide=True,
        )
    differentials = "".join(
        _rank_item(item) for item in (formulation.differential_diagnoses if formulation else [])
    )
    recommendations = _list_block("诊断性建议", result.diagnostic_recommendations)
    reasoning = "".join(
        [
            _detail_section(
                "对主席问题的回应",
                [_chair_answer_card(item, roles) for item in result.chair_answers],
            ),
            _detail_section(
                "正式专科意见映射",
                [_mapped_finding_card(item, roles) for item in result.mapped_findings],
            ),
            _detail_section(
                "更新后的鉴别诊断",
                [
                    _differential_detail(item, roles)
                    for item in (formulation.differential_diagnoses if formulation else [])
                ],
            ),
            _detail_section(
                "八问状态变化",
                [_change_card(item, roles) for item in result.domain_changes],
            ),
            _detail_section(
                "未解决冲突",
                [_clinical_detail(item, roles) for item in result.unresolved_conflicts],
            ),
        ]
    )
    gaps = "".join(_gap_card(item, roles) for item in state.missing_data)
    collaboration = "".join(
        [
            _list_block("采用的专科意见 ID", result.specialist_opinions_used),
            recommendations,
            _list_block("局限性", result.limitations),
        ]
    )
    return "".join(
        [
            _zone(
                "results",
                "结果",
                "核心结果",
                "优先呈现会中更新后的判断与建议。",
                f'<div class="result-grid">{overview or _empty()}</div>{_rank_block(differentials)}{recommendations}',
            ),
            _zone(
                "reasoning", "依据", "推理与证据", "将观点变化、分歧和证据与结果分开。", reasoning
            ),
            _zone(
                "gaps",
                "下一步",
                "数据缺口",
                "补充数据前不能解决的关键问题。",
                f'<div class="gap-stack">{gaps or _empty()}</div>',
            ),
            _zone(
                "collaboration",
                "协作",
                "MDT 协作信息",
                "本轮采用的专科意见和提交给主席的建议。",
                collaboration,
            ),
            _coverage_zone(state.domain_reviews),
        ]
    )


def _zone(anchor: str, eyebrow: str, title: str, note: str, content: str) -> str:
    return (
        f'<section class="zone" id="{escape(anchor)}">'
        '<div class="zone-head"><div>'
        f'<div class="eyebrow" style="color:#2563eb;opacity:1">{escape(eyebrow)}</div>'
        f'<h2>{escape(title)}</h2></div><div class="zone-note">{escape(note)}</div></div>'
        f"{content or _empty()}</section>"
    )


def _result_card(label: str, text: str, confidence: str, *, wide: bool = False) -> str:
    wide_class = " wide" if wide else ""
    return (
        f'<article class="result-card{wide_class}"><div class="card-label">{escape(label)}</div>'
        f'<p class="result-text">{escape(text)}</p>{_confidence(confidence)}</article>'
    )


def _rank_block(items: str) -> str:
    return f'<div class="rank-list">{items}</div>' if items else ""


def _rank_item(item: Any) -> str:
    return (
        '<div class="rank-item">'
        f'<span class="rank">{item.rank}</span><strong>{escape(item.diagnosis)}</strong>'
        f"{_confidence(item.confidence)}</div>"
    )


def _detail_section(title: str, items: list[str]) -> str:
    return _subsection(title, items)


def _subsection(title: str, items: list[str]) -> str:
    content = "".join(items) or _empty()
    return f'<div style="margin-bottom:25px"><h3 style="margin:0 0 10px">{escape(title)}</h3><div class="detail-stack">{content}</div></div>'


def _clinical_detail(item: Any, roles: dict[str, str]) -> str:
    return _detail_card(
        item.assessment,
        item.confidence,
        item.reasoning_summary,
        item.supporting_evidence,
        roles,
        item.specialist_opinion_ids,
        _evidence_panel(item.related_evidence, roles, "相关上下文（不支持结论）"),
    )


def _progression_detail(item: Any, roles: dict[str, str]) -> str:
    if item is None:
        return _detail_section("进展与 PPF", [])
    components = "；".join(
        f"{component.component}: {component.status}（{component.assessment}）"
        for component in item.components
    )
    evidence = [
        pointer for component in item.components for pointer in component.supporting_evidence
    ]
    related = [pointer for component in item.components for pointer in component.related_evidence]
    body = (
        '<article class="plain-card">'
        f"<h3>近期变化：{escape(item.recent_worsening)} · "
        f"PPF：{escape(item.ppf_status)}</h3>"
        f"<p>{escape(item.reasoning_summary)}</p>"
        f"<p><strong>规则：</strong>{escape(item.rule_source)}；"
        f"{escape(item.assessment_window)}</p>"
        f"<p>{escape(components)}</p>{_evidence_panel(evidence, roles)}"
        f"{_evidence_panel(related, roles, '相关上下文（不支持结论）')}</article>"
    )
    return _detail_section("进展与 PPF", [body])


def _bronchoscopy_detail(item: Any, roles: dict[str, str]) -> str:
    if item is None:
        return _detail_section("BAL / 支气管镜", [])
    body = (
        '<article class="plain-card">'
        f'<span class="badge">{escape(item.decision)}</span>'
        f"<h3>{escape(item.clinical_question)}</h3><p>{escape(item.rationale)}</p>"
        f"{_evidence_panel(item.supporting_evidence, roles)}"
        f"{_evidence_panel(item.related_evidence, roles, '相关上下文（不支持结论）')}"
        "</article>"
    )
    return _detail_section("BAL / 支气管镜", [body])


def _chair_answer_card(item: Any, roles: dict[str, str]) -> str:
    return _detail_card(
        item.question_id,
        item.confidence,
        item.answer,
        item.supporting_evidence,
        roles,
        item.specialist_opinion_ids,
    )


def _mapped_finding_card(item: Any, roles: dict[str, str]) -> str:
    domains = "、".join(
        DOMAIN_LABELS.get(str(domain), str(domain)) for domain in item.affected_domains
    )
    return (
        '<article class="plain-card">'
        f'<span class="badge">{escape(item.relationship)}</span>'
        f"<h3>{escape(item.opinion_id)} · {escape(domains)}</h3>"
        f"<p>{escape(item.clinical_effect)}</p>"
        f"{_evidence_panel(item.evidence, roles)}</article>"
    )


def _coverage_zone(reviews: list[Any]) -> str:
    cards = "".join(
        '<article class="plain-card">'
        f'<span class="badge">{escape(REVIEW_STATUS_LABELS.get(item.status, item.status))}</span>'
        f"<h3>{escape(DOMAIN_LABELS.get(str(item.domain), str(item.domain)))}</h3>"
        f"<p>{escape(item.rationale)}</p></article>"
        for item in reviews
    )
    return _zone(
        "coverage",
        "审计",
        "八问处理状态",
        "处理状态不等于必须有结论；不可评价和等待专科也是有效结果。",
        f'<div class="result-grid">{cards}</div>',
    )


def _secondary_detail(item: Any, roles: dict[str, str]) -> str:
    return _detail_card(
        f"{item.cause} · {item.status}",
        item.confidence,
        item.reasoning_summary,
        item.supporting_evidence,
        roles,
        item.specialist_opinion_ids,
        _evidence_panel(item.related_evidence, roles, "相关上下文（不支持结论）"),
    )


def _differential_detail(item: Any, roles: dict[str, str]) -> str:
    extra = _evidence_panel(item.conflicting_evidence, roles, "冲突证据")
    extra += _evidence_panel(item.related_evidence, roles, "相关上下文（不支持结论）")
    return _detail_card(
        f"#{item.rank} {item.diagnosis}",
        item.confidence,
        item.reasoning_summary,
        item.supporting_evidence,
        roles,
        item.specialist_opinion_ids,
        extra,
    )


def _detail_card(
    title: str,
    confidence: str,
    reasoning: str,
    evidence: list[EvidencePointer],
    roles: dict[str, str],
    opinion_ids: list[str],
    extra: str = "",
) -> str:
    opinions = (
        ""
        if not opinion_ids
        else f'<span class="badge">专科意见：{escape(", ".join(opinion_ids))}</span>'
    )
    return (
        '<article class="detail-card"><div class="detail-head">'
        f"<h3>{escape(title)}</h3>{_confidence(confidence)}{opinions}</div>"
        '<div class="detail-body"><div class="reasoning"><strong>推理摘要</strong>'
        f"{escape(reasoning)}</div>{_evidence_panel(evidence, roles)}{extra}</div></article>"
    )


def _gap_card(item: Any, roles: dict[str, str]) -> str:
    gap_label = GAP_LABELS.get(item.gap_type, item.gap_type)
    return (
        '<article class="gap-card">'
        f'<div class="gap-head"><h3>{escape(item.missing_information)}</h3>'
        f'<span class="badge gap-type">{escape(gap_label)}</span></div>'
        '<div class="gap-grid">'
        f'<div class="gap-side available"><strong>已有资料</strong>{escape(item.available_information)}</div>'
        f'<div class="gap-side missing"><strong>真正缺失</strong>{escape(item.missing_information)}</div>'
        "</div>"
        f'<p class="gap-why"><strong>为什么重要：</strong>{escape(item.why_it_matters)}</p>'
        f'<p class="gap-why"><strong>可解锁的决策：</strong>{escape(item.decision_unlocked)}</p>'
        f"{_evidence_panel(item.related_evidence, roles, '查看相关现有资料（不证明缺失）')}"
        "</article>"
    )


def _question_card(item: Any, roles: dict[str, str]) -> str:
    specialty = SPECIALTY_LABELS.get(str(item.specialty), str(item.specialty))
    return (
        '<article class="plain-card">'
        f'<span class="badge">{escape(specialty)}</span><h3>{escape(item.question)}</h3>'
        f"<p>{escape(item.why_it_matters)}</p>"
        f"{_evidence_panel(item.related_evidence, roles, '查看问题相关资料')}</article>"
    )


def _reference_card(item: Any, roles: dict[str, str]) -> str:
    return (
        '<article class="plain-card">'
        f"<h3>{escape(item.observation)}</h3><p>{escape(item.why_confirmation_is_needed)}</p>"
        f"{_evidence_panel(item.related_evidence, roles, '查看待确认资料')}</article>"
    )


def _change_card(item: Any, roles: dict[str, str]) -> str:
    return (
        '<article class="detail-card"><div class="detail-head">'
        f"<h3>{escape(DOMAIN_LABELS.get(str(item.domain), str(item.domain)))}</h3>"
        f'<span class="badge">{escape(REVIEW_STATUS_LABELS.get(item.change_status, item.change_status))}</span>'
        '</div><div class="detail-body">'
        '<div class="change">'
        f"<div><strong>首轮判断</strong><br>{escape(item.initial_view)}</div>"
        '<span class="change-arrow">→</span>'
        f"<div><strong>更新判断</strong><br>{escape(item.updated_view)}</div></div>"
        '<div class="reasoning"><strong>复核原因</strong>'
        f"{escape(item.reason)}</div>"
        f"{_evidence_panel(item.supporting_evidence, roles)}</div></article>"
    )


def _evidence_panel(
    pointers: list[EvidencePointer],
    roles: dict[str, str],
    label: str = "查看证据与定位",
) -> str:
    if not pointers:
        return ""
    items = "".join(_evidence(item, roles) for item in pointers)
    return (
        f'<details class="evidence-panel"><summary>{escape(label)} · {len(pointers)} 条</summary>'
        f'<div class="evidence-list">{items}</div></details>'
    )


def _evidence(pointer: EvidencePointer, roles: dict[str, str]) -> str:
    role = roles.get(pointer.graph_unit_id, "unknown")
    role_label = ROLE_LABELS.get(role, role)
    return (
        f'<div class="evidence role-{escape(role)}">'
        f'<span class="badge">{escape(role_label)}</span> '
        f'<span class="badge">{escape(pointer.graph_unit_id)}</span>'
        f"<blockquote>{escape(pointer.quote)}</blockquote>"
        f"<code>segment · {escape(pointer.segment_id)}</code>"
        f"<code>evidence · {escape(', '.join(pointer.evidence_ids))}</code>"
        f"<code>nodes · {escape(', '.join(pointer.node_ids) or '无')}</code></div>"
    )


def _list_block(title: str, values: list[str]) -> str:
    items = "".join(f"<li>{escape(item)}</li>" for item in values)
    body = f'<ul class="clean">{items}</ul>' if items else "无"
    return f'<div class="plain-card" style="margin-top:14px"><h3>{escape(title)}</h3>{body}</div>'


def _confidence(value: str) -> str:
    label = CONFIDENCE_LABELS.get(value, value)
    return f'<span class="badge confidence-{escape(value)}">{escape(label)}</span>'


def _lowest_confidence(items: list[Any]) -> str:
    if not items:
        return "unknown"
    order = {"unknown": 0, "low": 1, "moderate": 2, "high": 3}
    return min((item.confidence for item in items), key=lambda value: order.get(value, 0))


def _empty() -> str:
    return '<div class="empty">当前没有可展示的内容</div>'
