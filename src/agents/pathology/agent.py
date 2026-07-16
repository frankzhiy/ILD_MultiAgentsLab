"""Pathology specialist agent for staged ILD diagnostic consultation."""

from pathlib import Path
from typing import Any, Callable

from src.agents.common.evidence_projection import (
    build_specialty_evidence_prompt_input,
    build_specialty_working_input,
)
from src.agents.common.prompt_contract import specialty_output_contract
from src.agents.common.validation import diagnostic_evidence_schema_constraints
from src.agents.pathology.models import (
    DiscussionConsultOutput,
    DiscussionEvidenceMap,
    DiscussionStateUpdate,
    InitialConsultFormulation,
    InitialMorphologicAssessment,
    InitialSpecimenReconstruction,
    PathologyDiscussionInput,
    PathologyDiscussionResponse,
    PathologyDomain,
    PathologyInitialAssessment,
)
from src.agents.pathology.validation import (
    require_pathology_input,
    validate_consult_output,
    validate_discussion_response,
    validate_evidence_map,
    validate_initial_assessment,
    validate_initial_stage,
    validate_specialist_opinions,
    validate_state_update,
)
from src.guidelines.runtime import GuidelineRuntime, PROMPT_RULES, resolve_guideline_evidence
from src.llm.base import LLMClient
from src.llm.prompting import prompt_json, prompt_schema_json
from src.llm.structured import StructuredLLMGenerator
from src.schemas.specialty_agent_input import SpecialtyCaseInput
from src.utils.config import load_text, load_yaml, render_template


SYSTEM_PROMPT = (
    "你是严谨的 ILD 多学科团队肺病理会诊医生。你只能解释病例中提供的病理文字资料，"
    "不能读取玻片或数字切片；原报告、转述和你的有限解释必须严格分层。"
    "你负责标本可评价性、组织学模式和病因线索，不是 MDT 主席，不输出最终疾病诊断。"
    "所有面向人的文本字段使用简体中文，医学标准缩写可保留；只返回符合 schema 的 JSON。"
)

_RULE_KEYS_BY_STAGE = {
    "initial_specimen_reconstruction": ("sampling", "biopsy_scope", "boundaries"),
    "initial_morphologic_assessment": (
        "morphology",
        "ipf_histopathology",
        "terminology",
        "boundaries",
    ),
    "initial_consult_formulation": (
        "morphology",
        "ipf_histopathology",
        "terminology",
        "sampling",
        "biopsy_scope",
        "boundaries",
    ),
    "discussion_evidence_mapping": (),
    "discussion_state_update": (
        "morphology",
        "ipf_histopathology",
        "terminology",
        "sampling",
        "biopsy_scope",
        "boundaries",
    ),
    "discussion_consult_response": (
        "morphology",
        "ipf_histopathology",
        "biopsy_scope",
        "boundaries",
    ),
}

_CONTRACT_RULES_BY_STAGE = {
    "initial_consult_formulation": (
        "pathology_formulation 只能形成病理模式和病因提示，不得输出最终 MDT 疾病诊断。",
    ),
    "discussion_consult_response": (
        "不得输出 final_mdt_diagnosis 或声称跨专业共识已经形成。",
    ),
}


