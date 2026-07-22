from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.agents.mdt_discussion.models import (
    DiscussionEvidenceCandidate,
    DiscussionProposition,
    DiscussionRound,
    DiscussionTask,
)


SPECIALTIES = {
    "pulmonology",
    "thoracic_radiology",
    "rheumatology",
    "pathology",
}
def build_discussion_tasks(
    *,
    chair_result: dict[str, Any],
    clinical_propositions: dict[str, Any],
    local_graphs: dict[str, Any],
    round_number: int,
    previous_rounds: list[DiscussionRound],
) -> list[DiscussionTask]:
    """Route open chair issues directly to their declared specialties."""

    proposition_index = _proposition_index(clinical_propositions)
    graph_index = _graph_index(local_graphs)
    attempted = {
        answer.issue_id
        for discussion_round in previous_rounds
        for response in discussion_round.specialty_responses
        for answer in response.answers
    }
    prior_answers = _prior_answers(previous_rounds)
    tasks: list[DiscussionTask] = []

    for question in chair_result.get("questions") or []:
        issue_id = str(question.get("question_id") or "")
        if not issue_id or question.get("resolution_status") == "resolved":
            continue
        if (
            question.get("resolution_status") == "blocked_by_evidence"
            and issue_id in attempted
        ):
            continue
        targets = _valid_specialties(question.get("target_specialties") or [])
        for specialty in targets:
            tasks.append(
                _task(
                    round_number=round_number,
                    issue_type="question",
                    issue_id=issue_id,
                    specialty=specialty,
                    prompt=str(question.get("question") or ""),
                    current_result=str(question.get("answer_summary") or ""),
                    remaining=str(question.get("remaining_clarification") or ""),
                    why=str(question.get("why_it_matters") or ""),
                    issue=question,
                    propositions=proposition_index,
                    graphs=graph_index,
                    prior_answers=prior_answers.get(issue_id, []),
                )
            )

    for conflict in chair_result.get("conflicts") or []:
        issue_id = str(conflict.get("conflict_id") or "")
        if not issue_id:
            continue
        targets = _valid_specialties(conflict.get("specialties") or [])
        if not targets:
            targets = _valid_specialties(
                position.get("specialty")
                for position in conflict.get("positions") or []
            )
        prompt = "\n".join(
            value
            for value in (
                str(conflict.get("shared_claim") or ""),
                str(conflict.get("why_incompatible") or ""),
            )
            if value
        )
        for specialty in targets:
            tasks.append(
                _task(
                    round_number=round_number,
                    issue_type="conflict",
                    issue_id=issue_id,
                    specialty=specialty,
                    prompt=prompt or str(conflict.get("topic") or ""),
                    current_result=str(conflict.get("topic") or ""),
                    remaining=str(conflict.get("resolution_requirement") or ""),
                    why=str(conflict.get("decision_impact") or ""),
                    issue=conflict,
                    propositions=proposition_index,
                    graphs=graph_index,
                    prior_answers=prior_answers.get(issue_id, []),
                )
            )
    return tasks


def group_tasks_by_specialty(
    tasks: Iterable[DiscussionTask],
) -> dict[str, list[DiscussionTask]]:
    grouped: dict[str, list[DiscussionTask]] = {}
    for task in tasks:
        grouped.setdefault(task.specialty, []).append(task)
    return grouped


def _task(
    *,
    round_number: int,
    issue_type: str,
    issue_id: str,
    specialty: str,
    prompt: str,
    current_result: str,
    remaining: str,
    why: str,
    issue: dict[str, Any],
    propositions: dict[str, list[DiscussionProposition]],
    graphs: dict[str, dict[str, Any]],
    prior_answers: list[dict[str, Any]],
) -> DiscussionTask:
    evidence = _evidence_candidates(issue, propositions, graphs)
    task_id = f"R{round_number:02d}-{issue_id}-{specialty}"
    return DiscussionTask(
        task_id=task_id,
        round_number=round_number,
        issue_type=issue_type,
        issue_id=issue_id,
        specialty=specialty,
        prompt=prompt,
        current_result=current_result,
        remaining_clarification=remaining,
        why_it_matters=why,
        prior_answers=prior_answers,
        specialty_context=_specialty_context(issue),
        evidence_candidates=evidence,
    )


