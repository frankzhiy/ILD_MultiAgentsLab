from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any

from src.agents.mdt_chair.models import (
    AssessmentBoundary,
    ChairEvidenceBundle,
    MDTChairIntegration,
    QuestionAnswer,
)
from src.agents.mdt_discussion.models import DiscussionRound, SpecialtyRoundResponse


def append_round_responses(
    specialty_outputs: dict[str, dict[str, Any]],
    responses: list[SpecialtyRoundResponse],
) -> dict[str, dict[str, Any]]:
    """Project task answers into the existing formal specialty-output contract."""

    updated = deepcopy(specialty_outputs)
    for response in responses:
        conclusions = updated[response.specialty]["professional_conclusions"]["conclusions"]
        for answer in response.answers:
            evidence = {
                "supporting": [],
                "weakening": [],
                "discriminating": [],
                "background": [],
            }
            for use in answer.evidence_uses:
                evidence[use.effect].extend(
                    {"evidence_ids": [evidence_id]}
                    for evidence_id in use.evidence_ids
                )
            status = (
                "not_assessable"
                if answer.answerability == "not_assessable"
                else "supported"
                if answer.confidence == "high"
                else "favored"
                if answer.confidence == "moderate"
                else "possible"
            )
            interpretation = "；".join(
                use.interpretation for use in answer.evidence_uses if use.interpretation
            )
            medical_basis = answer.medical_basis
            if interpretation and interpretation not in medical_basis:
                medical_basis = f"{medical_basis}；证据解释：{interpretation}"
            conclusions.append(
                {
                    "conclusion_id": answer.answer_id,
                    "role": "primary",
                    "conclusion_type": "other",
                    "statement": f"对议题 {answer.issue_id} 的会中回应：{answer.answer}",
                    "status": status,
                    "medical_basis": medical_basis,
                    "decision_impact": (
                        "用于主持人更新该议题的解决状态。"
                        if answer.answerability == "answered"
                        else "用于限定该议题当前可解决的范围。"
                    ),
                    "evidence": evidence,
                    "guideline_evidence": [
                        pointer.model_dump(mode="json")
                        for pointer in answer.guideline_evidence
                    ],
                    "limitations": (
                        [answer.remaining_limitation]
                        if answer.remaining_limitation
                        else []
                    ),
                }
            )
    return updated


def reconcile_discussion_references(
    result: MDTChairIntegration,
    previous: MDTChairIntegration,
    responses: list[SpecialtyRoundResponse],
    bundle: Any,
) -> MDTChairIntegration:
    """Rebuild discussion question, answer, and evidence-need refs in program code."""

    current_by_identity = {
        (item.specialty, item.source_type, item.source_path, item.quote): ref
        for ref, item in bundle.source_registry.items()
    }
    previous_citations = {
        citation.source_ref: citation
        for item in (
            list(previous.integrated_conclusions)
            + list(previous.assessment_boundaries)
            + list(previous.questions)
            + [answer for question in previous.questions for answer in question.answers]
            + list(previous.evidence_needs)
            + [position for conflict in previous.conflicts for position in conflict.positions]
        )
        for citation in item.source_citations
    }

    def rebase(refs: list[str]) -> list[str]:
        rebased = []
        for ref in refs:
            citation = previous_citations.get(ref)
            if citation is None:
                raise ValueError(f"Cannot rebase prior specialty source ref: {ref}")
            current = current_by_identity.get(
                (
                    citation.specialty,
                    citation.source_type,
                    citation.source_path,
                    citation.quote,
                )
            )
            if current is None:
                raise ValueError(f"Prior specialty source is absent from current round: {ref}")
            if current not in rebased:
                rebased.append(current)
        return rebased

    answer_ref_by_id = {
        metadata.get("conclusion_id"): ref
        for ref, metadata in bundle.source_metadata.items()
        if metadata.get("conclusion_id")
    }
    answers_by_issue: dict[str, list[tuple[Any, str]]] = {}
    for response in responses:
        for answer in response.answers:
            source_ref = answer_ref_by_id.get(answer.answer_id)
            if source_ref is None:
                raise ValueError(
                    f"Discussion answer source was not registered: {answer.answer_id}"
                )
            answers_by_issue.setdefault(answer.issue_id, []).append((answer, source_ref))

    active_previous = [
        question
        for question in previous.questions
        if question.question_id in answers_by_issue
    ]
    candidates = list(result.questions)
    pairs = sorted(
        (
            SequenceMatcher(None, old.question, candidate.question).ratio(),
            old_index,
            candidate_index,
        )
        for old_index, old in enumerate(active_previous)
        for candidate_index, candidate in enumerate(candidates)
    )
    matched: dict[int, int] = {}
    used_candidates: set[int] = set()
    for _, old_index, candidate_index in reversed(pairs):
        if old_index not in matched and candidate_index not in used_candidates:
            matched[old_index] = candidate_index
            used_candidates.add(candidate_index)

    questions = []
    for old_index, old in enumerate(active_previous):
        question = deepcopy(candidates[matched[old_index]]) if old_index in matched else deepcopy(old)
        question.source_refs = rebase(old.source_refs)
        question.related_evidence_need_source_refs = rebase(
            old.related_evidence_need_source_refs
        )
        prior_answers = []
        for answer in old.answers:
            answer = deepcopy(answer)
            answer.source_refs = rebase(answer.source_refs)
            prior_answers.append(answer)
        round_answers = [
            QuestionAnswer(
                source_refs=[source_ref],
                relation=(
                    "direct_answer"
                    if answer.answerability == "answered"
                    else "partial_answer"
                    if answer.answerability == "partially_answered"
                    else "evidence_boundary"
                ),
                answer=answer.answer,
            )
            for answer, source_ref in answers_by_issue[old.question_id]
        ]
        question.answers = prior_answers + round_answers
        questions.append(question)
    result.questions = questions

    evidence_needs = []
    for need in previous.evidence_needs:
        need = deepcopy(need)
        need.source_refs = rebase(need.source_refs)
        evidence_needs.append(need)
    result.evidence_needs = evidence_needs
    return result


