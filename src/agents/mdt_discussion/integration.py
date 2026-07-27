from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any

from src.agents.mdt_chair.models import (
    ChairEvidenceBundle,
    MDTChairIntegration,
    QuestionAnswer,
)
from src.agents.mdt_discussion.models import (
    SpecialtyAnswerReview,
    SpecialtyRoundResponse,
)


def append_round_responses(
    specialty_outputs: dict[str, dict[str, Any]],
    responses: list[SpecialtyRoundResponse],
    reviews: list[SpecialtyAnswerReview] | None = None,
) -> dict[str, dict[str, Any]]:
    """Project task answers into the two-section specialty-output contract."""

    updated = {
        specialty: _current_specialty_output(output)
        for specialty, output in deepcopy(specialty_outputs).items()
    }
    reviews = reviews or []
    answer_round = {
        answer.answer_id: response.round_number
        for response in responses
        for answer in response.answers
    }
    for response in responses:
        specialty_output = updated[response.specialty]
        assessments = specialty_output["specialty_assessments"]
        assessment_items = assessments["assessments"]
        for answer in response.answers:
            evidence = {
                "supporting": [],
                "weakening": [],
                "discriminating": [],
                "background": [],
            }
            for use in answer.evidence_uses:
                evidence[use.effect].append({
                    "segment_id": use.segment_id,
                    "graph_unit_id": use.graph_unit_id,
                    "evidence_ids": use.evidence_ids,
                    "proposition_ids": use.proposition_ids,
                    "node_ids": [
                        node.get("node_id")
                        for node in use.graph_nodes
                        if node.get("node_id")
                    ],
                    "quote": use.quote,
                })
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
            assessment_items.append(
                {
                    "assessment_id": answer.answer_id,
                    "role": "primary",
                    "assessment_type": "other",
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
                    "origin": "discussion_answer",
                    "answered_question_id": answer.issue_id,
                }
            )
    # Only the original requester may convert an issue into a non-blocking
    # evidence need. Follow-ups continue the same stable question instead.
    latest_reviews = {
        (review.issue_id, review.reviewer_specialty): review for review in reviews
    }
    for review in latest_reviews.values():
        reviewer_output = updated.get(review.reviewer_specialty)
        if reviewer_output is None:
            continue
        assessments = reviewer_output["specialty_assessments"]
        if review.evidence_gap is not None:
            gap = review.evidence_gap.model_dump(mode="json")
            gap["_discussion_round"] = answer_round[review.answer_id]
            gap["_discussion_issue_id"] = review.issue_id
            gap["_discussion_disposition"] = review.outcome
            assessments["evidence_gaps"].append(gap)
    return updated


def _current_specialty_output(output: dict[str, Any]) -> dict[str, Any]:
    if "specialty_assessments" in output:
        return output
    legacy = output.get("professional_conclusions")
    if not isinstance(legacy, dict):
        raise ValueError("Specialty output is missing specialty_assessments")
    assessments = []
    for item in legacy.get("conclusions") or []:
        current = dict(item)
        current["assessment_id"] = current.pop("conclusion_id")
        current["assessment_type"] = current.pop("conclusion_type")
        assessments.append(current)
    return {
        "specialty_assessments": {
            "specialty_question": legacy.get("specialty_question"),
            "assessability": legacy.get("assessability"),
            "assessments": assessments,
            "evidence_gaps": list(legacy.get("evidence_gaps") or []),
            "boundaries": list(legacy.get("boundaries") or []),
        },
        "interspecialty_questions": {
            "questions": list(legacy.get("interspecialty_questions") or [])
        },
    }


