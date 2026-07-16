"""Pulmonology specialist agent for staged ILD diagnostic consultation."""

from pathlib import Path
from typing import Any, Callable

from src.agents.common.evidence_projection import (
    build_specialty_evidence_prompt_input,
    build_specialty_working_input,
)
from src.agents.common.prompt_contract import specialty_output_contract
from src.agents.common.validation import diagnostic_evidence_schema_constraints
from src.agents.pulmonology.models import (
    DiscussionConsultOutput,
    DiscussionEvidenceMap,
    DiscussionStateUpdate,
    InitialDiagnosticFormulation,
    InitialFoundation,
    InitialPulmonaryAssessment,
    PulmonologyDiscussionInput,
    PulmonologyDiscussionResponse,
    PulmonologyInitialAssessment,
)
from src.agents.pulmonology.validation import (
    require_pulmonology_input as _require_pulmonology_input,
    validate_consult_output as _validate_consult_output,
    validate_discussion_response,
    validate_evidence_map as _validate_evidence_map,
    validate_initial_assessment,
    validate_initial_stage as _validate_initial_stage,
    validate_specialist_opinions as _validate_specialist_opinions,
    validate_state_update as _validate_state_update,
)
from src.guidelines.runtime import GuidelineRuntime, PROMPT_RULES, resolve_guideline_evidence
from src.llm.base import LLMClient
from src.llm.prompting import prompt_json, prompt_schema_json
from src.llm.structured import StructuredLLMGenerator
from src.schemas.specialty_agent_input import SpecialtyCaseInput
from src.utils.config import load_text, load_yaml, render_template


SYSTEM_PROMPT = (
    "你是严谨的 ILD 呼吸科会诊医生，只负责诊断与专业会诊，"
    "不是 MDT 主席。所有面向人的文本字段必须使用简体中文，医学标准缩写可保留；"
    "不得整句或整段使用英文。只返回符合 schema 的 JSON。"
)

_RULE_KEYS_BY_STAGE = {
    "initial_foundation": (),
    "initial_pulmonary_assessment": ("ppf",),
    "initial_diagnostic_formulation": ("diagnostic_confidence", "ppf"),
    "discussion_evidence_mapping": (),
    "discussion_state_update": ("diagnostic_confidence", "ppf"),
    "discussion_consult_response": ("diagnostic_confidence", "ppf"),
}

_CONTRACT_RULES_BY_STAGE = {
    "initial_diagnostic_formulation": (
        "diagnostic_formulation.classification_status 不是 insufficient_data 时，"
        "leading_diagnosis 必须是非空字符串。",
    ),
    "discussion_state_update": (
        "updated_state.diagnostic_formulation.classification_status 不是 insufficient_data 时，"
        "leading_diagnosis 必须是非空字符串。",
    ),
}


