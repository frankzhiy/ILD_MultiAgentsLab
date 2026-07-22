"""Rheumatology specialist agent for staged ILD diagnostic consultation."""

from pathlib import Path
from typing import Any, Callable

from src.agents.common.evidence_projection import (
    build_specialty_evidence_prompt_input,
    build_specialty_working_input,
)
from src.agents.common.initial_output import SpecialtyInitialConsultResult, SpecialtyInitialOutput
from src.agents.common.initial_output_validation import (
    formal_evidence_schema_constraints,
    validate_specialty_initial_output,
)
from src.agents.common.prompt_contract import specialty_output_contract
from src.agents.common.validation import diagnostic_evidence_schema_constraints
from src.agents.rheumatology.models import (
    InitialAutoimmuneAssessment,
    InitialCaseReconstruction,
    InitialConsultFormulation,
    RheumatologyDomain,
    RheumatologyInitialAssessment,
)
from src.agents.rheumatology.validation import (
    require_rheumatology_input,
    validate_initial_assessment,
    validate_initial_stage,
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
    "你是严谨的 ILD 多学科团队风湿免疫会诊医生，只负责诊断与专业会诊，不是 MDT 主席。"
    "所有面向人的文本字段必须使用简体中文，医学标准缩写可保留；不得整句或整段使用英文。"
    "只返回符合 schema 的 JSON。"
)

_RULE_KEYS_BY_STAGE = {
    "initial_case_reconstruction": (),
    "initial_autoimmune_assessment": (
        "ipaf",
        "screening_and_risk",
        "specialist_boundaries",
    ),
    "initial_consult_formulation": (
        "diagnostic_confidence",
        "ctd_ild_diagnosis",
        "ipaf",
        "specialist_boundaries",
    ),
    "initial_reasoning_output": (
        "ctd_ild_diagnosis",
        "ipaf",
        "specialist_boundaries",
    ),
}

_CONTRACT_RULES_BY_STAGE = {
    "initial_autoimmune_assessment": (
        "rheumatic_disease_formulation.classification_status 为 established_rheumatic_disease、"
        "provisional_rheumatic_disease、overlap_rheumatic_disease、"
        "undifferentiated_autoimmune_state 或 ipaf_classification_possible 时，"
        "leading_diagnosis 必须是非空字符串。",
    ),
}