def stabilize_integration_ids(
    result: MDTChairIntegration,
    previous: MDTChairIntegration,
) -> MDTChairIntegration:
    """Preserve matching IDs and allocate all new IDs in program code."""

    _stabilize(
        result.integrated_conclusions,
        previous.integrated_conclusions,
        "conclusion_id",
        "IC",
        lambda item: frozenset(item.source_refs),
    )
    _stabilize(
        result.assessment_boundaries,
        previous.assessment_boundaries,
        "boundary_id",
        "B",
        lambda item: frozenset(item.source_refs),
    )
    _stabilize(
        result.questions,
        previous.questions,
        "question_id",
        "Q",
        lambda item: frozenset(item.source_refs),
    )
    _stabilize(
        result.evidence_needs,
        previous.evidence_needs,
        "need_id",
        "EN",
        lambda item: frozenset(item.source_refs),
    )
    _stabilize(
        result.conflicts,
        previous.conflicts,
        "conflict_id",
        "CF",
        lambda item: frozenset(
            ref for position in item.positions for ref in position.source_refs
        ),
    )
    _relink(result)
    return result


def move_stalled_issues_to_boundaries(
    result: MDTChairIntegration,
    previous_rounds: list[DiscussionRound],
    current_responses: list[SpecialtyRoundResponse],
) -> MDTChairIntegration:
    """Stop loops: evidence-blocked once, or still unresolved after two attempts."""

    attempts: dict[str, set[int]] = {}
    for discussion_round in previous_rounds:
        for response in discussion_round.specialty_responses:
            for answer in response.answers:
                attempts.setdefault(answer.issue_id, set()).add(discussion_round.round_number)
    for response in current_responses:
        for answer in response.answers:
            attempts.setdefault(answer.issue_id, set()).add(response.round_number)

    kept_questions = []
    for question in result.questions:
        count = len(attempts.get(question.question_id, set()))
        stalled = (
            question.resolution_status == "blocked_by_evidence" and count >= 1
        ) or (question.resolution_status != "resolved" and count >= 2)
        if not stalled:
            kept_questions.append(question)
            continue
        result.assessment_boundaries.append(
            AssessmentBoundary(
                source_refs=question.source_refs,
                source_citations=question.source_citations,
                evidence=question.evidence,
                guideline_evidence=question.guideline_evidence,
                topic=question.question,
                scope=_scope_for_specialties(question.target_specialties),
                status="not_assessable",
                statement=question.answer_summary,
                reason=(
                    "现有病例证据缺口已明确，继续重复讨论不会产生新的患者事实。"
                    if question.resolution_status == "blocked_by_evidence"
                    else "相关专科已连续两轮处理该问题，仍未形成可解决结论。"
                ),
                decision_impact=question.why_it_matters,
                related_evidence_need_source_refs=question.related_evidence_need_source_refs,
                specialties=question.target_specialties,
            )
        )
    result.questions = kept_questions

    kept_conflicts = []
    for conflict in result.conflicts:
        count = len(attempts.get(conflict.conflict_id, set()))
        if count < 2:
            kept_conflicts.append(conflict)
            continue
        source_refs = list(
            dict.fromkeys(
                ref for position in conflict.positions for ref in position.source_refs
            )
        )
        citations = []
        guideline_evidence = []
        evidence = {
            role: []
            for role in ("supporting", "weakening", "discriminating", "background")
        }
        for position in conflict.positions:
            citations.extend(position.source_citations)
            guideline_evidence.extend(position.guideline_evidence)
            for role in evidence:
                evidence[role].extend(getattr(position.evidence, role))
        result.assessment_boundaries.append(
            AssessmentBoundary(
                source_refs=source_refs,
                source_citations=citations,
                evidence=ChairEvidenceBundle(**evidence),
                guideline_evidence=guideline_evidence,
                topic=conflict.topic,
                scope=_scope_for_conflict(conflict.conflict_domain),
                status="not_assessable",
                statement=f"当前不能消解以下跨专科分歧：{conflict.shared_claim}",
                reason="冲突相关专科已连续两轮处理，现有证据仍不足以判定哪一立场成立。",
                decision_impact=conflict.decision_impact,
                related_evidence_need_source_refs=conflict.related_evidence_need_source_refs,
                specialties=conflict.specialties,
            )
        )
    result.conflicts = kept_conflicts
    return result