def apply_review_outcomes(
    result: MDTChairIntegration,
    reviews: list[SpecialtyAnswerReview],
) -> MDTChairIntegration:
    """Programmatically expose review and closure state; the chair cannot invent it."""

    by_issue: dict[str, list[SpecialtyAnswerReview]] = {}
    for review in reviews:
        by_issue.setdefault(review.issue_id, []).append(review)
    for question in result.questions:
        all_issue_reviews = by_issue.get(question.question_id, [])
        if not all_issue_reviews:
            continue
        latest_by_reviewer = {
            review.reviewer_specialty: review for review in all_issue_reviews
        }
        issue_reviews = list(latest_by_reviewer.values())
        question.reviewed_by = list(dict.fromkeys(
            review.reviewer_specialty for review in issue_reviews
        ))
        question.awaiting_review_specialties = [
            specialty
            for specialty in question.raised_by
            if specialty not in question.reviewed_by
        ]
        outcomes = {review.outcome for review in issue_reviews}
        if question.awaiting_review_specialties:
            question.review_status = "awaiting_review"
            question.discussion_status = "awaiting_requester_review"
            question.closure_type = None
        elif "flag_incompatibility" in outcomes:
            question.review_status = "incompatibility_flagged"
            has_formal_conflict = any(
                question.question_id in conflict.related_question_ids
                for conflict in result.conflicts
            )
            question.discussion_status = (
                "awaiting_conflict_assessment"
                if has_formal_conflict
                else "clarification_in_progress"
            )
            question.closure_type = None
            if not has_formal_conflict:
                question.answer_status = "partially_answered"
        elif "request_corroboration" in outcomes:
            question.review_status = "corroboration_requested"
            question.discussion_status = "awaiting_corroboration"
            question.closure_type = None
            question.answer_status = "partially_answered"
            follow_up = next(
                review.follow_up_question
                for review in issue_reviews
                if review.outcome == "request_corroboration"
                and review.follow_up_question is not None
            )
            question.remaining_clarification = follow_up.question
            target = follow_up.target_specialty.value
            if target not in question.target_specialties:
                question.target_specialties.append(target)
        elif "request_clarification" in outcomes:
            question.review_status = "clarification_requested"
            question.discussion_status = "clarification_in_progress"
            question.closure_type = None
            question.answer_status = "partially_answered"
            follow_up = next(
                review.follow_up_question
                for review in issue_reviews
                if review.outcome == "request_clarification"
                and review.follow_up_question is not None
            )
            question.remaining_clarification = follow_up.question
        elif "convert_to_evidence_need" in outcomes:
            question.review_status = "converted_to_evidence_need"
            question.discussion_status = "closed_this_round"
            question.closure_type = "converted_to_evidence_need"
            question.answer_status = "answered"
        else:
            question.discussion_status = "closed_this_round"
            question.review_status = (
                "accepted_boundary"
                if "accept_boundary" in outcomes
                else "accepted"
            )
            question.answer_status = (
                "boundary_answered"
                if "accept_boundary" in outcomes
                else "answered"
            )
            all_outcomes = {review.outcome for review in all_issue_reviews}
            if "request_corroboration" in all_outcomes:
                question.closure_type = "corroborated_answer"
            elif "request_clarification" in all_outcomes:
                question.closure_type = "clarified_answer"
            elif "accept_boundary" in outcomes:
                question.closure_type = "boundary_answer"
            else:
                question.closure_type = "explicit_answer"
    result.questions = [
        question
        for question in result.questions
        if question.answer_status in {"unanswered", "partially_answered"}
    ]
    return result


def build_review_dispositions(
    result: MDTChairIntegration,
    reviews: list[SpecialtyAnswerReview],
) -> dict[str, dict[str, Any]]:
    """Turn requester reviews into deterministic issue destinations."""

    latest = {
        (review.issue_id, review.reviewer_specialty): review for review in reviews
    }
    dispositions = {}
    for question in result.questions:
        issue_reviews = [
            review
            for (issue_id, _), review in latest.items()
            if issue_id == question.question_id
        ]
        if not issue_reviews:
            continue
        reviewed_by = {review.reviewer_specialty for review in issue_reviews}
        outcomes = {review.outcome for review in issue_reviews}
        if any(specialty not in reviewed_by for specialty in question.raised_by):
            destination = "awaiting_requester_review"
        elif "flag_incompatibility" in outcomes:
            destination = "conflict_assessment"
        elif "request_corroboration" in outcomes:
            destination = "continue_corroboration"
        elif "request_clarification" in outcomes:
            destination = "continue_clarification"
        elif "convert_to_evidence_need" in outcomes:
            destination = "evidence_need"
        elif "accept_boundary" in outcomes:
            destination = "assessment_boundary"
        else:
            destination = "answered"
        dispositions[question.question_id] = {
            "destination": destination,
            "question_source_refs": list(question.source_refs),
            "outcomes": sorted(outcomes),
            "reviews": [review.model_dump(mode="json") for review in issue_reviews],
        }
    return dispositions


