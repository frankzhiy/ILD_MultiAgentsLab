"""Human-readable HTML report for rheumatology agent outputs."""

from html import escape
from pathlib import Path

from src.agents.rheumatology.models import (
    RheumatologyDiscussionResponse,
    RheumatologyInitialAssessment,
)
from src.schemas.specialty_agent_input import SpecialtyCaseInput


def render_rheumatology_report(
    result: RheumatologyInitialAssessment | RheumatologyDiscussionResponse,
    case_input: SpecialtyCaseInput,
    output_path: str | Path,
) -> Path:
    initial = isinstance(result, RheumatologyInitialAssessment)
    state = result if initial else result.updated_state
    formulation = state.rheumatic_disease_formulation
    sections = [
        _section("核心结论", [
            _item("风湿病工作诊断", formulation.leading_diagnosis if formulation else "当前不可评价"),
            _item("分类状态", formulation.classification_status if formulation else "insufficient_data"),
            _item("ILD 风湿归因", state.ild_attribution.attribution_strength if state.ild_attribution else "not_assessable"),
            _item("活动性与风险", _risk_text(state)),
        ]),
        _section("自身免疫表型", [_assessment(item) for item in state.autoimmune_manifestations]),
        _section("血清学解释", [_assessment(item) for item in state.serologic_findings]),
        _section("鉴别诊断", [_assessment(item) for item in (formulation.differential_diagnoses if formulation else [])]),
        _section("数据缺口", [_gap(item) for item in state.missing_data]),
        _section("专科问题", [_item(question.specialty, question.question) for question in state.specialist_dependencies]),
    ]
    if not initial:
        sections.extend([
            _section("状态变化", [_item(change.domain, f"{change.initial_view} → {change.updated_view}；{change.reason}") for change in result.domain_changes]),
            _section("主席问题回答", [_item(answer.question_id, answer.answer) for answer in result.chair_answers]),
            _section("未解决冲突", [_assessment(item) for item in result.unresolved_conflicts]),
        ])
    title = "首轮评估" if initial else "会中响应"
    path = Path(output_path)
    path.write_text(
        f"""<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>{escape(case_input.case_id)} · 风湿免疫 {title}</title>
<style>body{{max-width:960px;margin:36px auto;padding:0 20px;font:15px/1.65 -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;color:#172033;background:#f6f8fb}}section{{background:white;border:1px solid #dbe5ef;border-radius:12px;padding:18px 22px;margin:16px 0}}h1{{margin-bottom:0}}h2{{font-size:19px;margin:0 0 10px}}dl{{margin:0}}dt{{font-weight:700;margin-top:10px}}dd{{margin:2px 0 0;color:#475569}}.empty{{color:#64748b}}</style>
<h1>风湿免疫科 {title}</h1><p>病例：{escape(case_input.case_id)}</p>{''.join(sections)}</html>""",
        encoding="utf-8",
    )
    return path


def _section(title: str, rows: list[str]) -> str:
    return f"<section><h2>{escape(title)}</h2>{''.join(rows) or '<p class=\"empty\">当前无可呈现内容。</p>'}</section>"


def _item(label: object, value: object) -> str:
    return f"<dl><dt>{escape(str(label))}</dt><dd>{escape(str(value))}</dd></dl>"


def _assessment(item) -> str:
    return _item(getattr(item, "assessment", "判断"), getattr(item, "reasoning_summary", ""))


def _gap(item) -> str:
    return _item(item.missing_information, f"{item.why_it_matters}；可解锁：{item.decision_unlocked}")


def _risk_text(state) -> str:
    if not state.activity_and_risk:
        return "当前不可评价"
    return f"活动性：{state.activity_and_risk.disease_activity}；ILD 风险：{state.activity_and_risk.ild_risk}"
