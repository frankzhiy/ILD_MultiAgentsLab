"""Pulmonology specialist agent for staged ILD diagnostic consultation."""

import json
from pathlib import Path
from typing import Any, Callable, Iterable

from pydantic import BaseModel

from src.agents.pulmonology.models import (
    DiscussionConsultOutput,
    DiscussionEvidenceMap,
    DiscussionStateUpdate,
    EvidencePointer,
    InitialDiagnosticFormulation,
    InitialFoundation,
    InitialPulmonaryAssessment,
    PulmonologyClinicalState,
    PulmonologyDiscussionInput,
    PulmonologyDiscussionResponse,
    PulmonologyInitialAssessment,
)
from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import SpecialtyCaseInput, SpecialtyUnitInput
from src.utils.config import load_text, load_yaml, render_template


SYSTEM_PROMPT = (
    "你是严谨的 ILD 呼吸科会诊医生，只负责诊断与专业会诊，"
    "不是 MDT 主席。所有面向人的文本字段必须使用简体中文，医学标准缩写可保留；"
    "不得整句或整段使用英文。只返回符合 schema 的 JSON。"
)

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
        )

    def initial_assessment(
        self,
        case_input: SpecialtyCaseInput,
    ) -> tuple[PulmonologyInitialAssessment, dict]:
        _require_pulmonology_input(case_input)
        case_json = _json(case_input)
        rules_json = _json(self.clinical_rules)

        foundation, foundation_trace = self._generate(
            stage="initial_foundation",
            schema_model=InitialFoundation,
            variables={"case_input": case_json, "clinical_rules": rules_json},
            validation=lambda result: _validate_initial_stage(result, case_input),
        )
        pulmonary, pulmonary_trace = self._generate(
            stage="initial_pulmonary_assessment",
            schema_model=InitialPulmonaryAssessment,
            variables={
                "case_input": case_json,
                "clinical_foundation": _json(foundation),
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_initial_stage(
                result, case_input, self.clinical_rules
            ),
        )
        formulation, formulation_trace = self._generate(
            stage="initial_diagnostic_formulation",
            schema_model=InitialDiagnosticFormulation,
            variables={
                "case_input": case_json,
                "clinical_foundation": _json(foundation),
                "pulmonary_assessment": _json(pulmonary),
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_initial_stage(result, case_input),
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
        discussion_json = _json(discussion_input)
        rules_json = _json(self.clinical_rules)

        evidence_map, map_trace = self._generate(
            stage="discussion_evidence_mapping",
            schema_model=DiscussionEvidenceMap,
            variables={
                "discussion_input": discussion_json,
                "clinical_rules": rules_json,
            },
            validation=lambda result: _validate_evidence_map(result, discussion_input),
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


def validate_initial_assessment(
    result: PulmonologyInitialAssessment,
    case_input: SpecialtyCaseInput,
    clinical_rules: dict | None = None,
) -> PulmonologyInitialAssessment:
    if result.case_id and result.case_id != case_input.case_id:
        raise ValueError(f"Assessment case_id {result.case_id} does not match {case_input.case_id}")
    result.case_id = case_input.case_id
    _resolve_evidence_pointers(result, case_input)
    _validate_clinical_state(result, case_input, {}, clinical_rules)
    return result


def validate_discussion_response(
    result: PulmonologyDiscussionResponse,
    discussion_input: PulmonologyDiscussionInput,
    clinical_rules: dict | None = None,
) -> PulmonologyDiscussionResponse:
    case_input = discussion_input.case_input
    if result.case_id and result.case_id != case_input.case_id:
        raise ValueError(f"Discussion case_id {result.case_id} does not match {case_input.case_id}")
    result.case_id = case_input.case_id
    _resolve_evidence_pointers(result, case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _validate_used_opinions(result.specialist_opinions_used, opinions)
    _validate_clinical_state(result.updated_state, case_input, opinions, clinical_rules)
    _validate_domain_changes(result.domain_changes, case_input, opinions)
    _validate_mapped_findings(result.mapped_findings, case_input, opinions)
    _validate_clinical_items(result.unresolved_conflicts, case_input, opinions)
    _validate_chair_answers(result.chair_answers, discussion_input, opinions)
    return result


def _validate_initial_stage(
    result: BaseModel,
    case_input: SpecialtyCaseInput,
    clinical_rules: dict | None = None,
):
    _resolve_evidence_pointers(result, case_input)
    units = _case_units(case_input)
    diagnostic_items: list[object] = []
    for field in (
        "clinical_phenotype",
        "pulmonary_severity",
        "bronchoscopy_assessment",
        "diagnostic_formulation",
    ):
        item = getattr(result, field, None)
        if item is not None:
            diagnostic_items.append(item)
    diagnostic_items.extend(getattr(result, "secondary_cause_assessment", []))
    diagnostic_items.extend(getattr(result, "respiratory_test_interpretation", []))
    progression = getattr(result, "progression_assessment", None)
    if progression:
        diagnostic_items.extend(progression.components)
    formulation = getattr(result, "diagnostic_formulation", None)
    if formulation:
        if formulation.morphologic_pattern:
            diagnostic_items.append(formulation.morphologic_pattern)
        diagnostic_items.extend(formulation.differential_diagnoses)
    _validate_clinical_items(diagnostic_items, case_input, {})
    for gap in getattr(result, "missing_data", []):
        _validate_pointers(gap.related_evidence, units)
    for question in getattr(result, "specialist_dependencies", []):
        _validate_pointers(question.related_evidence, units)
    for observation in getattr(result, "reference_observations", []):
        _validate_pointers(observation.related_evidence, units)
    _validate_ppf_rule(progression, clinical_rules)
    for question in getattr(result, "specialist_dependencies", []):
        if question.specialty == MdtSpecialty.SHARED_CONTEXT:
            raise ValueError("A specialist question cannot target shared_context")
    return result


def _validate_evidence_map(
    result: DiscussionEvidenceMap,
    discussion_input: PulmonologyDiscussionInput,
) -> DiscussionEvidenceMap:
    _resolve_evidence_pointers(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _validate_used_opinions(result.specialist_opinions_used, opinions)
    _validate_mapped_findings(result.mapped_findings, discussion_input.case_input, opinions)
    _validate_clinical_items(result.unresolved_conflicts, discussion_input.case_input, opinions)
    return result


def _validate_state_update(
    result: DiscussionStateUpdate,
    discussion_input: PulmonologyDiscussionInput,
    clinical_rules: dict | None,
) -> DiscussionStateUpdate:
    _resolve_evidence_pointers(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _validate_clinical_state(
        result.updated_state,
        discussion_input.case_input,
        opinions,
        clinical_rules,
    )
    _validate_domain_changes(result.domain_changes, discussion_input.case_input, opinions)
    return result


def _validate_consult_output(
    result: DiscussionConsultOutput,
    discussion_input: PulmonologyDiscussionInput,
) -> DiscussionConsultOutput:
    _resolve_evidence_pointers(result, discussion_input.case_input)
    opinions = {item.opinion_id: item for item in discussion_input.specialist_opinions}
    _validate_chair_answers(result.chair_answers, discussion_input, opinions)
    _validate_clinical_items(result.unresolved_conflicts, discussion_input.case_input, opinions)
    return result


def _validate_clinical_state(
    state: PulmonologyClinicalState,
    case_input: SpecialtyCaseInput,
    opinions: dict,
    clinical_rules: dict | None,
) -> None:
    state.case_id = case_input.case_id
    items: list[object] = [
        state.clinical_phenotype,
        *state.secondary_cause_assessment,
        state.pulmonary_severity,
        *state.respiratory_test_interpretation,
        state.bronchoscopy_assessment,
    ]
    if state.progression_assessment:
        items.extend(state.progression_assessment.components)
    if state.diagnostic_formulation:
        items.extend(
            [
                state.diagnostic_formulation,
                state.diagnostic_formulation.morphologic_pattern,
                *state.diagnostic_formulation.differential_diagnoses,
            ]
        )
    _validate_clinical_items([item for item in items if item is not None], case_input, opinions)
    units = _case_units(case_input)
    for gap in state.missing_data:
        _validate_pointers(gap.related_evidence, units)
    for question in state.specialist_dependencies:
        if question.specialty == MdtSpecialty.SHARED_CONTEXT:
            raise ValueError("A specialist question cannot target shared_context")
        _validate_pointers(question.related_evidence, units)
    for observation in state.reference_observations:
        _validate_pointers(observation.related_evidence, units)
    _validate_ppf_rule(state.progression_assessment, clinical_rules)


def _validate_clinical_items(items, case_input, opinions) -> None:
    units = _case_units(case_input)
    for item in items:
        pointers = list(getattr(item, "supporting_evidence", []))
        pointers.extend(getattr(item, "conflicting_evidence", []))
        _validate_authorized_pointers(
            pointers,
            getattr(item, "specialist_opinion_ids", []),
            units,
            opinions,
        )
        _validate_pointers(getattr(item, "related_evidence", []), units)


def _validate_domain_changes(changes, case_input, opinions) -> None:
    units = _case_units(case_input)
    for change in changes:
        _validate_authorized_pointers(
            change.supporting_evidence,
            change.specialist_opinion_ids,
            units,
            opinions,
        )


def _validate_mapped_findings(findings, case_input, opinions) -> None:
    units = _case_units(case_input)
    for finding in findings:
        if finding.opinion_id not in opinions:
            raise ValueError(f"Unknown mapped opinion_id: {finding.opinion_id}")
        _validate_authorized_pointers(
            finding.evidence,
            [finding.opinion_id],
            units,
            opinions,
        )


def _validate_chair_answers(answers, discussion_input, opinions) -> None:
    expected = [item.question_id for item in discussion_input.chair_questions]
    actual = [item.question_id for item in answers]
    if len(actual) != len(set(actual)):
        raise ValueError("Chair answers contain duplicate question_id values")
    if actual != expected:
        raise ValueError("Chair answers must match every chair question in input order")
    _validate_clinical_items(answers, discussion_input.case_input, opinions)


def _validate_ppf_rule(progression, clinical_rules: dict | None) -> None:
    if not progression or not clinical_rules:
        return
    configured = (clinical_rules.get("ppf") or {}).get("source")
    if configured and progression.rule_source != configured:
        raise ValueError(
            f"PPF rule_source {progression.rule_source!r} does not match configured {configured!r}"
        )


def _require_pulmonology_input(case_input: SpecialtyCaseInput) -> None:
    if case_input.target_specialty != MdtSpecialty.PULMONOLOGY:
        raise ValueError(
            "PulmonologyAgent requires target_specialty=pulmonology, "
            f"got {case_input.target_specialty}"
        )
    if case_input.summary.unit_count < 1:
        raise ValueError("PulmonologyAgent requires at least one graph unit")
    _case_units(case_input)


def _case_units(case_input: SpecialtyCaseInput) -> dict[str, SpecialtyUnitInput]:
    indexed = {}
    for segment in case_input.segments:
        segment_id = segment.segment.segment_id
        for unit in segment.units:
            unit_id = unit.graph_unit.graph_unit_id
            if unit_id in indexed:
                raise ValueError(f"Duplicate graph_unit_id in specialty input: {unit_id}")
            if unit.graph_unit.segment_id != segment_id:
                raise ValueError(
                    f"Specialty input unit {unit_id} is under the wrong segment {segment_id}"
                )
            indexed[unit_id] = unit
    if len(indexed) != case_input.summary.unit_count:
        raise ValueError("Specialty input summary.unit_count does not match its units")
    return indexed


def _validate_specialist_opinions(discussion_input: PulmonologyDiscussionInput) -> None:
    _resolve_evidence_pointers(
        discussion_input.specialist_opinions,
        discussion_input.case_input,
    )
    units = _case_units(discussion_input.case_input)
    opinion_ids = [item.opinion_id for item in discussion_input.specialist_opinions]
    if len(opinion_ids) != len(set(opinion_ids)):
        raise ValueError("Specialist opinions contain duplicate opinion_id values")
    for opinion in discussion_input.specialist_opinions:
        if opinion.specialty == MdtSpecialty.SHARED_CONTEXT:
            raise ValueError("A specialist opinion cannot use shared_context as its specialty")
        for claim in opinion.claims:
            _validate_pointers(claim.evidence, units)
            for pointer in claim.evidence:
                unit = units[pointer.graph_unit_id]
                specialties = unit.graph_unit.mdt_specialty
                if (
                    opinion.specialty not in specialties
                    and MdtSpecialty.SHARED_CONTEXT not in specialties
                ):
                    raise ValueError(
                        f"Opinion {opinion.opinion_id} cites unit {pointer.graph_unit_id} "
                        f"outside {opinion.specialty}'s evidence scope"
                    )


def _validate_used_opinions(used, opinions) -> None:
    unknown = set(used) - set(opinions)
    if unknown:
        raise ValueError(f"Unknown specialist_opinions_used: {sorted(unknown)}")
    if len(used) != len(set(used)):
        raise ValueError("specialist_opinions_used contains duplicates")


def _resolve_evidence_pointers(value: object, case_input: SpecialtyCaseInput) -> None:
    evidence_index: dict[str, tuple[SpecialtyUnitInput, str]] = {}
    for unit in _case_units(case_input).values():
        for block in unit.clinical_propositions.evidence_blocks:
            if block.evidence_id in evidence_index:
                raise ValueError(f"Duplicate evidence_id in specialty input: {block.evidence_id}")
            evidence_index[block.evidence_id] = (unit, block.text)

    for pointer in _iter_evidence_pointers(value):
        if not pointer.evidence_ids:
            raise ValueError("Evidence pointer must include at least one evidence_id")
        if len(pointer.evidence_ids) != len(set(pointer.evidence_ids)):
            raise ValueError("Evidence pointer contains duplicate evidence_ids")
        missing = sorted(set(pointer.evidence_ids) - set(evidence_index))
        if missing:
            raise ValueError(f"Evidence pointer has unknown evidence_ids: {missing}")
        referenced_units = {
            evidence_index[evidence_id][0].graph_unit.graph_unit_id
            for evidence_id in pointer.evidence_ids
        }
        if len(referenced_units) != 1:
            raise ValueError("Evidence pointer evidence_ids must belong to one graph unit")
        unit = evidence_index[pointer.evidence_ids[0]][0]
        selected_ids = set(pointer.evidence_ids)
        blocks = [
            block
            for block in unit.clinical_propositions.evidence_blocks
            if block.evidence_id in selected_ids
        ]
        pointer.evidence_ids = [block.evidence_id for block in blocks]
        pointer.graph_unit_id = unit.graph_unit.graph_unit_id
        pointer.segment_id = unit.graph_unit.segment_id
        pointer.quote = "".join(block.text for block in blocks)
        pointer.node_ids = [
            node.node_id
            for node in unit.local_graph.nodes
            if selected_ids.intersection(node.evidence.evidence_ids)
        ]


def _iter_evidence_pointers(value: object) -> Iterable[EvidencePointer]:
    if isinstance(value, EvidencePointer):
        yield value
    elif isinstance(value, BaseModel):
        for field_name in type(value).model_fields:
            yield from _iter_evidence_pointers(getattr(value, field_name))
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_evidence_pointers(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_evidence_pointers(item)


def _validate_authorized_pointers(
    pointers: Iterable[EvidencePointer],
    specialist_opinion_ids: list[str],
    units: dict[str, SpecialtyUnitInput],
    opinions: dict,
) -> None:
    pointers = list(pointers)
    _validate_pointers(pointers, units)
    unknown = set(specialist_opinion_ids) - set(opinions)
    if unknown:
        raise ValueError(f"Unknown specialist_opinion_ids: {sorted(unknown)}")
    authorized_evidence_ids = {
        evidence_id
        for opinion_id in specialist_opinion_ids
        for claim in opinions[opinion_id].claims
        for pointer in claim.evidence
        for evidence_id in pointer.evidence_ids
    }
    for pointer in pointers:
        unit = units[pointer.graph_unit_id]
        if unit.may_support_diagnostic_claim:
            continue
        if not set(pointer.evidence_ids).issubset(authorized_evidence_ids):
            raise ValueError(
                f"{unit.evidence_role} 证据 {pointer.evidence_ids} 不能直接支持呼吸科诊断性判断；"
                "如无精确引用相同 evidence ID 的正式专科 claim，请将其放入 "
                "related_evidence，用于病例理解、局限性说明或等待专科确认"
            )


def _validate_pointers(
    pointers: Iterable[EvidencePointer],
    units: dict[str, SpecialtyUnitInput],
) -> None:
    for pointer in pointers:
        unit = units.get(pointer.graph_unit_id)
        if unit is None:
            raise ValueError(f"Unknown graph_unit_id in evidence pointer: {pointer.graph_unit_id}")
        if pointer.segment_id != unit.graph_unit.segment_id:
            raise ValueError(
                f"Evidence pointer {pointer.graph_unit_id} has segment_id {pointer.segment_id}; "
                f"expected {unit.graph_unit.segment_id}"
            )
        known_nodes = {node.node_id for node in unit.local_graph.nodes}
        missing_nodes = sorted(set(pointer.node_ids) - known_nodes)
        if missing_nodes:
            raise ValueError(
                f"Evidence pointer {pointer.graph_unit_id} has unknown node_ids: {missing_nodes}"
            )
        known_evidence = {block.evidence_id for block in unit.clinical_propositions.evidence_blocks}
        missing_evidence = sorted(set(pointer.evidence_ids) - known_evidence)
        if missing_evidence:
            raise ValueError(
                f"Evidence pointer {pointer.graph_unit_id} has unknown evidence_ids: "
                f"{missing_evidence}"
            )


def _json(value: object) -> str:
    def serializable(item):
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, dict):
            return {key: serializable(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            return [serializable(value) for value in item]
        return item

    return json.dumps(serializable(value), ensure_ascii=False, indent=2)


def _combined_trace(*stages) -> dict:
    return {
        "schema_version": "pulmonology.v2",
        "stages": [{"stage": name, **trace} for name, trace in stages],
    }