def _scope_for_specialties(specialties: list[str]) -> str:
    if len(specialties) != 1:
        return "other"
    return {
        "pulmonology": "clinical",
        "thoracic_radiology": "imaging",
        "rheumatology": "rheumatology",
        "pathology": "pathology",
    }.get(specialties[0], "other")


def _scope_for_conflict(domain: str) -> str:
    return {
        "morphologic_interpretation": "imaging",
        "etiologic_attribution": "etiology",
        "severity_or_trajectory": "progression",
    }.get(domain, "other")


def _stabilize(current, previous, id_field, prefix, key) -> None:
    previous_by_key = {key(item): getattr(item, id_field) for item in previous if key(item)}
    used = {value for value in previous_by_key.values() if value}
    next_number = max((_numeric_id(value, prefix) for value in used), default=0) + 1
    for item in current:
        stable = previous_by_key.get(key(item))
        if stable:
            setattr(item, id_field, stable)
            continue
        while f"{prefix}{next_number:03d}" in used:
            next_number += 1
        value = f"{prefix}{next_number:03d}"
        setattr(item, id_field, value)
        used.add(value)
        next_number += 1


def _numeric_id(value: str, prefix: str) -> int:
    suffix = str(value).removeprefix(prefix)
    return int(suffix) if suffix.isdigit() else 0


def _relink(result: MDTChairIntegration) -> None:
    def ids_for(refs, items, field):
        selected = set(refs)
        return [getattr(item, field) for item in items if selected.intersection(item.source_refs)]

    for boundary in result.assessment_boundaries:
        boundary.related_evidence_need_ids = ids_for(
            boundary.related_evidence_need_source_refs,
            result.evidence_needs,
            "need_id",
        )
    for question in result.questions:
        question.related_evidence_need_ids = ids_for(
            question.related_evidence_need_source_refs,
            result.evidence_needs,
            "need_id",
        )
    for conflict in result.conflicts:
        conflict.related_question_ids = ids_for(
            conflict.related_question_source_refs,
            result.questions,
            "question_id",
        )
        conflict.related_evidence_need_ids = ids_for(
            conflict.related_evidence_need_source_refs,
            result.evidence_needs,
            "need_id",
        )