def _evidence_candidates(
    issue: dict[str, Any],
    proposition_index: dict[str, list[DiscussionProposition]],
    graph_index: dict[str, dict[str, Any]],
) -> list[DiscussionEvidenceCandidate]:
    found: dict[str, dict[str, Any]] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            evidence_ref = value.get("evidence_ref")
            if evidence_ref and value.get("evidence_ids") is not None:
                found.setdefault(str(evidence_ref), value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(issue.get("evidence") or {})
    walk(issue.get("answers") or [])
    walk(issue.get("positions") or [])
    candidates = []
    for ref, item in found.items():
        evidence_ids = list(item.get("evidence_ids") or [])
        graph = graph_index.get(str(item.get("graph_unit_id") or ""), {})
        nodes = [
            node
            for node in graph.get("nodes") or []
            if set((node.get("evidence") or {}).get("evidence_ids") or []).intersection(evidence_ids)
        ]
        node_ids = {str(node.get("node_id") or "") for node in nodes}
        candidates.append(
            DiscussionEvidenceCandidate(
                evidence_ref=ref,
                segment_id=str(item.get("segment_id") or ""),
                graph_unit_id=str(item.get("graph_unit_id") or ""),
                evidence_ids=evidence_ids,
                quote=str(item.get("quote") or ""),
                evidence_fragments=[
                    {
                        "evidence_id": block.get("evidence_id"),
                        "text": block.get("text"),
                    }
                    for block in graph.get("evidence_blocks") or []
                    if block.get("evidence_id") in evidence_ids
                ],
                propositions=_unique_propositions(evidence_ids, proposition_index),
                graph_nodes=[
                    {
                        "node_id": node.get("node_id"),
                        "node_type": node.get("node_type"),
                        "semantic_type": node.get("semantic_type"),
                        "label": node.get("label"),
                        "status": node.get("status"),
                        "certainty": node.get("certainty"),
                    }
                    for node in nodes
                ],
                graph_edges=[
                    {
                        "edge_type": edge.get("edge_type"),
                        "source_node_id": edge.get("source_node_id"),
                        "target_node_id": edge.get("target_node_id"),
                    }
                    for edge in graph.get("edges") or []
                    if edge.get("source_node_id") in node_ids
                    or edge.get("target_node_id") in node_ids
                ],
            )
        )
    return candidates


def _graph_index(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(unit.get("graph_unit_id")): unit
        for segment in document.get("segments") or []
        for unit in segment.get("units") or []
        if unit.get("graph_unit_id")
    }


def _unique_propositions(
    evidence_ids: list[str],
    index: dict[str, list[DiscussionProposition]],
) -> list[DiscussionProposition]:
    found: dict[str, DiscussionProposition] = {}
    for evidence_id in evidence_ids:
        for proposition in index.get(str(evidence_id), []):
            found.setdefault(proposition.proposition_id, proposition)
    return list(found.values())


def _proposition_index(
    document: dict[str, Any],
) -> dict[str, list[DiscussionProposition]]:
    result: dict[str, list[DiscussionProposition]] = {}
    for segment in document.get("segments") or []:
        for unit in segment.get("units") or []:
            unit_id = str(unit.get("graph_unit_id") or "")
            for proposition in unit.get("propositions") or []:
                value = DiscussionProposition(
                    proposition_id=f"{unit_id}::{proposition.get('proposition_id')}",
                    concept_text=str(proposition.get("concept_text") or ""),
                    status=str(proposition.get("status") or "unknown"),
                    certainty=str(proposition.get("certainty") or "unknown"),
                    modifiers=list(proposition.get("modifiers") or []),
                )
                for evidence_id in (proposition.get("evidence") or {}).get("evidence_ids") or []:
                    result.setdefault(str(evidence_id), []).append(value)
    return result


def _specialty_context(issue: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for citation in issue.get("source_citations") or []:
        items.append(
            {
                "specialty": citation.get("specialty"),
                "source_ref": citation.get("source_ref"),
                "quote": citation.get("quote"),
            }
        )
    for answer in issue.get("answers") or []:
        items.append(
            {
                "specialty": answer.get("specialty"),
                "relation": answer.get("relation"),
                "answer": answer.get("answer"),
            }
        )
    for position in issue.get("positions") or []:
        items.append(
            {
                "specialty": position.get("specialty"),
                "stance": position.get("stance"),
                "position": position.get("position"),
            }
        )
    return items


def _prior_answers(rounds: list[DiscussionRound]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for discussion_round in rounds:
        for response in discussion_round.specialty_responses:
            for answer in response.answers:
                result.setdefault(answer.issue_id, []).append(
                    {
                        "round_number": discussion_round.round_number,
                        "specialty": response.specialty,
                        "answer": answer.answer,
                        "confidence": answer.confidence,
                        "remaining_limitation": answer.remaining_limitation,
                    }
                )
    return result


def _valid_specialties(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value) in SPECIALTIES))