class PulmonologyAgent:
    def __init__(
        self,
        llm: LLMClient,
        *,
        initial_foundation_prompt_path: str | Path,
        initial_pulmonary_assessment_prompt_path: str | Path,
        initial_diagnostic_formulation_prompt_path: str | Path,
        discussion_evidence_mapping_prompt_path: str | Path,
        discussion_state_update_prompt_path: str | Path,
        discussion_consult_response_prompt_path: str | Path,
        clinical_rules: dict,
        temperature: float,
        max_tokens: int,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.0,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
        guideline_runtime: GuidelineRuntime | None = None,
    ) -> None:
        self.prompts = {
            "initial_foundation": load_text(initial_foundation_prompt_path),
            "initial_pulmonary_assessment": load_text(initial_pulmonary_assessment_prompt_path),
            "initial_diagnostic_formulation": load_text(initial_diagnostic_formulation_prompt_path),
            "discussion_evidence_mapping": load_text(discussion_evidence_mapping_prompt_path),
            "discussion_state_update": load_text(discussion_state_update_prompt_path),
            "discussion_consult_response": load_text(discussion_consult_response_prompt_path),
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
    ) -> "PulmonologyAgent":
        config = load_yaml(config_path)
        prompt_keys = (
            "initial_foundation_prompt",
            "initial_pulmonary_assessment_prompt",
            "initial_diagnostic_formulation_prompt",
            "discussion_evidence_mapping_prompt",
            "discussion_state_update_prompt",
            "discussion_consult_response_prompt",
        )
        missing = [key for key in prompt_keys if key not in config]
        if missing:
            raise ValueError(f"Pulmonology config is missing prompt paths: {missing}")
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

    def initial_assessment(
        self,
        case_input: SpecialtyCaseInput,
    ) -> tuple[PulmonologyInitialAssessment, dict]:
        _require_pulmonology_input(case_input)
        case_json = _json(build_specialty_working_input(case_input))
        evidence_json = _json(build_specialty_evidence_prompt_input(case_input))
        rules_json = _json(self.clinical_rules)
        pointer_constraints = diagnostic_evidence_schema_constraints(case_input)

        foundation, foundation_trace = self._generate(
            stage="initial_foundation",
            schema_model=InitialFoundation,
            variables={"case_input": case_json, "clinical_rules": rules_json},
            validation=lambda result: _validate_initial_stage(result, case_input),
            pointer_constraints=pointer_constraints,
        )
        pulmonary, pulmonary_trace = self._generate(
            stage="initial_pulmonary_assessment",
            schema_model=InitialPulmonaryAssessment,
            variables={
                "case_input": evidence_json,
                "clinical_foundation": _json(foundation),
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_initial_stage(
                result, case_input, self.clinical_rules
            ),
            pointer_constraints=pointer_constraints,
        )
        formulation, formulation_trace = self._generate(
            stage="initial_diagnostic_formulation",
            schema_model=InitialDiagnosticFormulation,
            variables={
                "case_input": evidence_json,
                "clinical_foundation": _json(foundation),
                "pulmonary_assessment": _json(pulmonary),
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_initial_stage(result, case_input),
            pointer_constraints=pointer_constraints,
        )
        result = PulmonologyInitialAssessment(
            case_id=case_input.case_id,
            domain_reviews=sorted(
                [
                    *foundation.domain_reviews,
                    *pulmonary.domain_reviews,
                    *formulation.domain_reviews,
                ],
                key=lambda item: list(type(item.domain)).index(item.domain),
            ),
            clinical_phenotype=foundation.clinical_phenotype,
            secondary_cause_assessment=foundation.secondary_cause_assessment,
            pulmonary_severity=pulmonary.pulmonary_severity,
            respiratory_test_interpretation=pulmonary.respiratory_test_interpretation,
            bronchoscopy_assessment=pulmonary.bronchoscopy_assessment,
            specialist_dependencies=[
                *pulmonary.specialist_dependencies,
                *formulation.specialist_dependencies,
            ],
            reference_observations=[
                *pulmonary.reference_observations,
                *formulation.reference_observations,
            ],
            progression_assessment=pulmonary.progression_assessment,
            diagnostic_formulation=formulation.diagnostic_formulation,
            missing_data=formulation.missing_data,
            limitations=[
                *foundation.limitations,
                *pulmonary.limitations,
                *formulation.limitations,
            ],
        )
        validate_initial_assessment(result, case_input, self.clinical_rules)
        return result, _combined_trace(
            ("initial_foundation", foundation_trace),
            ("initial_pulmonary_assessment", pulmonary_trace),
            ("initial_diagnostic_formulation", formulation_trace),
        )

    def discussion_response(
        self,
        discussion_input: PulmonologyDiscussionInput,
    ) -> tuple[PulmonologyDiscussionResponse, dict]:
        case_input = discussion_input.case_input
        _require_pulmonology_input(case_input)
        validate_initial_assessment(
            discussion_input.initial_assessment,
            case_input,
            self.clinical_rules,
        )
        _validate_specialist_opinions(discussion_input)
        discussion_json = _json(
            {
                "case_input": build_specialty_evidence_prompt_input(case_input),
                "initial_assessment": discussion_input.initial_assessment,
                "specialist_opinions": discussion_input.specialist_opinions,
                "chair_questions": discussion_input.chair_questions,
            }
        )
        rules_json = _json(self.clinical_rules)
        pointer_constraints = diagnostic_evidence_schema_constraints(
            case_input,
            discussion_input.specialist_opinions,
        )

        evidence_map, map_trace = self._generate(
            stage="discussion_evidence_mapping",
            schema_model=DiscussionEvidenceMap,
            variables={
                "discussion_input": discussion_json,
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_evidence_map(result, discussion_input),
            pointer_constraints=pointer_constraints,
        )
        state_update, update_trace = self._generate(
            stage="discussion_state_update",
            schema_model=DiscussionStateUpdate,
            variables={
                "discussion_input": discussion_json,
                "evidence_map": _json(evidence_map),
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_state_update(
                result, discussion_input, self.clinical_rules
            ),
            pointer_constraints=pointer_constraints,
        )
        consult, consult_trace = self._generate(
            stage="discussion_consult_response",
            schema_model=DiscussionConsultOutput,
            variables={
                "discussion_input": discussion_json,
                "updated_state": _json(state_update.updated_state),
                "state_delta": _json(state_update.domain_changes),
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_consult_output(result, discussion_input),
            pointer_constraints=pointer_constraints,
        )
        result = PulmonologyDiscussionResponse(
            case_id=case_input.case_id,
            updated_state=state_update.updated_state,
            domain_changes=state_update.domain_changes,
            specialist_opinions_used=evidence_map.specialist_opinions_used,
            mapped_findings=evidence_map.mapped_findings,
            chair_answers=consult.chair_answers,
            unresolved_conflicts=[
                *evidence_map.unresolved_conflicts,
                *consult.unresolved_conflicts,
            ],
            diagnostic_recommendations=consult.diagnostic_recommendations,
            limitations=consult.limitations,
        )
        validate_discussion_response(result, discussion_input, self.clinical_rules)
        return result, _combined_trace(
            ("discussion_evidence_mapping", map_trace),
            ("discussion_state_update", update_trace),
            ("discussion_consult_response", consult_trace),
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
            pointer_style="evidence_id",
            initial_stage=stage.startswith("initial_"),
            partitioned_evidence=stage != "initial_foundation",
            extra_rules=_CONTRACT_RULES_BY_STAGE.get(stage, ()),
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
            schema_name=stage,
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


def _json(value: object) -> str:
    return prompt_json(value)


def _combined_trace(*stages) -> dict:
    return {
        "schema_version": "pulmonology.v2",
        "stages": [{"stage": name, **trace} for name, trace in stages],
    }
