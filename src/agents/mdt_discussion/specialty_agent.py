from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.agents.mdt_discussion.models import (
    DiscussionEvidenceUse,
    DiscussionTask,
    SpecialtyTaskAnswer,
    SpecialtyTaskAnswerDraft,
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
        self.guideline_runtime = GuidelineRuntime.from_config(config)
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
                self.guideline_runtime.prepare_query(guideline_query, limit=6)
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
                "specialty_initial_output": prompt_json(specialty_initial_output),
                "chair_result": prompt_json(chair_result),
                "task": prompt_json(task.model_dump(mode="json")),
                "clinical_rules": prompt_json(self.config.get("clinical_rules") or {}),
                "guideline_context": guideline_context,
                "output_schema": output_schema,
            },
        )

        def validate(draft: SpecialtyTaskAnswerDraft) -> SpecialtyTaskAnswerDraft:
            candidates = {item.evidence_ref: item for item in task.evidence_candidates}
            for use in draft.evidence_uses:
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
            pointer_field_constraints=guideline_evidence_schema_constraints(allowed_chunks),
        )
        trace["guideline_retrieval"] = retrieval_trace
        return _resolve_answer(
            task,
            draft,
            answer_id=f"{task.task_id}-A",
        ), trace


def _resolve_answer(task, draft, *, answer_id: str) -> SpecialtyTaskAnswer:
    candidates = {item.evidence_ref: item for item in task.evidence_candidates}
    uses = []
    for use in draft.evidence_uses:
        candidate = candidates[use.evidence_ref]
        selected = set(use.proposition_ids)
        uses.append(
            DiscussionEvidenceUse(
                **use.model_dump(mode="json"),
                evidence_ids=candidate.evidence_ids,
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
        )
    return SpecialtyTaskAnswer(
        answer_id=answer_id,
        task_id=task.task_id,
        issue_type=task.issue_type,
        issue_id=task.issue_id,
        answerability=draft.answerability,
        answer=draft.answer,
        confidence=draft.confidence,
        medical_basis=draft.medical_basis,
        evidence_uses=uses,
        guideline_evidence=draft.guideline_evidence,
        changed_from_previous=draft.changed_from_previous,
        remaining_limitation=draft.remaining_limitation,
    )
