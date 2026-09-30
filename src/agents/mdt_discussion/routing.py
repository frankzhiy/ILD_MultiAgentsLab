from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.agents.common.decision_state import (
    MultiSpecialtyDecisionState,
    active_judgments,
)
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
    decision_state: MultiSpecialtyDecisionState | None = None,
) -> list[DiscussionTask]:
    """Route the current synthesis directly; never reconstruct legacy chair sections."""
    from src.agents.common.team_synthesis import MDTChairResult
    from src.agents.mdt_chair.agent import build_semantic_evidence_catalog

    team = MDTChairResult.model_validate(chair_result).team_synthesis
    propositions = _proposition_index(clinical_propositions)
    graphs = _graph_index(local_graphs)
    prior_answers = _prior_answers(previous_rounds)
    catalog = build_semantic_evidence_catalog(clinical_propositions, local_graphs)
    evidence = _evidence_candidates({"evidence": [
        dict(evidence_ref=ref, graph_unit_id=ref, segment_id=item["segment_id"],
             evidence_ids=list(item["evidence_blocks"]), quote="\n".join(item["evidence_blocks"].values()))
        for ref, item in catalog.items() if item["evidence_blocks"]
    ]}, propositions, graphs)
    return [DiscussionTask(
        task_id=f"R{round_number:02d}-{issue.issue_id}-{assignment.specialty}",
        round_number=round_number, issue_type="question", issue_id=issue.issue_id,
        specialty=assignment.specialty, prompt=assignment.question,
        origin=issue.origin, raised_by=issue.raised_by,
        remaining_clarification=issue.closure_criterion, why_it_matters=issue.decision_impact,
        affected_specialties=issue.affected_specialties,
        prior_answers=prior_answers.get(issue.issue_id, []),
        specialty_context=[team.source_catalog[ref] for ref in issue.source_refs],
        evidence_candidates=evidence,
        active_judgments=_active_judgment_view(decision_state, assignment.specialty),
    ) for issue in team.issues for assignment in issue.assignments]


def group_tasks_by_specialty(
    tasks: Iterable[DiscussionTask],
) -> dict[str, list[DiscussionTask]]:
    grouped: dict[str, list[DiscussionTask]] = {}
    for task in tasks:
        grouped.setdefault(task.specialty, []).append(task)
    return grouped


def _active_judgment_view(
    state: MultiSpecialtyDecisionState | None,
    specialty: str,
) -> list[dict[str, Any]]:
    if state is None:
        return []
    return [
        {
            "judgment_id": item.judgment_id,
            "version_id": item.version_id,
            "statement": item.assessment.statement,
            "status": item.assessment.status,
            "assessability": item.assessment.assessability,
            "direction": item.assessment.direction,
            "confidence": item.assessment.confidence,
            "clinical_role": item.assessment.clinical_role,

            "medical_basis": item.assessment.medical_basis,
            "limitations": item.assessment.limitations,
            "conditions": item.assessment.conditions.model_dump(mode="json"),
        }
        for item in active_judgments(state, specialty)
    ]


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
                graph_unit_id = str(value.get("graph_unit_id") or "")
                key = graph_unit_id or str(evidence_ref)
                current = found.setdefault(key, {
                    **value,
                    "evidence_ref": key,
                    "evidence_ids": [],
                    "quotes": [],
                })
                current["evidence_ids"] = list(dict.fromkeys([
                    *current["evidence_ids"],
                    *(value.get("evidence_ids") or []),
                ]))
                quote = str(value.get("quote") or "").strip()
                if quote and quote not in current["quotes"]:
                    current["quotes"].append(quote)
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
                quote="\n".join(item.get("quotes") or []),
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
    _validate_evidence_candidates(candidates)
    return candidates


def _validate_evidence_candidates(
    candidates: list[DiscussionEvidenceCandidate],
) -> None:
    missing_source_text = [
        candidate.evidence_ref
        for candidate in candidates
        if not candidate.quote.strip()
        and not any(
            str(fragment.get("text") or "").strip()
            for fragment in candidate.evidence_fragments
        )
    ]
    if missing_source_text:
        raise ValueError(
            "Discussion evidence is missing source text: "
            f"{sorted(missing_source_text)}"
        )


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
