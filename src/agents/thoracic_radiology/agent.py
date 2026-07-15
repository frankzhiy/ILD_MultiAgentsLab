"""Problem-oriented, text-description-based thoracic-radiology specialist agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from src.agents.thoracic_radiology.evidence_projection import (
    RadiologyWorkingInput,
    build_radiology_working_input,
)
from src.agents.thoracic_radiology.models import (
    DiscussionEvidenceMap,
    DiscussionUpdateAndConsult,
    InitialCaseReconstruction,
    InitialConsultFormulation,
    RadiologyActionItem,
    SpecialistQuestion,
    ThoracicRadiologyDiscussionInput,
    ThoracicRadiologyDiscussionResponse,
    ThoracicRadiologyInitialAssessment,
)
from src.agents.thoracic_radiology.validation import (
    require_radiology_input as _require_radiology_input,
    validate_case_reconstruction as _validate_case_reconstruction,
    validate_discussion_response,
    validate_evidence_map as _validate_evidence_map,
    validate_initial_assessment,
    validate_initial_formulation as _validate_initial_formulation,
    validate_specialist_opinions as _validate_specialist_opinions,
    validate_update_and_consult as _validate_update_and_consult,
)
from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.schemas.specialty_agent_input import SpecialtyCaseInput
from src.utils.config import load_text, load_yaml, render_template


SYSTEM_PROMPT = (
    "你是严谨的ILD胸部影像科会诊医生，只能分析病例中的影像文字描述，不能读取原始图像。"
    "你必须先回答当前病例真正交给影像科的问题，再按资料可回答性选择任务；"
    "原报告所见、原报告印象、临床诊断和你的影像解释必须严格分层。"
    "所有面向人的文本字段使用简体中文，标准医学缩写可保留；只返回符合schema的JSON。"
)


class ThoracicRadiologyAgent:
    def __init__(
        self,
        llm: LLMClient,
        *,
        initial_case_reconstruction_prompt_path: str | Path,
        initial_consult_formulation_prompt_path: str | Path,
        discussion_evidence_mapping_prompt_path: str | Path,
        discussion_update_and_response_prompt_path: str | Path,
        clinical_rules: dict,
        temperature: float,
        max_tokens: int,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.0,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.prompts = {
            "initial_case_reconstruction": load_text(
                initial_case_reconstruction_prompt_path
            ),
            "initial_consult_formulation": load_text(initial_consult_formulation_prompt_path),
            "discussion_evidence_mapping": load_text(
                discussion_evidence_mapping_prompt_path
            ),
            "discussion_update_and_response": load_text(
                discussion_update_and_response_prompt_path
            ),
        }
        self.clinical_rules = clinical_rules
        self.generator = StructuredLLMGenerator(
            llm,
            temperature=temperature,
            max_tokens=max_tokens,
            max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
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
    ) -> "ThoracicRadiologyAgent":
        config = load_yaml(config_path)
        prompt_keys = (
            "initial_case_reconstruction_prompt",
            "initial_consult_formulation_prompt",
            "discussion_evidence_mapping_prompt",
            "discussion_update_and_response_prompt",
        )
        missing = [key for key in prompt_keys if key not in config]
        if missing:
            raise ValueError(f"Thoracic radiology config is missing prompt paths: {missing}")
        return cls(
            llm,
            **{f"{key}_path": config[key] for key in prompt_keys},
            clinical_rules=config.get("clinical_rules", {}),
            temperature=float(config.get("temperature", 0.0)),
            max_tokens=int(config.get("max_tokens", 12000)),
            max_attempts=int(config.get("max_attempts", 2)),
            retry_backoff_seconds=float(config.get("retry_backoff_seconds", 2)),
            event_callback=event_callback,
        )

    @staticmethod
    def build_working_input(case_input: SpecialtyCaseInput) -> RadiologyWorkingInput:
        _require_radiology_input(case_input)
        return build_radiology_working_input(case_input)

    def initial_assessment(
        self, case_input: SpecialtyCaseInput
    ) -> tuple[ThoracicRadiologyInitialAssessment, dict]:
        _require_radiology_input(case_input)
        working_input = build_radiology_working_input(case_input)
        working_json = _json(working_input)
        rules_json = _json(self.clinical_rules)

        reconstruction, reconstruction_trace = self._generate(
            stage="initial_case_reconstruction",
            schema_model=InitialCaseReconstruction,
            variables={
                "working_input": working_json,
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_case_reconstruction(
                result, case_input, working_input
            ),
        )
        formulation, formulation_trace = self._generate(
            stage="initial_consult_formulation",
            schema_model=InitialConsultFormulation,
            variables={
                "working_input": working_json,
                "case_reconstruction": _json(reconstruction),
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_initial_formulation(
                result, reconstruction, case_input, working_input
            ),
        )
        result = ThoracicRadiologyInitialAssessment(
            case_id=case_input.case_id,
            reconstruction=reconstruction,
            task_assessments=formulation.task_assessments,
            core_answer=formulation.core_answer,
            review_coverage=formulation.review_coverage,
            specialist_questions=_dedupe_questions(formulation.specialist_questions),
            action_items=_dedupe_actions(formulation.action_items),
            limitations=_dedupe_strings(
                [*reconstruction.limitations, *formulation.limitations]
            ),
        )
        validate_initial_assessment(result, case_input, self.clinical_rules)
        return result, _combined_trace(
            working_input,
            ("initial_case_reconstruction", reconstruction_trace),
            ("initial_consult_formulation", formulation_trace),
        )

    def discussion_response(
        self, discussion_input: ThoracicRadiologyDiscussionInput
    ) -> tuple[ThoracicRadiologyDiscussionResponse, dict]:
        case_input = discussion_input.case_input
        _require_radiology_input(case_input)
        validate_initial_assessment(
            discussion_input.initial_assessment,
            case_input,
            self.clinical_rules,
        )
        _validate_specialist_opinions(discussion_input)
        working_input = build_radiology_working_input(case_input)
        compact_input = {
            "case_id": case_input.case_id,
            "working_input": working_input,
            "initial_assessment": discussion_input.initial_assessment,
            "specialist_opinions": discussion_input.specialist_opinions,
            "chair_questions": discussion_input.chair_questions,
        }
        compact_json = _json(compact_input)
        rules_json = _json(self.clinical_rules)

        evidence_map, map_trace = self._generate(
            stage="discussion_evidence_mapping",
            schema_model=DiscussionEvidenceMap,
            variables={
                "discussion_input": compact_json,
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_evidence_map(result, discussion_input),
        )
        update, update_trace = self._generate(
            stage="discussion_update_and_response",
            schema_model=DiscussionUpdateAndConsult,
            variables={
                "discussion_input": compact_json,
                "evidence_map": _json(evidence_map),
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_update_and_consult(
                result, discussion_input
            ),
        )
        updated_assessment = _merge_discussion_update(
            discussion_input.initial_assessment, update
        )
        used_opinions = _dedupe_strings(
            [
                *evidence_map.specialist_opinions_used,
                *update.reported_content_opinion_ids,
                *[
                    opinion_id
                    for item in update.task_updates
                    for opinion_id in item.specialist_opinion_ids
                ],
                *[
                    opinion_id
                    for item in update.chair_answers
                    for opinion_id in item.specialist_opinion_ids
                ],
            ]
        )
        result = ThoracicRadiologyDiscussionResponse(
            case_id=case_input.case_id,
            updated_assessment=updated_assessment,
            task_changes=update.task_updates,
            specialist_opinions_used=used_opinions,
            mapped_findings=evidence_map.mapped_findings,
            chair_answers=update.chair_answers,
            unresolved_conflicts=[
                *evidence_map.unresolved_conflicts,
                *update.unresolved_conflicts,
            ],
            imaging_recommendations=_dedupe_strings(update.imaging_recommendations),
            limitations=_dedupe_strings(update.limitations),
        )
        validate_discussion_response(result, discussion_input, self.clinical_rules)
        return result, _combined_trace(
            working_input,
            ("discussion_evidence_mapping", map_trace),
            ("discussion_update_and_response", update_trace),
        )

    def _generate(self, *, stage, schema_model, variables, validation):
        prompt = render_template(
            self.prompts[stage],
            {
                "output_schema": json.dumps(
                    schema_model.model_json_schema(), ensure_ascii=False, indent=2
                ),
                **variables,
            },
        )
        return self.generator.generate(
            schema_model=schema_model,
            schema_name=stage,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            extra_validation=validation,
        )


def _merge_discussion_update(
    initial: ThoracicRadiologyInitialAssessment,
    update: DiscussionUpdateAndConsult,
) -> ThoracicRadiologyInitialAssessment:
    reconstruction_data = initial.reconstruction.model_dump(mode="python")
    reconstruction_data["examinations"] = [
        *initial.reconstruction.examinations,
        *update.added_examinations,
    ]
    reconstruction_data["reported_statements"] = [
        *initial.reconstruction.reported_statements,
        *update.added_reported_statements,
    ]
    reconstruction = InitialCaseReconstruction.model_validate(reconstruction_data)
    assessments = {item.task: item for item in initial.task_assessments}
    for item in update.task_updates:
        assessments[item.task] = item.updated_assessment
    return ThoracicRadiologyInitialAssessment(
        case_id=initial.case_id,
        reconstruction=reconstruction,
        task_assessments=list(assessments.values()),
        core_answer=update.updated_core_answer,
        review_coverage=update.review_coverage or initial.review_coverage,
        specialist_questions=_dedupe_questions(
            [*initial.specialist_questions, *update.specialist_questions]
        ),
        action_items=_dedupe_actions([*initial.action_items, *update.action_items]),
        limitations=_dedupe_strings([*initial.limitations, *update.limitations]),
    )


def _dedupe_strings(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        normalized = "".join(item.split()).rstrip("。；;，,")
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(item)
    return result


def _dedupe_questions(items: list[SpecialistQuestion]) -> list[SpecialistQuestion]:
    seen = set()
    result = []
    for item in items:
        key = (item.specialty, "".join(item.question.split()).rstrip("？?。"))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _dedupe_actions(items: list[RadiologyActionItem]) -> list[RadiologyActionItem]:
    seen = set()
    result = []
    for item in items:
        key = "".join(item.action.split()).rstrip("。；;，,")
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _json(value: object) -> str:
    def serializable(item):
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, dict):
            return {key: serializable(nested) for key, nested in item.items()}
        if isinstance(item, (list, tuple)):
            return [serializable(nested) for nested in item]
        return item

    return json.dumps(serializable(value), ensure_ascii=False, indent=2)


def _combined_trace(working_input: RadiologyWorkingInput, *stages) -> dict:
    return {
        "schema_version": "thoracic_radiology.v2",
        "working_input_summary": working_input.summary.model_dump(mode="json"),
        "stages": [{"stage": name, **trace} for name, trace in stages],
    }
