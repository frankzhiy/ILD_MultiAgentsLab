"""Problem-oriented, text-description-based thoracic-radiology specialist agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.agents.common.prompt_contract import specialty_output_contract
from src.agents.common.initial_output import SpecialtyInitialConsultResult, SpecialtyInitialOutput
from src.agents.common.initial_output_validation import (
    formal_evidence_schema_constraints,
    validate_specialty_initial_output,
)
from src.agents.thoracic_radiology.evidence_projection import (
    RadiologyWorkingInput,
    build_radiology_evidence_prompt_input,
    build_radiology_reconstruction_prompt_input,
    build_radiology_working_input,
    radiology_proposition_schema_constraints,
)
from src.agents.thoracic_radiology.models import (
    InitialCaseReconstruction,
    InitialConsultFormulation,
    RadiologyActionItem,
    SpecialistQuestion,
    ThoracicRadiologyInitialAssessment,
)
from src.agents.thoracic_radiology.validation import (
    require_radiology_input as _require_radiology_input,
    validate_case_reconstruction as _validate_case_reconstruction,
    validate_initial_assessment,
    validate_initial_formulation as _validate_initial_formulation,
)
from src.guidelines.runtime import (
    PROMPT_RULES,
    GuidelineRuntime,
    guideline_evidence_schema_constraints,
    resolve_guideline_evidence,
)
from src.llm.base import LLMClient
from src.llm.prompting import prompt_json, prompt_schema_json
from src.llm.structured import StructuredLLMGenerator
from src.schemas.specialty_agent_input import SpecialtyCaseInput
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget
from src.utils.config import load_text, load_yaml, render_template


SYSTEM_PROMPT = (
    "你是严谨的ILD胸部影像科会诊医生，只能分析病例中的影像文字描述，不能读取原始图像。"
    "你必须先回答当前病例真正交给影像科的问题，再按资料可回答性选择任务；"
    "原报告所见、原报告印象、临床诊断和你的影像解释必须严格分层。"
    "所有面向人的文本字段使用简体中文，标准医学缩写可保留；只返回符合schema的JSON。"
)

_RULE_KEYS_BY_STAGE = {
    "initial_case_reconstruction": ("ipf_hrct", "acquisition"),
    "initial_consult_formulation": (
        "morphology",
        "diagnostic_confidence",
        "ipf_hrct",
        "radiologic_progression",
        "acquisition",
    ),
    "initial_reasoning_output": (
        "morphology",
        "ipf_hrct",
        "radiologic_progression",
        "acquisition",
    ),
}


class ThoracicRadiologyAgent:
    def __init__(
        self,
        llm: LLMClient,
        *,
        initial_case_reconstruction_prompt_path: str | Path,
        initial_consult_formulation_prompt_path: str | Path,
        clinical_rules: dict,
        temperature: float,
        max_tokens: int,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.0,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
        guideline_runtime: GuidelineRuntime | None = None,
        initial_reasoning_output_prompt_path: str | Path | None = None,
    ) -> None:
        self.prompts = {
            "initial_case_reconstruction": load_text(
                initial_case_reconstruction_prompt_path
            ),
            "initial_consult_formulation": load_text(initial_consult_formulation_prompt_path),
            "initial_reasoning_output": (
                load_text(initial_reasoning_output_prompt_path)
                if initial_reasoning_output_prompt_path
                else ""
            ),
        }
        self.clinical_rules = clinical_rules
        self.guideline_runtime = guideline_runtime
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
        enable_guidelines: bool = True,
    ) -> "ThoracicRadiologyAgent":
        config = load_yaml(config_path)
        prompt_keys = (
            "initial_case_reconstruction_prompt",
            "initial_consult_formulation_prompt",
            "initial_reasoning_output_prompt",
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
            guideline_runtime=GuidelineRuntime.from_config(config) if enable_guidelines else None,
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
        reconstruction_input_json = _json(
            build_radiology_reconstruction_prompt_input(case_input, working_input)
        )
        evidence_input_json = _json(
            build_radiology_evidence_prompt_input(working_input)
        )
        rules_json = _json(self.clinical_rules)
        pointer_constraints = radiology_proposition_schema_constraints(working_input)

        reconstruction, reconstruction_trace = self._generate(
            stage="initial_case_reconstruction",
            schema_model=InitialCaseReconstruction,
            variables={
                "working_input": reconstruction_input_json,
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_case_reconstruction(
                result, case_input, working_input
            ),
            pointer_constraints=pointer_constraints,
        )
        formulation, formulation_trace = self._generate(
            stage="initial_consult_formulation",
            schema_model=InitialConsultFormulation,
            variables={
                "working_input": evidence_input_json,
                "case_reconstruction": _json(reconstruction),
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_initial_formulation(
                result, reconstruction, case_input, working_input
            ),
            pointer_constraints=pointer_constraints,
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

    def initial_consult(
        self, case_input: SpecialtyCaseInput
    ) -> SpecialtyInitialConsultResult:
        internal_state, trace = self.initial_assessment(case_input)
        working_input = build_radiology_working_input(case_input)
        diagnostic_evidence_ids = {
            evidence_id
            for unit in working_input.evidence_units
            for statement in unit.statements
            if statement.thoracic_imaging_eligible
            for evidence_id in statement.evidence_ids
        }
        formal_output, output_trace = self._generate(
            stage="initial_reasoning_output",
            schema_model=SpecialtyInitialOutput,
            variables={
                "working_input": _json(build_radiology_evidence_prompt_input(working_input)),
                "internal_state": _json(internal_state),
                "clinical_rules": _json(self.clinical_rules),
            },
            validation=lambda result: validate_specialty_initial_output(
                result,
                case_input,
                SpecialistTarget.THORACIC_RADIOLOGY,
                internal_state,
                diagnostic_evidence_ids,
            ),
            pointer_constraints=formal_evidence_schema_constraints(
                case_input, diagnostic_evidence_ids
            ),
        )
        return SpecialtyInitialConsultResult(
            internal_state=internal_state,
            formal_output=formal_output,
            trace=_append_trace(trace, "initial_reasoning_output", output_trace),
        )

    def _generate(
        self,
        *,
        stage,
        schema_model,
        variables,
        validation,
        pointer_constraints=None,
    ):
        guideline_context, allowed_chunks, retrieval_trace = (
            self.guideline_runtime.prepare(stage)
            if self.guideline_runtime
            else ("[]", {}, {"query": "", "candidates": [], "used_chunk_ids": []})
        )
        pointer_constraints = {
            **(pointer_constraints or {}),
            **guideline_evidence_schema_constraints(allowed_chunks),
        }
        variables = {
            **variables,
            "clinical_rules": _json(
                {
                    key: self.clinical_rules[key]
                    for key in _RULE_KEYS_BY_STAGE[stage]
                    if key in self.clinical_rules
                }
            ),
        }
        output_schema = (
            "由 API 的严格 JSON Schema response_format 提供。"
            if self.generator.response_format_mode == "json_schema"
            else prompt_schema_json(schema_model)
        )
        prompt = render_template(
            self.prompts[stage],
            {
                "output_schema": output_schema,
                **variables,
            },
        )
        if allowed_chunks:
            prompt = f"{prompt}\n\n{PROMPT_RULES}\n\n本轮检索到的指南片段：\n{guideline_context}"
        contract = specialty_output_contract(
            pointer_style="radiology_proposition",
            initial_stage=stage.startswith("initial_"),
        )
        prompt = f"{prompt}\n\n{contract}"

        def validate_with_guidelines(result):
            result = validation(result)
            retrieval_trace["used_chunk_ids"] = resolve_guideline_evidence(
                result, allowed_chunks
            )
            return result

        result, trace = self.generator.generate(
            schema_model=schema_model,
            schema_name=("specialty_initial" if stage == "initial_reasoning_output" else stage),
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            extra_validation=validate_with_guidelines,
            pointer_field_constraints=pointer_constraints,
        )
        trace["guideline_retrieval"] = retrieval_trace
        trace["prompt_components"] = {
            "total_chars": len(prompt),
            "output_schema_chars": len(output_schema),
            "guideline_context_chars": len(guideline_context) if allowed_chunks else 0,
            **{f"{key}_chars": len(value) for key, value in variables.items()},
        }
        return result, trace


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
    return prompt_json(value)


def _combined_trace(working_input: RadiologyWorkingInput, *stages) -> dict:
    return {
        "schema_version": "thoracic_radiology.v2",
        "working_input_summary": working_input.summary.model_dump(mode="json"),
        "stages": [{"stage": name, **trace} for name, trace in stages],
    }


def _append_trace(trace: dict, stage: str, stage_trace: dict) -> dict:
    return {**trace, "stages": [*trace.get("stages", []), {"stage": stage, **stage_trace}]}
