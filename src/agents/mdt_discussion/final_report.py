from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.agents.mdt_chair.models import (
    AssessmentBoundary,
    ChairEvidenceBundle,
    CrossSpecialtyConflict,
    EvidenceNeed,
    SpecialtySourceCitation,
)
from src.agents.mdt_discussion.models import (
    ConflictAuditItem,
    DiscussionAudit,
    DiscussionAuditRound,
    DiscussionDecisionAudit,
    DiscussionRound,
    MDTFinalReport,
    ReportReasoningTrace,
    ResearchAuditMetrics,
)
from src.guidelines.models import GuidelineEvidencePointer
from src.agents.mdt_discussion.prompt_projection import build_chair_prompt_view
from src.llm.base import LLMClient
from src.llm.prompting import prompt_json, prompt_schema_json
from src.llm.structured import StructuredLLMGenerator
from src.utils.config import load_text, load_yaml, render_template


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts/mdt_discussion/final_report.md"


class FinalReportAgent:
    def __init__(
        self,
        llm: LLMClient,
        *,
        config: dict[str, Any],
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.prompt = load_text(PROMPT_PATH)
        self.generator = StructuredLLMGenerator(
            llm,
            temperature=float(config.get("temperature", 0.0)),
            max_tokens=int(config.get("max_tokens", 12000)),
            max_attempts=int(config.get("max_attempts", 2)),
            retry_backoff_seconds=float(config.get("retry_backoff_seconds", 2)),
            response_format_mode=(
                "json_schema" if getattr(llm, "supports_json_schema", False) else "json_object"
            ),
            event_callback=event_callback,
        )

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        llm: LLMClient,
        *,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> "FinalReportAgent":
        return cls(
            llm,
            config=load_yaml(config_path),
            event_callback=event_callback,
        )

    def generate(
        self,
        *,
        case_id: str,
        chair_result: dict[str, Any],
        rounds: list[DiscussionRound],
        stop_reason: str,
        baseline_chair_result: dict[str, Any] | None = None,
    ) -> tuple[MDTFinalReport, dict[str, Any]]:
        output_schema = (
            "由 API 的严格 JSON Schema response_format 提供。"
            if self.generator.response_format_mode == "json_schema"
            else prompt_schema_json(MDTFinalReport)
        )
        round_summary = [
            {
                "round_number": item.round_number,
                "specialties": [response.specialty for response in item.specialty_responses],
                "answers": [
                    {
                        "specialty": response.specialty,
                        "issue_id": answer.issue_id,
                        "answer": answer.answer,
                        "confidence": answer.confidence,
                        "remaining_limitation": answer.remaining_limitation,
                    }
                    for response in item.specialty_responses
                    for answer in response.answers
                ],
                "answer_reviews": [
                    {
                        "reviewer_specialty": review.reviewer_specialty,
                        "issue_id": review.issue_id,
                        "answer_id": review.answer_id,
                        "outcome": review.outcome,
                        "rationale": review.rationale,
                    }
                    for review in item.answer_reviews
                ],
                "round_decision": item.round_decision,
                "chair_five_sections": build_chair_prompt_view(item.chair_result),
            }
            for item in rounds
        ]
        prompt = render_template(
            self.prompt,
            {
                "stop_reason": stop_reason,
                "chair_result": prompt_json(build_chair_prompt_view(chair_result)),
                "rounds": prompt_json(round_summary),
                "output_schema": output_schema,
            },
        )

        def resolve(result: MDTFinalReport) -> MDTFinalReport:
            result.schema_version = "mdt_final_report.v3"
            result.case_id = case_id
            result.discussion_rounds = len(rounds)
            result.reasoning_trace = _resolve_reasoning_trace(result, chair_result)
            result.discussion_audit = build_discussion_audit(
                baseline_chair_result or chair_result,
                rounds,
                stop_reason,
            )
            result.assessment_boundaries = [
                AssessmentBoundary.model_validate(item)
                for item in chair_result.get("assessment_boundaries", [])
            ]
            result.unresolved_conflicts = [
                CrossSpecialtyConflict.model_validate(item)
                for item in chair_result.get("conflicts", [])
            ]
            result.evidence_needs = [
                EvidenceNeed.model_validate(item)
                for item in chair_result.get("evidence_needs", [])
            ]
            result.research_metrics = _research_metrics(result)
            has_open_issues = bool(
                chair_result.get("conflicts") or chair_result.get("questions")
            )
            if has_open_issues:
                result.consensus_status = (
                    "unresolved_after_max_rounds"
                    if stop_reason.startswith("已达到最多")
                    else "unresolved_without_further_progress"
                )
            elif chair_result.get("assessment_boundaries") or chair_result.get(
                "evidence_needs"
            ):
                result.consensus_status = "consensus_with_boundaries"
            else:
                result.consensus_status = "consensus_reached"
            return result

        return self.generator.generate(
            schema_model=MDTFinalReport,
            schema_name="mdt_final_report",
            system_prompt=(
                "你是以呼吸科为主要背景的 ILD MDT 主持人，负责在讨论结束后形成统一报告。"
                "忠实保留证据边界和未解决分歧，只返回符合 schema 的 JSON。"
            ),
            user_prompt=prompt,
            extra_validation=resolve,
        )


_CHAIR_COLLECTIONS = (
    ("integrated_conclusions", "conclusion_id"),
    ("assessment_boundaries", "boundary_id"),
    ("conflicts", "conflict_id"),
    ("questions", "question_id"),
    ("evidence_needs", "need_id"),
)


def _chair_registry(chair_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    registry = {}
    for collection, item_id in _CHAIR_COLLECTIONS:
        for item in chair_result.get(collection, []):
            value = item.get(item_id)
            if not value:
                continue
            if value in registry:
                raise ValueError(f"duplicate chair item reference: {value}")
            registry[value] = item
    return registry


def _append_unique(items: list[Any], values: list[Any], key) -> None:
    existing = {key(item) for item in items}
    for value in values:
        marker = key(value)
        if marker not in existing:
            existing.add(marker)
            items.append(value)


def _collect_provenance(
    value: Any,
    *,
    source_citations: list[SpecialtySourceCitation],
    evidence: ChairEvidenceBundle,
    guideline_evidence: list[GuidelineEvidencePointer],
) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_provenance(
                item,
                source_citations=source_citations,
                evidence=evidence,
                guideline_evidence=guideline_evidence,
            )
        return
    if not isinstance(value, dict):
        return
    if value.get("source_citations"):
        citations = [
            SpecialtySourceCitation.model_validate(item)
            for item in value["source_citations"]
        ]
        _append_unique(
            source_citations,
            citations,
            lambda item: (item.source_ref, item.specialty, item.source_path),
        )
    if value.get("evidence"):
        bundle = ChairEvidenceBundle.model_validate(value["evidence"])
        _append_unique(
            evidence.links,
            bundle.links,
            lambda item: (
                item.target_claim_id,
                item.evidence_ref,
                item.relation,
                tuple(item.evidence_ids),
                tuple(item.proposition_ids),
                tuple(item.node_ids),
            ),
        )
        for role in (
            "supporting",
            "weakening",
            "discriminating",
            "qualifying",
            "background",
        ):
            _append_unique(
                getattr(evidence, role),
                getattr(bundle, role),
                lambda item: (item.evidence_ref, tuple(item.evidence_ids)),
            )
    if value.get("guideline_evidence"):
        pointers = [
            GuidelineEvidencePointer.model_validate(item)
            for item in value["guideline_evidence"]
        ]
        _append_unique(
            guideline_evidence,
            pointers,
            lambda item: item.chunk_id,
        )
    for key, item in value.items():
        if key not in {"source_citations", "evidence", "guideline_evidence"}:
            _collect_provenance(
                item,
                source_citations=source_citations,
                evidence=evidence,
                guideline_evidence=guideline_evidence,
            )


def _reasoning_trace(
    *,
    claim_id: str,
    statement: str,
    medical_basis: str,
    chair_item_ids: list[str],
    limitations: list[str],
    registry: dict[str, dict[str, Any]],
) -> ReportReasoningTrace:
    unknown = [item_id for item_id in chair_item_ids if item_id not in registry]
    if unknown:
        raise ValueError(f"unknown chair item references: {', '.join(unknown)}")
    if not chair_item_ids:
        raise ValueError(f"{claim_id} must cite at least one chair item")
    source_citations: list[SpecialtySourceCitation] = []
    evidence = ChairEvidenceBundle()
    guideline_evidence: list[GuidelineEvidencePointer] = []
    for item_id in chair_item_ids:
        _collect_provenance(
            registry[item_id],
            source_citations=source_citations,
            evidence=evidence,
            guideline_evidence=guideline_evidence,
        )
    return ReportReasoningTrace(
        claim_id=claim_id,
        claim_statement=statement,
        chair_item_ids=chair_item_ids,
        medical_basis=medical_basis,
        source_citations=source_citations,
        evidence=evidence,
        guideline_evidence=guideline_evidence,
        limitations=limitations,
    )


def _resolve_reasoning_trace(
    report: MDTFinalReport,
    chair_result: dict[str, Any],
) -> list[ReportReasoningTrace]:
    if report.legacy_source:
        return []
    registry = _chair_registry(chair_result)
    traces = [
        _reasoning_trace(
            claim_id=f"DX{index:02d}",
            statement=item.statement,
            medical_basis=item.medical_basis,
            chair_item_ids=item.chair_item_ids,
            limitations=item.limitations,
            registry=registry,
        )
        for index, item in enumerate(report.clinical_report.diagnostic_matrix, start=1)
    ]
    traces.extend(
        _reasoning_trace(
            claim_id=f"DD{item.rank:02d}",
            statement=item.diagnosis,
            medical_basis=item.rationale,
            chair_item_ids=item.chair_item_ids,
            limitations=[],
            registry=registry,
        )
        for item in report.clinical_report.differential_diagnoses
    )
    return traces


def _find_issue(chair_result: dict[str, Any], issue_id: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for collection, item_id in (("questions", "question_id"), ("conflicts", "conflict_id"))
            for item in chair_result.get(collection, [])
            if item.get(item_id) == issue_id
        ),
        None,
    )


def _issue_result(issue: dict[str, Any] | None) -> str:
    if not issue:
        return ""
    return (
        issue.get("answer_summary")
        or issue.get("why_incompatible")
        or issue.get("comparison_target")
        or issue.get("question")
        or issue.get("topic")
        or ""
    )


def _issue_status(issue: dict[str, Any] | None) -> str:
    if not issue:
        return "closed"
    return (
        issue.get("discussion_status")
        or issue.get("status")
        or issue.get("answer_status")
        or "open"
    )


def build_discussion_audit(
    baseline_chair_result: dict[str, Any],
    rounds: list[DiscussionRound],
    stop_reason: str,
) -> DiscussionAudit:
    tasks_by_issue: dict[str, list[tuple[DiscussionRound, Any]]] = {}
    for round_item in rounds:
        for task in round_item.tasks:
            tasks_by_issue.setdefault(task.issue_id, []).append((round_item, task))

    decisions = []
    for issue_id, entries in tasks_by_issue.items():
        baseline_issue = _find_issue(baseline_chair_result, issue_id)
        audit_rounds = []
        for round_item, task in entries:
            answer = next(
                (
                    answer
                    for response in round_item.specialty_responses
                    for answer in response.answers
                    if answer.task_id == task.task_id
                ),
                None,
            )
            reviews = [
                {
                    "reviewer_specialty": review.reviewer_specialty,
                    "outcome": review.outcome,
                    "rationale": review.rationale,
                }
                for review in round_item.answer_reviews
                if answer and review.answer_id == answer.answer_id
            ]
            chair_after = _find_issue(round_item.chair_result, issue_id)
            audit_rounds.append(DiscussionAuditRound(
                round_number=round_item.round_number,
                task_id=task.task_id,
                specialty=task.specialty,
                prompt=task.prompt,
                current_result=task.current_result,
                answer=answer.answer if answer else "",
                answerability=answer.answerability if answer else "",
                confidence=answer.confidence if answer else "",
                changed_from_previous=answer.changed_from_previous if answer else False,
                reviews=reviews,
                chair_result_after_round=(
                    _issue_result(chair_after)
                    if chair_after
                    else "主持人已将该议题移出开放问题或冲突列表。"
                ),
                closure="；".join(review["outcome"] for review in reviews),
            ))
        final_issue = _find_issue(rounds[-1].chair_result, issue_id) if rounds else baseline_issue
        first_task = entries[0][1]
        decisions.append(DiscussionDecisionAudit(
            issue_id=issue_id,
            issue_type=first_task.issue_type,
            question=first_task.prompt,
            why_it_matters=first_task.why_it_matters,
            baseline_result=_issue_result(baseline_issue) or first_task.current_result,
            rounds=audit_rounds,
            final_status=_issue_status(final_issue),
            final_result=(
                _issue_result(final_issue)
                or next(
                    (item.answer for item in reversed(audit_rounds) if item.answer),
                    audit_rounds[-1].chair_result_after_round,
                )
            ),
            decision_impact=(final_issue or baseline_issue or {}).get("decision_impact", ""),
        ))

    snapshots = [(0, baseline_chair_result)] + [
        (item.round_number, item.chair_result) for item in rounds
    ]
    formal_conflicts: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for round_number, snapshot in snapshots:
        for conflict in snapshot.get("conflicts", []):
            if conflict.get("conflict_id"):
                formal_conflicts.setdefault(conflict["conflict_id"], []).append(
                    (round_number, conflict)
                )
    latest_snapshot = snapshots[-1][1]
    latest_conflict_ids = {
        item.get("conflict_id") for item in latest_snapshot.get("conflicts", [])
    }
    conflicts = [
        ConflictAuditItem(
            issue_id=conflict_id,
            topic=history[-1][1].get("topic", conflict_id),
            kind="formal_conflict",
            outcome="unresolved" if conflict_id in latest_conflict_ids else "resolved",
            first_round=history[0][0],
            last_round=history[-1][0],
            summary=history[-1][1].get("why_incompatible", ""),
        )
        for conflict_id, history in formal_conflicts.items()
    ]
    formal_question_ids = {
        question_id
        for history in formal_conflicts.values()
        for _, conflict in history
        for question_id in conflict.get("related_question_ids", [])
    }
    for round_item in rounds:
        for review in round_item.answer_reviews:
            if review.outcome != "flag_incompatibility" or review.issue_id in formal_question_ids:
                continue
            conflicts.append(ConflictAuditItem(
                issue_id=review.issue_id,
                topic=next(
                    (task.prompt for task in round_item.tasks if task.issue_id == review.issue_id),
                    review.issue_id,
                ),
                kind="flagged_incompatibility",
                outcome="not_confirmed_as_formal_conflict",
                first_round=round_item.round_number,
                last_round=round_item.round_number,
                summary=review.rationale,
            ))
    return DiscussionAudit(
        decisions=decisions,
        conflicts=conflicts,
        stop_reason=stop_reason,
    )


def _research_metrics(report: MDTFinalReport) -> ResearchAuditMetrics:
    traces = report.reasoning_trace
    formal_conflicts = [
        item for item in report.discussion_audit.conflicts
        if item.kind == "formal_conflict"
    ]
    return ResearchAuditMetrics(
        diagnostic_claims=len(traces),
        claims_with_specialty_citations=sum(bool(item.source_citations) for item in traces),
        claims_with_patient_evidence=sum(
            any((
                item.evidence.links,
                item.evidence.supporting,
                item.evidence.weakening,
                item.evidence.discriminating,
                item.evidence.qualifying,
                item.evidence.background,
            ))
            for item in traces
        ),
        claims_with_guideline_citations=sum(bool(item.guideline_evidence) for item in traces),
        discussion_issues=len(report.discussion_audit.decisions),
        closed_issues=sum(
            item.final_status in {"closed", "closed_this_round", "waiting_for_new_evidence"}
            for item in report.discussion_audit.decisions
        ),
        formal_conflicts=len(formal_conflicts),
        resolved_formal_conflicts=sum(item.outcome == "resolved" for item in formal_conflicts),
        unresolved_formal_conflicts=sum(item.outcome == "unresolved" for item in formal_conflicts),
        assessment_boundaries=len(report.assessment_boundaries),
    )
