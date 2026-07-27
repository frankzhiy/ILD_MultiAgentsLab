from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.agents.mdt_discussion.models import (
    DiscussionAnswerClaim,
    DiscussionEvidenceUse,
    DiscussionTask,
    SpecialtyAnswerReview,
    SpecialtyAnswerReviewDraft,
    SpecialtyTaskAnswer,
    SpecialtyTaskAnswerDraft,
)
from src.agents.mdt_discussion.prompt_projection import (
    build_issue_chair_prompt_view,
    build_specialty_discussion_prompt_view,
)
from src.guidelines.runtime import (
    GuidelineRuntime,
    guideline_evidence_schema_constraints,
    resolve_guideline_evidence,
)
from src.llm.base import LLMClient
from src.llm.prompting import prompt_json, prompt_schema_json
from src.llm.structured import StructuredLLMGenerator
from src.utils.config import load_text, load_yaml, render_template


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts/mdt_discussion/specialty_response.md"
REVIEW_PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts/mdt_discussion/answer_review.md"
)
SPECIALTY_LABELS = {
    "pulmonology": "呼吸科",
    "thoracic_radiology": "胸部影像科",
    "rheumatology": "风湿免疫科",
    "pathology": "病理科",
}
ROLE_BOUNDARIES = {
    "pulmonology": "负责临床疾病层面的整合，但不是 MDT 主席。",
    "thoracic_radiology": "只能解释提供的影像文字资料，不能声称直接阅片。",
    "rheumatology": "负责风湿病和 CTD 归因判断，不代替影像或病理模式判断。",
    "pathology": "只能解释提供的病理文字资料；没有材料时不得形成组织学模式。",
}


