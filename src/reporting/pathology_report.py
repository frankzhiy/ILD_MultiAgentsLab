"""Human-readable HTML report for pathology agent outputs."""

from html import escape
from pathlib import Path

from src.agents.pathology.models import (
    PathologyDiscussionResponse,
    PathologyInitialAssessment,
)
from src.reporting.specialty_report_common import (
    COMMON_CSS,
    render_guideline_audit,
    render_reasoning_audit,
    report_nav,
)
from src.schemas.specialty_agent_input import SpecialtyCaseInput


def render_pathology_report(
    result: PathologyInitialAssessment | PathologyDiscussionResponse,
    case_input: SpecialtyCaseInput,
    output_path: str | Path,
) -> Path:
    initial = isinstance(result, PathologyInitialAssessment)
    state = result if initial else result.updated_state
    roles = {
        unit.graph_unit.graph_unit_id: unit.evidence_role.value
        for segment in case_input.segments
        for unit in segment.units
    }
    source = state.source_assessment
    formulation = state.pathology_formulation
    sections = [
        _section(
            "核心病理意见",
            [
                _item(
                    "病理材料状态",
                    source.material_status if source else "当前不可评价",
                ),
                _item(
                    "审阅基础",
                    source.review_basis if source else "当前不可评价",
                ),
                _item(
                    "组织学综合",
                    formulation.formulation if formulation else "当前不可评价",
                ),
                _item(
                    "分类状态",
                    formulation.classification_status if formulation else "当前不可评价",
                ),
            ],
            anchor="results",
        ),
        _section("标本与取材", [_specimen(item) for item in state.specimens]),
        _section("形态特征", [_assessment(item) for item in state.morphologic_features]),
        _section("主导、共存及急性模式", [_pattern(item) for item in state.pattern_assessments]),
        _section("病因与替代诊断线索", [_assessment(item) for item in state.etiologic_associations]),
        _section("辅助病理检查", [_assessment(item) for item in state.ancillary_studies]),
        _section("数据缺口", [_gap(item) for item in state.missing_data], anchor="gaps"),
        _section(
            "跨专科协作",
            [
                _item(question.specialty, f"{question.question}；意义：{question.why_it_matters}")
                for question in state.specialist_dependencies
            ],
            anchor="collaboration",
        ),
    ]
    if not initial:
        sections.extend(
            [
                _section(
                    "状态变化",
                    [
                        _item(
                            change.domain,
                            f"{change.initial_view} → {change.updated_view}；{change.reason}",
                        )
                        for change in result.domain_changes
                    ],
                ),
                _section(
                    "主席问题回答",
                    [
                        _item(
                            answer.question_id,
                            f"{answer.answerability}；{answer.answer}",
                        )
                        for answer in result.chair_answers
                    ],
                ),
                _section(
                    "未解决冲突",
                    [_assessment(item) for item in result.unresolved_conflicts],
                ),
            ]
        )
    title = "首轮评估" if initial else "会中响应"
    path = Path(output_path)
    summary = case_input.summary
    audit = render_reasoning_audit(result, roles, path) + render_guideline_audit(result, path)
    path.write_text(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(case_input.case_id)} · 病理科 {title}</title>
<style>{COMMON_CSS}
.hero-inner{{max-width:1180px;margin:auto;padding:42px 24px 38px}}.hero h1{{margin:8px 0 5px;font-size:34px}}.metrics{{display:flex;flex-wrap:wrap;gap:9px;margin-top:20px}}.metric{{padding:5px 11px;border:1px solid #ffffff38;border-radius:999px;background:#ffffff16;font-size:12px}}
section{{padding:22px;margin:18px 0}}h2{{font-size:20px;margin:0 0 12px}}dl{{margin:0}}dt{{font-weight:750;margin-top:10px}}dd{{margin:2px 0 0;color:#475569}}.empty{{color:#64748b}}
</style></head><body>
<header class="hero"><div class="hero-inner"><div style="font-size:12px;letter-spacing:.13em;opacity:.72">ILD 多学科团队 · 病理科</div><h1>{escape(case_input.case_id)} · 病理科{title}</h1><div>病理来源、标本代表性、形态模式与疾病诊断严格分层</div><div class="metrics"><span class="metric">片段 {summary.segment_count}</span><span class="metric">单元 {summary.unit_count}</span><span class="metric">主责 {summary.owned_unit_count}</span><span class="metric">共享 {summary.shared_context_unit_count}</span><span class="metric">跨专科 {summary.collaborative_context_unit_count}</span></div></div></header>
{report_nav()}<main>{''.join(sections)}{audit}</main></body></html>""",
        encoding="utf-8",
    )
    return path


def _section(title: str, rows: list[str], *, anchor: str = "") -> str:
    anchor_html = f' id="{escape(anchor)}"' if anchor else ""
    empty = '<p class="empty">当前无可呈现内容。</p>'
    return f"<section{anchor_html}><h2>{escape(title)}</h2>{''.join(rows) or empty}</section>"


def _item(label: object, value: object) -> str:
    return f"<dl><dt>{escape(str(label))}</dt><dd>{escape(str(value))}</dd></dl>"


def _assessment(item) -> str:
    return _item(item.assessment, f"{item.confidence}；{item.reasoning_summary}")


def _pattern(item) -> str:
    return _item(
        f"{item.role}: {item.pattern}",
        f"{item.status}；{item.confidence}；{item.reasoning_summary}",
    )


def _specimen(item) -> str:
    return _item(
        item.specimen_id,
        f"{item.procedure}，{item.site}；充分性：{item.adequacy}；"
        f"代表性：{item.representativeness}；{item.description}",
    )


def _gap(item) -> str:
    return _item(
        item.missing_information,
        f"{item.why_it_matters}；可解锁：{item.decision_unlocked}",
    )
