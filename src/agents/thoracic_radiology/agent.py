"""Text-description-based thoracic radiology specialist agent for ILD MDT."""

import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from src.agents.thoracic_radiology.models import (
    DiscussionConsultOutput,
    DiscussionEvidenceMap,
    DiscussionStateUpdate,
    ImagingInterpretationState,
    InitialImagingFormulation,
    InitialMorphologicAssessment,
    InitialSourceReconstruction,
    ThoracicRadiologyDiscussionInput,
    ThoracicRadiologyDiscussionResponse,
    ThoracicRadiologyInitialAssessment,
)
from src.agents.thoracic_radiology.validation import (
    require_radiology_input as _require_radiology_input,
    validate_consult_output as _validate_consult_output,
    validate_discussion_response,
    validate_evidence_map as _validate_evidence_map,
    validate_formulation_stage as _validate_formulation_stage,
    validate_initial_assessment,
    validate_morphology_stage as _validate_morphology_stage,
    validate_source_stage as _validate_source_stage,
    validate_specialist_opinions as _validate_specialist_opinions,
    validate_state_update as _validate_state_update,
)
from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.schemas.specialty_agent_input import SpecialtyCaseInput
from src.utils.config import load_text, load_yaml, render_template


SYSTEM_PROMPT = (
    "你是严谨的 ILD 胸部影像科会诊医生，只能分析病例中的影像文字描述，"
    "不能读取原始图像。你负责影像诊断与专业会诊，不是 MDT 主席。"
    "所有面向人的文本字段必须使用简体中文，标准医学缩写可保留；"
    "不得整句或整段使用英文。只返回符合 schema 的 JSON。"
)


class ThoracicRadiologyAgent:
    def __init__(
        self,
        llm: LLMClient,
        *,
        initial_source_reconstruction_prompt_path: str | Path,
        initial_morphologic_assessment_prompt_path: str | Path,
        initial_imaging_formulation_prompt_path: str | Path,
        discussion_evidence_mapping_prompt_path: str | Path,
        discussion_imaging_update_prompt_path: str | Path,
        discussion_consult_response_prompt_path: str | Path,
        clinical_rules: dict,
        temperature: float,
        max_tokens: int,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.0,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.prompts = {
            "initial_source_reconstruction": load_text(initial_source_reconstruction_prompt_path),
            "initial_morphologic_assessment": load_text(initial_morphologic_assessment_prompt_path),
            "initial_imaging_formulation": load_text(initial_imaging_formulation_prompt_path),
            "discussion_evidence_mapping": load_text(discussion_evidence_mapping_prompt_path),
            "discussion_imaging_update": load_text(discussion_imaging_update_prompt_path),
            "discussion_consult_response": load_text(discussion_consult_response_prompt_path),
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
            "initial_source_reconstruction_prompt",
            "initial_morphologic_assessment_prompt",
            "initial_imaging_formulation_prompt",
            "discussion_evidence_mapping_prompt",
            "discussion_imaging_update_prompt",
            "discussion_consult_response_prompt",
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

    def initial_assessment(
        self, case_input: SpecialtyCaseInput
    ) -> tuple[ThoracicRadiologyInitialAssessment, dict]:
        _require_radiology_input(case_input)
        case_json = _json(case_input)
        rules_json = _json(self.clinical_rules)

        source, source_trace = self._generate(
            stage="initial_source_reconstruction",
            schema_model=InitialSourceReconstruction,
            variables={"case_input": case_json, "clinical_rules": rules_json},
            validation=lambda result: _validate_source_stage(result, case_input),
        )
        morphology, morphology_trace = self._generate(
            stage="initial_morphologic_assessment",
            schema_model=InitialMorphologicAssessment,
            variables={
                "case_input": case_json,
                "source_reconstruction": _json(source),
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_morphology_stage(
                result, case_input, self.clinical_rules
            ),
        )
        formulation, formulation_trace = self._generate(
            stage="initial_imaging_formulation",
            schema_model=InitialImagingFormulation,
            variables={
                "case_input": case_json,
                "source_reconstruction": _json(source),
                "morphologic_assessment": _json(morphology),
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_formulation_stage(
                result, case_input, self.clinical_rules
            ),
        )
        result = ThoracicRadiologyInitialAssessment(
            case_id=case_input.case_id,
            domain_reviews=sorted(
                [
                    *source.domain_reviews,
                    *morphology.domain_reviews,
                    *formulation.domain_reviews,
                ],
                key=lambda item: list(type(item.domain)).index(item.domain),
            ),
            source_state=source.source_state,
            observation_state=morphology.observation_state,
            interpretation_state=ImagingInterpretationState(
                morphologic_pattern=formulation.morphologic_pattern,
                conditional_classifications=formulation.conditional_classifications,
                disease_associations=formulation.disease_associations,
                longitudinal_assessment=morphology.longitudinal_assessment,
                discordances=formulation.discordances,
            ),
            specialist_dependencies=formulation.specialist_dependencies,
            direct_review_requests=[
                *source.direct_review_requests,
                *morphology.direct_review_requests,
                *formulation.direct_review_requests,
            ],
            missing_data=formulation.missing_data,
            limitations=[
                *source.limitations,
                *morphology.limitations,
                *formulation.limitations,
            ],
        )
        validate_initial_assessment(result, case_input, self.clinical_rules)
        return result, _combined_trace(
            ("initial_source_reconstruction", source_trace),
            ("initial_morphologic_assessment", morphology_trace),
            ("initial_imaging_formulation", formulation_trace),
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
        discussion_json = _json(discussion_input)
        rules_json = _json(self.clinical_rules)

        evidence_map, map_trace = self._generate(
            stage="discussion_evidence_mapping",
            schema_model=DiscussionEvidenceMap,
            variables={"discussion_input": discussion_json, "clinical_rules": rules_json},
            validation=lambda result: _validate_evidence_map(result, discussion_input),
        )
        update, update_trace = self._generate(
            stage="discussion_imaging_update",
            schema_model=DiscussionStateUpdate,
            variables={
                "discussion_input": discussion_json,
                "evidence_map": _json(evidence_map),
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_state_update(
                result, discussion_input, self.clinical_rules
            ),
        )
        consult, consult_trace = self._generate(
            stage="discussion_consult_response",
            schema_model=DiscussionConsultOutput,
            variables={
                "discussion_input": discussion_json,
                "updated_state": _json(update.updated_state),
                "state_delta": _json(update.domain_changes),
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_consult_output(result, discussion_input),
        )
        result = ThoracicRadiologyDiscussionResponse(
            case_id=case_input.case_id,
            updated_state=update.updated_state,
            domain_changes=update.domain_changes,
            specialist_opinions_used=evidence_map.specialist_opinions_used,
            mapped_findings=evidence_map.mapped_findings,
            chair_answers=consult.chair_answers,
            unresolved_conflicts=[
                *evidence_map.unresolved_conflicts,
                *consult.unresolved_conflicts,
            ],
            imaging_recommendations=consult.imaging_recommendations,
            limitations=consult.limitations,
        )
        validate_discussion_response(result, discussion_input, self.clinical_rules)
        return result, _combined_trace(
            ("discussion_evidence_mapping", map_trace),
            ("discussion_imaging_update", update_trace),
            ("discussion_consult_response", consult_trace),
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


def _combined_trace(*stages) -> dict:
    return {
        "schema_version": "thoracic_radiology.v1",
        "stages": [{"stage": name, **trace} for name, trace in stages],
    }
