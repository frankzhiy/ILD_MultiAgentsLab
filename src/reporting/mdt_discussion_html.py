"""Render the documented discussion as rounds and traceable clinical issues."""

from collections import defaultdict
import re

from src.agents.common.team_synthesis import MDTTeamReport
from src.agents.mdt_discussion.models import MDTDiscussionState

from .mdt_html_parts import details, paragraph, text

SPECIALTY = {
    "pulmonology": "呼吸科", "thoracic_radiology": "胸部影像科",
    "rheumatology": "风湿免疫科", "pathology": "病理科",
}
_OUTCOME = {
    "covered": "已有相关意见", "continue": "继续讨论", "answered": "已处理",
    "bounded": "尚待明确", "deferred": "暂未解决", "merged": "并入其他议题",
}
_ANSWER = {"answered": "已回复", "partially_answered": "部分回答", "not_assessable": "现有资料无法回答"}
_REVIEW = {
    "accept_answer": "接受答复", "accept_boundary": "同意暂不明确",
    "request_clarification": "需进一步澄清", "request_corroboration": "需其他证据佐证",
    "flag_incompatibility": "指出不相容判断", "convert_to_evidence_need": "待核实资料",
}
_CHANGE = {
    "supplement": "补充", "qualify": "加上限定", "revise": "修订",
    "withdraw": "撤回", "create": "新增", "maintain": "维持原判断",
}
_ACCEPT = {"accept": "接受", "accept_with_boundaries": "同意，部分事项待明确", "dissent": "保留异议"}


def _preview(value: str, limit: int) -> str:
    value = value.strip()
    first = re.split(r"(?<=[。！？；])|\n", value, maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit].rstrip("，、；：") + "…"


def _button(label: str, panel_id: str, selected: bool = False) -> str:
    default = " data-default" if selected else ""
    return f'<button type="button" aria-controls="{panel_id}"{default}>{text(label)}</button>'


def _step(title: str, value: str, record: str) -> str:
    return (
        f'<li><h3>{text(title)}</h3>' + paragraph(_preview(value, 145))
        + (details("查看本步记录", record, "inline-detail") if record else "") + "</li>"
    )


