from __future__ import annotations
from typing import Any
from src.agents.common.decision_state import SpecialtyJudgmentUpdate
from src.agents.common.team_synthesis import MDTChairResult
from src.agents.mdt_discussion.models import DiscussionTask, SpecialtyAnswerReview, SpecialtyRoundResponse, SpecialtyTaskAnswer

def build_judgment_update_inputs(
    tasks: list[DiscussionTask],
    responses: list[SpecialtyRoundResponse],
    reviews: list[SpecialtyAnswerReview],
) -> dict[str, list[tuple[DiscussionTask, SpecialtyTaskAnswer]]]:
    """Give both answerers and reviewers the information that may affect their state."""

    task_order = {task.task_id: index for index, task in enumerate(tasks)}
    task_by_id = {task.task_id: task for task in tasks}
    answer_context = {
        answer.answer_id: (task_by_id[answer.task_id], answer)
        for response in responses
        for answer in response.answers
    }
    result: dict[str, dict[str, tuple[DiscussionTask, SpecialtyTaskAnswer]]] = {}

    def add(specialty, task, answer) -> None:
        result.setdefault(specialty, {})[task.task_id] = (task, answer)

    for response in responses:
        for answer in response.answers:
            add(response.specialty, task_by_id[answer.task_id], answer)
    conflict_participants: dict[str, set[str]] = {}
    for response in responses:
        for answer in response.answers:
            if task_by_id[answer.task_id].issue_type == "conflict":
                conflict_participants.setdefault(answer.issue_id, set()).add(
                    response.specialty
                )
    for task, answer in answer_context.values():
        for specialty in task.affected_specialties:
            add(specialty, task, answer)
        if task.issue_type != "conflict":
            continue
        for specialty in conflict_participants.get(answer.issue_id, set()):
            add(specialty, task, answer)
    for review in reviews:
        if review.answer_id not in answer_context:
            raise ValueError(
                f"Review references an unknown round answer: {review.answer_id}"
            )
        task, answer = answer_context[review.answer_id]
        add(review.reviewer_specialty, task, answer)
    return {
        specialty: sorted(
            material.values(),
            key=lambda item: task_order[item[0].task_id],
        )
        for specialty, material in result.items()
    }


def decide_discussion_continuation(
    *,
    previous: MDTChairResult,
    current: MDTChairResult,
    round_number: int,
    max_rounds: int,
    responses: list[SpecialtyRoundResponse],
    reviews: list[SpecialtyAnswerReview],
    judgment_updates: list[SpecialtyJudgmentUpdate] | None = None,
) -> dict[str, Any]:
    """Decide round continuation from structured state; never reclassify medicine."""

    team = current.team_synthesis
    changed = sum(p.change_type != "maintain" for u in judgment_updates or [] for p in u.proposals)
    forward = sum(r.outcome in {"request_clarification", "request_corroboration", "flag_incompatibility"} for r in reviews)

    def targets(issues):
        # Compare actual work targets, ignoring punctuation and whitespace; round budget bounds rewording.
        def text(value):
            return "".join(c.lower() for c in value if c.isalnum())
        return {(i.issue_id, text(i.question), text(i.closure_criterion),
                 tuple(sorted((a.specialty, text(a.question)) for a in i.assignments))) for i in issues}

    summary = {"actionable_questions": len(team.issues),
               "actionable_conflicts": sum(d.status == "open" for d in team.disagreements),
               "changed_judgments": changed, "forward_reviews": forward}
    if not team.issues:
        kind, reason = "completed", "当前已无可通过专科回答推进的议题；判断边界及资料需求保留。"
    elif round_number >= max_rounds:
        kind, reason = "budget", f"已达到最多{max_rounds}轮团队讨论。"
    elif not changed and not forward and targets(team.issues) == targets(previous.team_synthesis.issues):
        kind, reason = "no_progress", "本轮未形成新的专科判断或可继续处理的路径。"
    else:
        kind, reason = "pending", ""
    team.stop_kind = kind
    return {**summary, "continue_discussion": not reason, "stop_kind": kind, "stop_reason": reason}