def decide_discussion_continuation(
    *,
    previous: MDTChairIntegration,
    current: MDTChairIntegration,
    round_number: int,
    max_rounds: int,
    responses: list[SpecialtyRoundResponse],
    reviews: list[SpecialtyAnswerReview],
) -> dict[str, Any]:
    """Decide round continuation from structured state; never reclassify medicine."""

    actionable_questions = [
        question
        for question in current.questions
        if question.answer_status in {"unanswered", "partially_answered"}
        and question.discussion_status
        not in {"awaiting_requester_review", "awaiting_conflict_assessment"}
    ]
    actionable_count = len(actionable_questions) + len(current.conflicts)
    summary = {
        "actionable_questions": len(actionable_questions),
        "actionable_conflicts": len(current.conflicts),
        "changed_answers": sum(
            answer.changed_from_previous
            for response in responses
            for answer in response.answers
        ),
        "forward_reviews": sum(
            review.outcome
            in {
                "request_clarification",
                "request_corroboration",
                "flag_incompatibility",
            }
            for review in reviews
        ),
    }
    if actionable_count == 0:
        reason = (
            "当前仅剩判断边界或证据需求，继续专科讨论不会增加信息。"
            if current.assessment_boundaries or current.evidence_needs
            else "当前已无仍需专科处理的问题或真实冲突。"
        )
        return {**summary, "continue_discussion": False, "stop_reason": reason}
    if round_number >= max_rounds:
        return {
            **summary,
            "continue_discussion": False,
            "stop_reason": f"已达到最多{max_rounds}轮团队讨论。",
        }
    if (
        summary["changed_answers"] == 0
        and summary["forward_reviews"] == 0
        and _open_issue_signature(previous) == _open_issue_signature(current)
    ):
        return {
            **summary,
            "continue_discussion": False,
            "stop_reason": "本轮未形成新的专科判断或可继续处理的路径。",
        }
    return {**summary, "continue_discussion": True, "stop_reason": ""}


def _open_issue_signature(result: MDTChairIntegration) -> tuple:
    questions = tuple(sorted(
        (
            question.question_id,
            question.answer_status,
            question.discussion_status,
            tuple(question.target_specialties),
            tuple(question.responded_by),
            tuple(question.source_refs),
        )
        for question in result.questions
    ))
    conflicts = tuple(sorted(
        (
            conflict.conflict_id,
            conflict.conflict_nature,
            conflict.status,
            tuple(
                sorted(
                    ref
                    for position in conflict.positions
                    for ref in position.source_refs
                )
            ),
        )
        for conflict in result.conflicts
    ))
    return questions, conflicts