class PathologyAgent:
    def __init__(
        self,
        llm: LLMClient,
        *,
        initial_specimen_reconstruction_prompt_path: str | Path,
        initial_morphologic_assessment_prompt_path: str | Path,
        initial_consult_formulation_prompt_path: str | Path,
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
            "initial_specimen_reconstruction": load_text(
                initial_specimen_reconstruction_prompt_path
            ),
            "initial_morphologic_assessment": load_text(
                initial_morphologic_assessment_prompt_path
            ),
            "initial_consult_formulation": load_text(initial_consult_formulation_prompt_path),
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
    ) -> "PathologyAgent":
        config = load_yaml(config_path)
        keys = (
            "initial_specimen_reconstruction_prompt",
            "initial_morphologic_assessment_prompt",
            "initial_consult_formulation_prompt",
            "discussion_evidence_mapping_prompt",
            "discussion_state_update_prompt",
            "discussion_consult_response_prompt",
        )
        if missing := [key for key in keys if key not in config]:
            raise ValueError(f"Pathology config is missing prompt paths: {missing}")
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

    def initial_assessment(
        self, case_input: SpecialtyCaseInput
    ) -> tuple[PathologyInitialAssessment, dict]:
        require_pathology_input(case_input)
        case_json = _json(build_specialty_working_input(case_input))
        evidence_json = _json(build_specialty_evidence_prompt_input(case_input))
        rules_json = _json(self.clinical_rules)
        pointer_constraints = diagnostic_evidence_schema_constraints(case_input)

        reconstruction, reconstruction_trace = self._generate(
            "initial_specimen_reconstruction",
            InitialSpecimenReconstruction,
            {"case_input": case_json, "clinical_rules": rules_json},
            lambda result: validate_initial_stage(result, case_input),
            pointer_constraints,
        )
        morphology, morphology_trace = self._generate(
            "initial_morphologic_assessment",
            InitialMorphologicAssessment,
            {
                "case_input": evidence_json,
                "specimen_reconstruction": _json(reconstruction),
                "clinical_rules": rules_json,
            },
            lambda result: validate_initial_stage(result, case_input),
            pointer_constraints,
        )
        formulation, formulation_trace = self._generate(
            "initial_consult_formulation",
            InitialConsultFormulation,
            {
                "case_input": evidence_json,
                "specimen_reconstruction": _json(reconstruction),
                "morphologic_assessment": _json(morphology),
                "clinical_rules": rules_json,
            },
            lambda result: validate_initial_stage(result, case_input),
            pointer_constraints,
        )
        result = PathologyInitialAssessment(
            case_id=case_input.case_id,
            domain_reviews=sorted(
                [
                    *reconstruction.domain_reviews,
                    *morphology.domain_reviews,
                    *formulation.domain_reviews,
                ],
                key=lambda item: list(PathologyDomain).index(PathologyDomain(item.domain)),
            ),
            source_assessment=reconstruction.source_assessment,
            specimens=reconstruction.specimens,
            morphologic_features=morphology.morphologic_features,
            pattern_assessments=morphology.pattern_assessments,
            etiologic_associations=morphology.etiologic_associations,
            ancillary_studies=morphology.ancillary_studies,
            pathology_formulation=formulation.pathology_formulation,
            specialist_dependencies=formulation.specialist_dependencies,
            reference_observations=formulation.reference_observations,
            missing_data=formulation.missing_data,
            limitations=[
                *reconstruction.limitations,
                *morphology.limitations,
                *formulation.limitations,
            ],
        )
        validate_initial_assessment(result, case_input, self.clinical_rules)
        return result, _combined_trace(
            ("initial_specimen_reconstruction", reconstruction_trace),
            ("initial_morphologic_assessment", morphology_trace),
            ("initial_consult_formulation", formulation_trace),
        )

    def discussion_response(
        self, discussion_input: PathologyDiscussionInput
    ) -> tuple[PathologyDiscussionResponse, dict]:
        case = discussion_input.case_input
        require_pathology_input(case)
        validate_initial_assessment(
            discussion_input.initial_assessment, case, self.clinical_rules
        )
        validate_specialist_opinions(discussion_input)
        discussion_json = _json(
            {
                "case_input": build_specialty_evidence_prompt_input(case),
                "initial_assessment": discussion_input.initial_assessment,
                "specialist_opinions": discussion_input.specialist_opinions,
                "chair_questions": discussion_input.chair_questions,
            }
        )
        rules_json = _json(self.clinical_rules)
        pointer_constraints = diagnostic_evidence_schema_constraints(
            case, discussion_input.specialist_opinions
        )
        evidence_map, map_trace = self._generate(
            "discussion_evidence_mapping",
            DiscussionEvidenceMap,
            {"discussion_input": discussion_json, "clinical_rules": rules_json},
            lambda result: validate_evidence_map(result, discussion_input),
            pointer_constraints,
        )
        state_update, update_trace = self._generate(
            "discussion_state_update",
            DiscussionStateUpdate,
            {
                "discussion_input": discussion_json,
                "evidence_map": _json(evidence_map),
                "clinical_rules": rules_json,
            },
            lambda result: validate_state_update(
                result, discussion_input, self.clinical_rules
            ),
            pointer_constraints,
        )
        consult, consult_trace = self._generate(
            "discussion_consult_response",
            DiscussionConsultOutput,
            {
                "discussion_input": discussion_json,
                "updated_state": _json(state_update.updated_state),
                "state_delta": _json(state_update.domain_changes),
                "clinical_rules": rules_json,
            },
            lambda result: validate_consult_output(result, discussion_input),
            pointer_constraints,
        )
        result = PathologyDiscussionResponse(
            case_id=case.case_id,
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
            self.prompts[stage], {"output_schema": output_schema, **variables}
        )
        if allowed_chunks:
            prompt = (
                f"{prompt}\n\n{PROMPT_RULES}\n\n本轮检索到的指南片段：\n"
                f"{guideline_context}"
            )
        contract = specialty_output_contract(
            pointer_style="evidence_id",
            initial_stage=stage.startswith("initial_"),
            partitioned_evidence=stage != "initial_specimen_reconstruction",
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
        "schema_version": "pathology.v1",
        "stages": [{"stage": name, **trace} for name, trace in stages],
    }