def render_discussion(
    report: MDTTeamReport, state: MDTDiscussionState | None, baseline: dict, render_guidelines,
    evidence_blocks: dict[str, str],
) -> str:
    if state is None:
        return paragraph("本次无逐轮讨论记录。")

    histories = state.decision_state.judgments
    versions = {v.version_id: v for history in histories for v in history.versions}

    def entering(round_number):
        opinions = defaultdict(list)
        for history in histories:
            eligible = [v for v in history.versions if v.created_in_round < round_number]
            if not eligible or eligible[-1].change_type == "withdraw":
                continue
            item = eligible[-1].assessment
            opinions[history.specialty].append(
                "<li>" + paragraph(item.statement)
                + details("依据及待明确事项", paragraph(item.medical_basis)
                          + paragraph("；".join(item.limitations))) + "</li>"
            )
        return "".join(
            details(title, "<ul>" + "".join(opinions[specialty]) + "</ul>")
            if opinions[specialty] else paragraph("本阶段未记录专科意见", title + "　")
            for specialty, title in SPECIALTY.items()
        )

    def change_rows(changes):
        rows = []
        for change in changes:
            before = versions.get(change.before_version_id)
            after = versions.get(change.after_version_id)
            body = paragraph(before.assessment.statement, "此前　") if before else ""
            if after and change.change_type not in {"withdraw", "maintain"}:
                body += paragraph(after.assessment.statement, "更新　")
            body += paragraph(change.rationale, "原因　")
            rows.append(details(
                SPECIALTY[change.specialty] + " · " + _CHANGE[change.change_type], body
            ))
        return "".join(rows)

    baseline_opinion = (baseline.get("team_synthesis", {}).get("diagnostic_opinion") or {}).get("diagnosis")
    phase_buttons = [_button("会前意见", "phase-before")]
    stages = [
        '<div id="phase-before"><h2>会前意见</h2>'
        + entering(1) + paragraph(baseline_opinion, "会前综合意见　") + "</div>"
    ]
    for round_index, round_ in enumerate(state.rounds, start=1):
        phase_id = f"phase-{round_index}"
        phase_buttons.append(_button(f"第 {round_.round_number} 轮", phase_id, round_index == 1))
        tasks = defaultdict(list)
        for task in round_.tasks:
            tasks[task.issue_id].append(task)
        answers = {a.task_id: a for response in round_.specialty_responses for a in response.answers}
        reviews = defaultdict(list)
        for review in round_.answer_reviews:
            reviews[review.answer_id].append(review)
        synthesis = round_.chair_result.get("team_synthesis", {})
        dispositions = synthesis.get("issue_dispositions", [])
        changes = [c for c in state.decision_state.change_events if c.round_number == round_.round_number]

        issue_buttons, issue_panels = [], []
        for issue_index, (issue_id, assigned) in enumerate(tasks.items(), start=1):
            first = assigned[0]
            panel_id = f"{phase_id}-issue-{issue_index}"
            related = [
                d for d in dispositions
                if d["issue_id"] == issue_id or issue_id in d.get("linked_issue_ids", [])
            ]
            direct = next((d for d in related if d["issue_id"] == issue_id), None)
            raisers = "、".join(dict.fromkeys(SPECIALTY[s] for t in assigned for s in t.raised_by)) or "主持人"
            targets = "、".join(dict.fromkeys(SPECIALTY[t.specialty] for t in assigned))
            prompt = first.prompt or f"议题 {issue_index}"
            issue_buttons.append(_button(_preview(prompt, 31), panel_id))
            steps = []
            for task in assigned:
                task_raisers = "、".join(SPECIALTY[s] for s in task.raised_by) or "主持人"
                steps.append(_step(
                    f"提问｜{task_raisers}", task.prompt,
                    paragraph(task.prompt) + paragraph(task.why_it_matters, "讨论目的　"),
                ))
                answer = answers.get(task.task_id)
                if answer is None:
                    steps.append(_step(f"答复｜{SPECIALTY[task.specialty]}", "本轮未记录答复。", ""))
                    continue
                excerpts = {}
                for use in answer.evidence_uses:
                    if use.evidence_ids and all(key in evidence_blocks for key in use.evidence_ids):
                        excerpts.update((key, evidence_blocks[key]) for key in use.evidence_ids)
                    elif use.quote:
                        excerpts.setdefault(use.evidence_ref, use.quote)
                response = (
                    paragraph(answer.answer)
                    + paragraph(_ANSWER[answer.answerability], "答复情况　")
                    + paragraph(answer.medical_basis, "依据　")
                    + paragraph(answer.remaining_limitation, "尚待明确　")
                    + (details("查看相关资料", "".join(
                        f"<blockquote>{text(quote)}</blockquote>" for quote in excerpts.values()
                    ), "inline-detail") if excerpts else "")
                    + render_guidelines([g.model_dump() for g in answer.guideline_evidence], "答复引用")
                    + "".join(paragraph(r.rationale, SPECIALTY[r.reviewer_specialty] + " · " + _REVIEW[r.outcome] + "　")
                              for r in reviews[answer.answer_id])
                )
                steps.append(_step(f"答复｜{SPECIALTY[task.specialty]}", answer.answer, response))

            disposition = direct or (related[0] if related else None)
            result = disposition["rationale"] if disposition else "本轮未记录该议题的处理意见。"
            records = "".join(paragraph(d["rationale"], _OUTCOME[d["outcome"]] + "　") for d in related)
            impact = change_rows(c for c in changes if issue_id in c.trigger_issue_ids)
            steps.append(_step("本轮处理", result, records + impact))
            issue_panels.append(
                f'<article id="{panel_id}" class="issue-panel">'
                f'<h2>{text(_preview(prompt, 55))}</h2>'
                + f'<p class="issue-route">{text(raisers + " → " + targets)}</p>'
                + '<ol class="steps">' + "".join(steps) + "</ol></article>"
            )

        opinion = (synthesis.get("diagnostic_resolution") or {}).get("primary_diagnosis")
        orphan_changes = [c for c in changes if not set(c.trigger_issue_ids).intersection(tasks)]
        round_summary = details(
            "本轮主要诊断",
            paragraph(opinion) + paragraph(round_.round_decision.get("stop_reason"))
            + (details("其他专科意见调整", change_rows(orphan_changes)) if orphan_changes else ""),
            "round-summary",
        )
        stages.append(
            f'<div id="{phase_id}"><div class="discussion-layout">'
            '<nav class="issue-nav" aria-label="本轮议题" data-switch><h2>本轮议题</h2>'
            + "".join(issue_buttons)
            + details("查看本轮讨论前的各科意见", entering(round_.round_number), "inline-detail")
            + "</nav><div>" + ("".join(issue_panels) or paragraph("本轮未记录单独议题。"))
            + round_summary + "</div></div></div>"
        )

    accepted = "".join(
        paragraph(r.rationale, SPECIALTY[r.specialty] + " · " + _ACCEPT[r.decision] + "　")
        + paragraph("；".join(r.boundaries), "尚待明确　") for r in report.team_synthesis.acceptance_records
    )
    phase_buttons.append(_button("最终意见", "phase-final"))
    original = report.team_synthesis.diagnostic_opinion
    stages.append(
        '<div id="phase-final"><h2>最终意见</h2>'
        + '<p><a href="#result">查看诊断结果</a></p>'
        + (details("主持人原始全病例意见", paragraph(original.diagnosis), "inline-detail") if original else "")
        + paragraph(report.stop_reason, "讨论结论　") + accepted
        + "".join(paragraph(note, "不同意见　") for note in report.team_synthesis.dissent)
        + "</div>"
    )
    return (
        '<nav class="phase-nav" aria-label="讨论阶段" data-switch>'
        + "".join(phase_buttons) + "</nav>" + "".join(stages)
    )