def reconcile_discussion_references(
    result: MDTChairIntegration,
    previous: MDTChairIntegration,
    responses: list[SpecialtyRoundResponse],
    bundle: Any,
    reviews: list[SpecialtyAnswerReview] | None = None,
) -> MDTChairIntegration:
    """Rebuild discussion question, answer, and evidence-need refs in program code."""

    del reviews  # retained for caller compatibility; requester routing is programmatic

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
                raise ValueError(f"Cannot verify prior specialty source ref: {ref}")
            current = bundle.source_registry.get(ref)
            if current is None:
                raise ValueError(f"Prior specialty source is absent from current round: {ref}")
            if (
                current.specialty,
                current.source_type,
                current.source_path,
                current.quote,
            ) != (
                citation.specialty,
                citation.source_type,
                citation.source_path,
                citation.quote,
            ):
                raise ValueError(f"Prior specialty source changed identity: {ref}")
            if ref not in rebased:
                rebased.append(ref)
        return rebased

    answer_ref_by_id = {
        metadata.get("assessment_id"): ref
        for ref, metadata in bundle.source_metadata.items()
        if metadata.get("assessment_id")
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
    for score, old_index, candidate_index in reversed(pairs):
        if score < 0.6:
            continue
        if old_index not in matched and candidate_index not in used_candidates:
            matched[old_index] = candidate_index
            used_candidates.add(candidate_index)

    questions = []
    for old_index, old in enumerate(active_previous):
        matched_candidate = old_index in matched
        question = (
            deepcopy(candidates[matched[old_index]])
            if matched_candidate
            else deepcopy(old)
        )
        candidate_need_refs = (
            list(question.related_evidence_need_source_refs)
            if matched_candidate
            else []
        )
        question.question = old.question
        question.source_refs = rebase(old.source_refs)
        question.related_evidence_need_source_refs = list(dict.fromkeys(
            rebase(old.related_evidence_need_source_refs)
            + candidate_need_refs
        ))
        question.question_id = old.question_id
        question.source_citations = []
        question.evidence = ChairEvidenceBundle()
        question.guideline_evidence = []
        prior_answers = []
        for prior in old.answers:
            prior = deepcopy(prior)
            refs_by_specialty: dict[str, list[str]] = {}
            for ref in rebase(prior.source_refs):
                refs_by_specialty.setdefault(
                    bundle.source_registry[ref].specialty, []
                ).append(ref)
            for refs in refs_by_specialty.values():
                specialty_answer = deepcopy(prior)
                specialty_answer.source_refs = refs
                specialty_answer.source_citations = []
                specialty_answer.evidence = ChairEvidenceBundle()
                specialty_answer.guideline_evidence = []
                prior_answers.append(specialty_answer)
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

    candidates = list(result.evidence_needs)
    used_candidates = set()
    evidence_needs = []
    for old in previous.evidence_needs:
        old_refs = rebase(old.source_refs)
        ranked = sorted(
            (
                (
                    bool(set(old_refs).intersection(candidate.source_refs)),
                    SequenceMatcher(
                        None,
                        old.required_information,
                        candidate.required_information,
                    ).ratio(),
                    index,
                )
                for index, candidate in enumerate(candidates)
                if index not in used_candidates
            ),
            reverse=True,
        )
        matched_index = None
        if ranked and (ranked[0][0] or ranked[0][1] >= 0.85):
            matched_index = ranked[0][2]
        need = (
            deepcopy(candidates[matched_index])
            if matched_index is not None
            else deepcopy(old)
        )
        if matched_index is not None:
            used_candidates.add(matched_index)
        need.need_id = old.need_id
        need.source_refs = list(dict.fromkeys(old_refs + list(need.source_refs)))
        need.source_citations = []
        need.evidence = ChairEvidenceBundle()
        need.guideline_evidence = []
        evidence_needs.append(need)
    evidence_needs.extend(
        deepcopy(candidate)
        for index, candidate in enumerate(candidates)
        if index not in used_candidates
    )
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
        _citation_key,
    )
    _stabilize(
        result.assessment_boundaries,
        previous.assessment_boundaries,
        "boundary_id",
        "B",
        _citation_key,
    )
    _stabilize(
        result.questions,
        previous.questions,
        "question_id",
        "Q",
        _citation_key,
    )
    _stabilize(
        result.evidence_needs,
        previous.evidence_needs,
        "need_id",
        "EN",
        _citation_key,
    )
    _stabilize(
        result.conflicts,
        previous.conflicts,
        "conflict_id",
        "CF",
        lambda item: frozenset(
            identity
            for position in item.positions
            for identity in _citation_key(position)
        ),
    )
    _relink(result)
    return result


def _citation_key(item) -> frozenset[tuple[str, str, str, str]]:
    return frozenset(
        (
            citation.specialty,
            citation.source_type,
            citation.source_path,
            citation.quote,
        )
        for citation in item.source_citations
    )


def _stabilize(current, previous, id_field, prefix, key) -> None:
    previous_by_key = {key(item): getattr(item, id_field) for item in previous if key(item)}
    used = {value for value in previous_by_key.values() if value}
    next_number = max((_numeric_id(value, prefix) for value in used), default=0) + 1
    for item in current:
        current_key = key(item)
        stable = previous_by_key.get(current_key)
        if stable is None:
            supersets = [
                (len(previous_key), value)
                for previous_key, value in previous_by_key.items()
                if previous_key.issubset(current_key)
            ]
            if supersets:
                stable = max(supersets)[1]
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
