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
from src.agents.mdt_discussion.models import (
    DiscussionRound,
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
    reviews_by_answer: dict[str, list[SpecialtyAnswerReview]] = {}
    for review in reviews:
        reviews_by_answer.setdefault(review.answer_id, []).append(review)
    for response in responses:
        specialty_output = updated[response.specialty]
        assessments = specialty_output["specialty_assessments"]
        assessment_items = assessments["assessments"]
        questions = specialty_output["interspecialty_questions"]["questions"]
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
            answer_reviews = reviews_by_answer.get(answer.answer_id, [])
            accepted = answer_reviews and all(
                review.outcome in {"accept_answer", "accept_boundary"}
                for review in answer_reviews
            )
            if accepted:
                questions.extend(item.model_dump(mode="json") for item in answer.new_questions)
            assessments["evidence_gaps"].extend(
                item.model_dump(mode="json") for item in answer.evidence_gaps
            )
    latest_reviews = {
        (review.issue_id, review.reviewer_specialty): review for review in reviews
    }
    for review in latest_reviews.values():
        reviewer_output = updated.get(review.reviewer_specialty)
        if reviewer_output is None:
            continue
        assessments = reviewer_output["specialty_assessments"]
        questions = reviewer_output["interspecialty_questions"]["questions"]
        if review.follow_up_question is not None:
            questions.append(
                review.follow_up_question.model_dump(mode="json")
            )
        if review.evidence_gap is not None:
            assessments["evidence_gaps"].append(
                review.evidence_gap.model_dump(mode="json")
            )
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
            question.discussion_status = "awaiting_conflict_assessment"
            question.closure_type = None
        elif "request_corroboration" in outcomes:
            question.review_status = "corroboration_requested"
            question.discussion_status = "awaiting_corroboration"
            question.closure_type = None
            question.answer_status = "partially_answered"
        elif "request_clarification" in outcomes:
            question.review_status = "clarification_requested"
            question.discussion_status = "clarification_in_progress"
            question.closure_type = None
            question.answer_status = "partially_answered"
        elif "convert_to_evidence_need" in outcomes:
            question.review_status = "converted_to_evidence_need"
            question.discussion_status = "waiting_for_new_evidence"
            question.closure_type = "converted_to_evidence_need"
            question.answer_status = "blocked_by_evidence"
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
    return result


def reconcile_discussion_references(
    result: MDTChairIntegration,
    previous: MDTChairIntegration,
    responses: list[SpecialtyRoundResponse],
    bundle: Any,
    reviews: list[SpecialtyAnswerReview] | None = None,
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
    reviews = reviews or []
    reviews_by_answer: dict[str, list[SpecialtyAnswerReview]] = {}
    for review in reviews:
        reviews_by_answer.setdefault(review.answer_id, []).append(review)
    allowed_new_questions = {
        question.question
        for response in responses
        for answer in response.answers
        if reviews_by_answer.get(answer.answer_id)
        and all(
            review.outcome in {"accept_answer", "accept_boundary"}
            for review in reviews_by_answer[answer.answer_id]
        )
        for question in answer.new_questions
    }
    latest_reviews = {
        (review.issue_id, review.reviewer_specialty): review for review in reviews
    }
    allowed_new_questions.update(
        review.follow_up_question.question
        for review in latest_reviews.values()
        if review.follow_up_question is not None
    )

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
    questions.extend(
        deepcopy(candidate)
        for index, candidate in enumerate(candidates)
        if index not in used_candidates
        and any(
            bundle.source_registry[ref].quote in allowed_new_questions
            for ref in candidate.source_refs
        )
    )
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
        if question.discussion_status == "waiting_for_new_evidence":
            kept_questions.append(question)
            continue
        count = len(attempts.get(question.question_id, set()))
        stalled = (
            question.answer_status == "blocked_by_evidence" and count >= 1
        ) or (
            question.discussion_status != "closed_this_round" and count >= 2
        )
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
                    if question.answer_status == "blocked_by_evidence"
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
                statement=f"当前不能消解以下跨专科分歧：{conflict.comparison_target}",
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