class RheumatologyAgent:
    def __init__(
        self,
        llm: LLMClient,
        *,
        initial_case_reconstruction_prompt_path: str | Path,
        initial_autoimmune_assessment_prompt_path: str | Path,
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
            "initial_case_reconstruction": load_text(initial_case_reconstruction_prompt_path),
            "initial_autoimmune_assessment": load_text(initial_autoimmune_assessment_prompt_path),
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
            response_format_mode="json_schema" if getattr(llm, "supports_json_schema", False) else "json_object",
            event_callback=event_callback,
        )

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        llm: LLMClient,
        *,
        event_callback=None,
        enable_guidelines: bool = True,
    ) -> "RheumatologyAgent":
        config = load_yaml(config_path)
        keys = (
            "initial_case_reconstruction_prompt",
            "initial_autoimmune_assessment_prompt",
            "initial_consult_formulation_prompt",
            "initial_reasoning_output_prompt",
        )
        if missing := [key for key in keys if key not in config]:
            raise ValueError(f"Rheumatology config is missing prompt paths: {missing}")
        return cls(
            llm,
            **{f"{key}_path": config[key] for key in keys},
            clinical_rules=config.get("clinical_rules", {}),
            temperature=float(config.get("temperature", 0.0)),
            max_tokens=int(config.get("max_tokens", 12000)),
            max_attempts=int(config.get("max_attempts", 2)),
            retry_backoff_seconds=float(config.get("retry_backoff_seconds", 2)),
            event_callback=event_callback,
            guideline_runtime=GuidelineRuntime.from_config(config) if enable_guidelines else None,
        )

    def initial_assessment(self, case_input: SpecialtyCaseInput) -> tuple[RheumatologyInitialAssessment, dict]:
        require_rheumatology_input(case_input)
        case_json = _json(build_specialty_working_input(case_input))
        evidence_json = _json(build_specialty_evidence_prompt_input(case_input))
        rules_json = _json(self.clinical_rules)
        pointer_constraints = diagnostic_evidence_schema_constraints(case_input)
        reconstruction, reconstruction_trace = self._generate(
            "initial_case_reconstruction", InitialCaseReconstruction,
            {"case_input": case_json, "clinical_rules": rules_json},
            lambda result: validate_initial_stage(result, case_input),
            pointer_constraints,
        )
        autoimmune, autoimmune_trace = self._generate(
            "initial_autoimmune_assessment", InitialAutoimmuneAssessment,
            {"case_input": evidence_json, "case_reconstruction": _json(reconstruction), "clinical_rules": rules_json},
            lambda result: validate_initial_stage(result, case_input, self.clinical_rules),
            pointer_constraints,
        )
        formulation, formulation_trace = self._generate(
            "initial_consult_formulation", InitialConsultFormulation,
            {
                "case_input": evidence_json,
                "case_reconstruction": _json(reconstruction),
                "autoimmune_assessment": _json(autoimmune),
                "clinical_rules": rules_json,
            },
            lambda result: validate_initial_stage(result, case_input, self.clinical_rules),
            pointer_constraints,
        )
        result = RheumatologyInitialAssessment(
            case_id=case_input.case_id,
            domain_reviews=sorted(
                [*reconstruction.domain_reviews, *autoimmune.domain_reviews, *formulation.domain_reviews],
                key=lambda item: list(RheumatologyDomain).index(RheumatologyDomain(item.domain)),
            ),
            case_orientation=reconstruction.case_orientation,
            autoimmune_manifestations=reconstruction.autoimmune_manifestations,
            serologic_findings=autoimmune.serologic_findings,
            rheumatic_disease_formulation=autoimmune.rheumatic_disease_formulation,
            activity_and_risk=autoimmune.activity_and_risk,
            ild_attribution=formulation.ild_attribution,
            specialist_dependencies=formulation.specialist_dependencies,
            reference_observations=formulation.reference_observations,
            missing_data=formulation.missing_data,
            limitations=[*reconstruction.limitations, *autoimmune.limitations, *formulation.limitations],
        )
        validate_initial_assessment(result, case_input, self.clinical_rules)
        return result, _combined_trace(
            ("initial_case_reconstruction", reconstruction_trace),
            ("initial_autoimmune_assessment", autoimmune_trace),
            ("initial_consult_formulation", formulation_trace),
        )

    def initial_consult(self, case_input: SpecialtyCaseInput) -> SpecialtyInitialConsultResult:
        internal_state, trace = self.initial_assessment(case_input)
        formal_output, output_trace = self._generate(
            "initial_reasoning_output",
            SpecialtyInitialOutput,
            {
                "case_input": _json(build_specialty_evidence_prompt_input(case_input)),
                "internal_state": _json(internal_state),
                "clinical_rules": _json(self.clinical_rules),
            },
            lambda result: validate_specialty_initial_output(
                result, case_input, SpecialistTarget.RHEUMATOLOGY, internal_state
            ),
            formal_evidence_schema_constraints(case_input),
        )
        return SpecialtyInitialConsultResult(
            internal_state=internal_state,
            formal_output=formal_output,
            trace=_append_trace(trace, "initial_reasoning_output", output_trace),
        )

    def _generate(
        self,
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
            {"output_schema": output_schema, **variables},
        )
        if allowed_chunks:
            prompt = f"{prompt}\n\n{PROMPT_RULES}\n\n本轮检索到的指南片段：\n{guideline_context}"
        contract = specialty_output_contract(
            pointer_style="evidence_id",
            initial_stage=stage.startswith("initial_"),
            partitioned_evidence=stage != "initial_case_reconstruction",
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


def _json(value: object) -> str:
    return prompt_json(value)


def _combined_trace(*stages) -> dict:
    return {"schema_version": "rheumatology.v1", "stages": [{"stage": name, **trace} for name, trace in stages]}


def _append_trace(trace: dict, stage: str, stage_trace: dict) -> dict:
    return {**trace, "stages": [*trace.get("stages", []), {"stage": stage, **stage_trace}]}