class SpecialtyDiscussionAgent:
    def __init__(
        self,
        llm: LLMClient,
        *,
        specialty: str,
        config: dict[str, Any],
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        if specialty not in SPECIALTY_LABELS:
            raise ValueError(f"Unsupported discussion specialty: {specialty}")
        self.specialty = specialty
        self.config = config
        self.prompt = load_text(PROMPT_PATH)
        self.review_prompt = load_text(REVIEW_PROMPT_PATH)
        self.guideline_runtime = GuidelineRuntime.from_config(config)
        self.generator = StructuredLLMGenerator(
            llm,
            temperature=float(config.get("temperature", 0.0)),
            max_tokens=min(int(config.get("max_tokens", 12000)), 8000),
            max_attempts=int(config.get("max_attempts", 2)),
            retry_backoff_seconds=float(config.get("retry_backoff_seconds", 2)),
            response_format_mode=(
                "json_schema" if getattr(llm, "supports_json_schema", False) else "json_object"
            ),
            event_callback=event_callback,
        )
        self.review_generator = StructuredLLMGenerator(
            llm,
            temperature=0.0,
            max_tokens=min(int(config.get("max_tokens", 12000)), 2500),
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
        specialty: str,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> "SpecialtyDiscussionAgent":
        return cls(
            llm,
            specialty=specialty,
            config=load_yaml(config_path),
            event_callback=event_callback,
        )

    def respond_to_task(
        self,
        *,
        task: DiscussionTask,
        specialty_initial_output: dict[str, Any],
        chair_result: dict[str, Any],
    ) -> tuple[SpecialtyTaskAnswer, dict[str, Any]]:
        guideline_query = (
            f"ILD MDT {task.prompt} {task.remaining_clarification}".strip()
        )
        if self.guideline_runtime:
            guideline_context, allowed_chunks, retrieval_trace = (
                self.guideline_runtime.prepare_query(guideline_query, limit=3)
            )
        else:
            guideline_context, allowed_chunks, retrieval_trace = "[]", {}, {
                "query": guideline_query,
                "candidates": [],
                "used_chunk_ids": [],
            }
        output_schema = (
            "由 API 的严格 JSON Schema response_format 提供。"
            if self.generator.response_format_mode == "json_schema"
            else prompt_schema_json(SpecialtyTaskAnswerDraft)
        )
        prompt = render_template(
            self.prompt,
            {
                "specialty_label": SPECIALTY_LABELS[self.specialty],
                "specialty_initial_output": prompt_json(
                    build_specialty_discussion_prompt_view(specialty_initial_output)
                ),
                "chair_result": prompt_json(
                    build_issue_chair_prompt_view(chair_result, task.issue_id)
                ),
                "task": prompt_json(task.model_dump(mode="json")),
                "clinical_rules": prompt_json(self.config.get("clinical_rules") or {}),
                "guideline_context": guideline_context,
                "output_schema": output_schema,
            },
        )

        def validate(draft: SpecialtyTaskAnswerDraft) -> SpecialtyTaskAnswerDraft:
            candidates = {item.evidence_ref: item for item in task.evidence_candidates}
            allowed_evidence_ids = {
                evidence_id
                for candidate in task.evidence_candidates
                for evidence_id in candidate.evidence_ids
            }
            claim_uses = [
                use
                for claim in draft.answer_claims
                for use in claim.evidence_uses
            ]
            for use in [*claim_uses, *draft.evidence_uses]:
                if use.evidence_ref not in candidates:
                    raise ValueError(f"Unknown evidence_ref for {task.task_id}: {use.evidence_ref}")
                allowed_propositions = {
                    item.proposition_id
                    for item in candidates[use.evidence_ref].propositions
                }
                unknown = set(use.proposition_ids) - allowed_propositions
                if unknown:
                    raise ValueError(
                        f"Unknown proposition_ids for {task.task_id}: {sorted(unknown)}"
                    )
            for question in draft.new_questions:
                if question.target_specialty.value == self.specialty:
                    raise ValueError("A new discussion question cannot target its issuing specialty")
                if question.question.strip() == task.prompt.strip():
                    raise ValueError("A new discussion question cannot repeat the current question")
            for item in [*draft.new_questions, *draft.evidence_gaps]:
                unknown = {
                    evidence_id
                    for pointer in item.related_evidence
                    for evidence_id in pointer.evidence_ids
                    if evidence_id not in allowed_evidence_ids
                }
                if unknown:
                    raise ValueError(
                        f"Unknown related evidence_ids for {task.task_id}: {sorted(unknown)}"
                    )
            retrieval_trace["used_chunk_ids"] = resolve_guideline_evidence(
                draft, allowed_chunks
            )
            return draft

        draft, trace = self.generator.generate(
            schema_model=SpecialtyTaskAnswerDraft,
            schema_name=f"{self.specialty}_discussion_{task.task_id}",
            system_prompt=(
                f"你是严谨的 ILD MDT {SPECIALTY_LABELS[self.specialty]}会诊医生。"
                f"{ROLE_BOUNDARIES[self.specialty]}只返回符合 schema 的 JSON。"
            ),
            user_prompt=prompt,
            extra_validation=validate,
            pointer_field_constraints={
                **discussion_evidence_schema_constraints(task),
                **guideline_evidence_schema_constraints(allowed_chunks),
            },
        )
        trace["guideline_retrieval"] = retrieval_trace
        return _resolve_answer(
            task,
            draft,
            answer_id=f"{task.task_id}-A",
        ), trace

    def review_answer(
        self,
        *,
        task: DiscussionTask,
        answer: SpecialtyTaskAnswer,
    ) -> tuple[SpecialtyAnswerReview, dict[str, Any]]:
        """Review one answer with a deliberately small, guideline-free prompt."""

        output_schema = (
            "由 API 的严格 JSON Schema response_format 提供。"
            if self.review_generator.response_format_mode == "json_schema"
            else prompt_schema_json(SpecialtyAnswerReviewDraft)
        )
        own_context = [
            item
            for item in task.specialty_context
            if item.get("specialty") == self.specialty
        ]
        prompt = render_template(
            self.review_prompt,
            {
                "review_context": prompt_json({
                    "issue_id": task.issue_id,
                    "question": task.prompt,
                    "why_it_matters": task.why_it_matters,
                    "requester_views": own_context,
                }),
                "answer": prompt_json({
                    "answer_id": answer.answer_id,
                    "answering_specialty": task.specialty,
                    "answerability": answer.answerability,
                    "answer": answer.answer,
                    "medical_basis": answer.medical_basis,
                    "remaining_limitation": answer.remaining_limitation,
                    "new_questions": [
                        item.model_dump(mode="json") for item in answer.new_questions
                    ],
                    "evidence_gaps": [
                        item.model_dump(mode="json") for item in answer.evidence_gaps
                    ],
                }),
                "output_schema": output_schema,
            },
        )

        def validate(draft: SpecialtyAnswerReviewDraft) -> SpecialtyAnswerReviewDraft:
            needs_question = draft.outcome in {
                "request_clarification",
                "request_corroboration",
            }
            if needs_question != (draft.follow_up_question is not None):
                raise ValueError(
                    f"{draft.outcome} requires exactly one follow_up_question"
                )
            if draft.outcome == "request_clarification":
                target = draft.follow_up_question.target_specialty.value
                if target != task.specialty:
                    raise ValueError("Clarification must target the answering specialty")
            if (draft.outcome == "convert_to_evidence_need") != (
                draft.evidence_gap is not None
            ):
                raise ValueError(
                    "convert_to_evidence_need requires exactly one evidence_gap"
                )
            return draft

        draft, trace = self.review_generator.generate(
            schema_model=SpecialtyAnswerReviewDraft,
            schema_name=f"{self.specialty}_review_{answer.answer_id}",
            system_prompt=(
                f"你是严谨的 ILD MDT {SPECIALTY_LABELS[self.specialty]}会诊医生。"
                "只复核当前问题和回答，不扩展病例，只返回符合 schema 的 JSON。"
            ),
            user_prompt=prompt,
            extra_validation=validate,
        )
        return SpecialtyAnswerReview(
            **draft.model_dump(mode="json"),
            review_id=f"{answer.answer_id}-RV-{self.specialty}",
            issue_id=answer.issue_id,
            answer_id=answer.answer_id,
            reviewer_specialty=self.specialty,
        ), trace


def discussion_evidence_schema_constraints(
    task: DiscussionTask,
) -> dict[str, list[dict[str, set[str]]]]:
    """Restrict every discussion evidence use to this task's candidates."""

    return {
        "evidence_uses": [
            {
                "evidence_ref": {candidate.evidence_ref},
                "proposition_ids": {
                    proposition.proposition_id for proposition in candidate.propositions
                },
            }
            for candidate in task.evidence_candidates
        ]
    }


def _resolve_answer(task, draft, *, answer_id: str) -> SpecialtyTaskAnswer:
    candidates = {item.evidence_ref: item for item in task.evidence_candidates}

    def resolve_use(use):
        candidate = candidates[use.evidence_ref]
        selected = set(use.proposition_ids)
        return DiscussionEvidenceUse(
            **use.model_dump(mode="json"),
            evidence_ids=candidate.evidence_ids,
            segment_id=candidate.segment_id,
            graph_unit_id=candidate.graph_unit_id,
            quote=candidate.quote,
            evidence_fragments=candidate.evidence_fragments,
            propositions=[
                proposition
                for proposition in candidate.propositions
                if proposition.proposition_id in selected
            ],
            graph_nodes=candidate.graph_nodes,
            graph_edges=candidate.graph_edges,
        )

    claims = [
        DiscussionAnswerClaim(
            claim_id=f"{answer_id}-C{index:03d}",
            statement=claim.statement,
            evidence_uses=[resolve_use(use) for use in claim.evidence_uses],
            guideline_evidence=claim.guideline_evidence,
        )
        for index, claim in enumerate(draft.answer_claims, start=1)
    ]
    uses = []
    seen = set()
    for use in [
        *(use for claim in claims for use in claim.evidence_uses),
        *(resolve_use(use) for use in draft.evidence_uses),
    ]:
        key = (
            use.evidence_ref,
            tuple(use.proposition_ids),
            use.effect,
            use.interpretation,
        )
        if key not in seen:
            seen.add(key)
            uses.append(use)
    guidelines = []
    guideline_ids = set()
    for pointer in [
        *(pointer for claim in claims for pointer in claim.guideline_evidence),
        *draft.guideline_evidence,
    ]:
        if pointer.chunk_id not in guideline_ids:
            guideline_ids.add(pointer.chunk_id)
            guidelines.append(pointer)
    return SpecialtyTaskAnswer(
        answer_id=answer_id,
        task_id=task.task_id,
        issue_type=task.issue_type,
        issue_id=task.issue_id,
        answerability=draft.answerability,
        answer="\n".join(claim.statement for claim in claims),
        confidence=draft.confidence,
        medical_basis=draft.medical_basis,
        answer_claims=claims,
        evidence_uses=uses,
        guideline_evidence=guidelines,
        changed_from_previous=draft.changed_from_previous,
        remaining_limitation=draft.remaining_limitation,
        new_questions=draft.new_questions,
        evidence_gaps=draft.evidence_gaps,
    )
