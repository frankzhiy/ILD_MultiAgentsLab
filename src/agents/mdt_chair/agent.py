"""Cross-specialty integration of the four formal specialty outputs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable

from src.agents.mdt_chair.models import (
    CaseEvidenceCitation,
    ChairEvidenceBundle,
    CitedChairStatement,
    CrossSpecialtyConflict,
    MDTChairIntegration,
    SpecialtySourceCitation,
)
from src.guidelines.models import GuidelineEvidencePointer
from src.llm.base import LLMClient
from src.llm.prompting import prompt_json, prompt_schema_json
from src.llm.structured import StructuredLLMGenerator
from src.utils.config import load_text, load_yaml, render_template


SYSTEM_PROMPT = (
    "你是以呼吸科为主要背景的 ILD MDT 主持人。你只整合四个专科已经形成的正式结论、"
    "合并专科已经提出的原生问题，并汇总已有证据需求；不创造问题，不联系或重新运行专科 Agent，"
    "识别并如实描述未解决的跨专科冲突，但不裁决冲突，不输出最终 MDT 诊断或治疗方案。"
    "所有面向人的文本使用简体中文，只返回符合 schema 的 JSON。"
)

SPECIALTIES = (
    "pulmonology",
    "thoracic_radiology",
    "rheumatology",
    "pathology",
)
EVIDENCE_ROLES = ("supporting", "weakening", "discriminating", "background")


@dataclass
class ChairPromptBundle:
    case_id: str
    prompt_input: dict[str, Any]
    source_registry: dict[str, SpecialtySourceCitation]
    evidence_registry: dict[str, CaseEvidenceCitation]
    source_evidence: dict[str, dict[str, list[str]]]
    source_guidelines: dict[str, list[GuidelineEvidencePointer]]
    source_metadata: dict[str, dict[str, Any]]


class _Registry:
    def __init__(self, semantic_evidence: dict[str, dict[str, Any]] | None = None) -> None:
        self.sources: dict[str, SpecialtySourceCitation] = {}
        self.evidence: dict[str, CaseEvidenceCitation] = {}
        self.source_evidence: dict[str, dict[str, list[str]]] = {}
        self.source_guidelines: dict[str, list[GuidelineEvidencePointer]] = {}
        self.source_metadata: dict[str, dict[str, Any]] = {}
        self._source_keys: dict[tuple[str, str, str, str], str] = {}
        self._evidence_keys: dict[str, str] = {}
        self.semantic_evidence = semantic_evidence or {}
        self.evidence_to_unit = {
            evidence_id: unit_id
            for unit_id, unit in self.semantic_evidence.items()
            for evidence_id in unit.get("evidence_blocks", {})
        }

    def source(
        self,
        specialty: str,
        source_type: str,
        source_path: str,
        quote: str,
        *,
        evidence: dict[str, list[str]] | None = None,
        guidelines: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        key = (specialty, source_type, source_path, quote)
        if key not in self._source_keys:
            ref = f"S{len(self.sources) + 1:03d}"
            self._source_keys[key] = ref
            self.sources[ref] = SpecialtySourceCitation(
                source_ref=ref,
                specialty=specialty,
                source_type=source_type,
                source_path=source_path,
                quote=quote,
            )
            self.source_evidence[ref] = {
                role: _ordered_unique((evidence or {}).get(role, []))
                for role in EVIDENCE_ROLES
            }
            self.source_guidelines[ref] = [
                GuidelineEvidencePointer.model_validate(item)
                for item in (guidelines or [])
            ]
            self.source_metadata[ref] = metadata or {}
        return self._source_keys[key]

    def pointer(self, pointer: dict[str, Any]) -> str:
        pointer = self._canonical_pointer(pointer)
        quote = str(pointer.get("quote") or "").strip()
        if not quote:
            quote = "\n".join(
                str(item.get("quote") or "").strip()
                for item in pointer.get("resolved_quotes") or []
                if str(item.get("quote") or "").strip()
            )
        value = {
            "segment_id": str(pointer.get("segment_id") or ""),
            "graph_unit_id": str(pointer.get("graph_unit_id") or ""),
            "evidence_ids": list(pointer.get("evidence_ids") or []),
            "proposition_ids": list(pointer.get("proposition_ids") or []),
            "node_ids": list(pointer.get("node_ids") or []),
            "quote": quote,
        }
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key not in self._evidence_keys:
            ref = f"E{len(self.evidence) + 1:03d}"
            self._evidence_keys[key] = ref
            self.evidence[ref] = CaseEvidenceCitation(evidence_ref=ref, **value)
        return self._evidence_keys[key]

    def _canonical_pointer(self, pointer: dict[str, Any]) -> dict[str, Any]:
        evidence_ids = list(pointer.get("evidence_ids") or [])
        unit_id = str(pointer.get("graph_unit_id") or "")
        if not unit_id and evidence_ids:
            unit_id = self.evidence_to_unit.get(evidence_ids[0], "")
        unit = self.semantic_evidence.get(unit_id)
        if not unit:
            return pointer

        proposition_ids = list(pointer.get("proposition_ids") or [])
        propositions = unit.get("propositions", {})
        if proposition_ids:
            evidence_ids = _ordered_unique(
                evidence_id
                for proposition_id in proposition_ids
                for evidence_id in propositions.get(proposition_id, {}).get("evidence_ids", [])
            )
            quote = "\n".join(
                propositions.get(proposition_id, {}).get("quote", "")
                for proposition_id in proposition_ids
                if propositions.get(proposition_id, {}).get("quote")
            )
            node_ids = [
                f"{unit_id}::{proposition_id}"
                for proposition_id in proposition_ids
                if f"{unit_id}::{proposition_id}" in unit.get("node_ids", set())
            ]
        else:
            quote = "".join(
                unit.get("evidence_blocks", {}).get(evidence_id, "")
                for evidence_id in evidence_ids
            )
            selected = set(evidence_ids)
            node_ids = [
                node_id
                for node_id, node_evidence in unit.get("node_evidence", {}).items()
                if selected.intersection(node_evidence)
            ]
        return {
            **pointer,
            "segment_id": unit.get("segment_id", ""),
            "graph_unit_id": unit_id,
            "evidence_ids": evidence_ids,
            "proposition_ids": proposition_ids,
            "node_ids": node_ids,
            "quote": quote or pointer.get("quote", ""),
        }


def build_semantic_evidence_catalog(
    clinical_propositions: dict[str, Any],
    local_graphs: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    graphs = {
        unit["graph_unit_id"]: unit
        for segment in local_graphs.get("segments") or []
        for unit in segment.get("units") or []
    }
    catalog = {}
    for segment in clinical_propositions.get("segments") or []:
        for unit in segment.get("units") or []:
            unit_id = unit["graph_unit_id"]
            graph = graphs.get(unit_id, {})
            catalog[unit_id] = {
                "segment_id": graph.get("segment_id", segment.get("segment_id", "")),
                "evidence_blocks": {
                    item["evidence_id"]: item.get("text", "")
                    for item in unit.get("evidence_blocks") or []
                },
                "propositions": {
                    item["proposition_id"]: {
                        "evidence_ids": list((item.get("evidence") or {}).get("evidence_ids") or []),
                        "quote": (item.get("evidence") or {}).get("quote", ""),
                    }
                    for item in unit.get("propositions") or []
                },
                "node_ids": {item["node_id"] for item in graph.get("nodes") or []},
                "node_evidence": {
                    item["node_id"]: set((item.get("evidence") or {}).get("evidence_ids") or [])
                    for item in graph.get("nodes") or []
                },
            }
    return catalog


def build_chair_prompt_bundle(
    case_id: str,
    specialty_outputs: dict[str, dict[str, Any]],
    input_summaries: dict[str, dict[str, Any]] | None = None,
    semantic_evidence: dict[str, dict[str, Any]] | None = None,
) -> ChairPromptBundle:
    """Project only each specialty's formal professional_conclusions section."""
    del input_summaries  # retained only for compatibility with the former caller API
    missing = sorted(set(SPECIALTIES) - set(specialty_outputs))
    if missing:
        raise ValueError(f"Missing specialty outputs: {missing}")
    registry = _Registry(semantic_evidence)
    compact = [
        _compact_specialty(specialty, specialty_outputs[specialty], registry)
        for specialty in SPECIALTIES
    ]
    prompt_input = {
        "case_id": case_id,
        "specialties": compact,
        "evidence_registry": [
            {
                "evidence_ref": item.evidence_ref,
                "segment_id": item.segment_id,
                "graph_unit_id": item.graph_unit_id,
                "evidence_ids": item.evidence_ids,
                "proposition_ids": item.proposition_ids,
                "quote": item.quote,
            }
            for item in registry.evidence.values()
        ],
    }
    return ChairPromptBundle(
        case_id,
        prompt_input,
        registry.sources,
        registry.evidence,
        registry.source_evidence,
        registry.source_guidelines,
        registry.source_metadata,
    )


def _compact_specialty(
    specialty: str,
    output: dict[str, Any],
    registry: _Registry,
) -> dict[str, Any]:
    professional = output.get("professional_conclusions")
    if not isinstance(professional, dict):
        raise ValueError(f"{specialty} output is missing professional_conclusions")

    conclusions = []
    for index, item in enumerate(professional.get("conclusions") or []):
        path = f"professional_conclusions.conclusions[{index}]"
        statement = str(item.get("statement") or "").strip()
        medical_basis = str(item.get("medical_basis") or "").strip()
        decision_impact = str(item.get("decision_impact") or "").strip()
        quote = "\n".join(
            (
                f"结论：{statement}",
                f"医学依据：{medical_basis}",
                f"决策影响：{decision_impact}",
            )
        )
        evidence = _formal_evidence_refs(item.get("evidence") or {}, registry)
        source_ref = registry.source(
            specialty,
            "native_conclusion",
            path,
            quote,
            evidence=evidence,
            guidelines=list(item.get("guideline_evidence") or []),
            metadata={
                "original_statement": statement,
                "limitations": list(item.get("limitations") or []),
            },
        )
        conclusions.append(
            {
                "source_ref": source_ref,
                "conclusion_id": item.get("conclusion_id"),
                "role": item.get("role"),
                "conclusion_type": item.get("conclusion_type"),
                "statement": statement,
                "status": item.get("status"),
                "medical_basis": medical_basis,
                "decision_impact": decision_impact,
                "evidence": evidence,
                "guideline_evidence": list(item.get("guideline_evidence") or []),
                "limitations": list(item.get("limitations") or []),
            }
        )

    questions = []
    for index, item in enumerate(professional.get("interspecialty_questions") or []):
        path = f"professional_conclusions.interspecialty_questions[{index}]"
        question = str(item.get("question") or "").strip()
        evidence = _related_evidence_refs(item.get("related_evidence") or [], registry)
        source_ref = registry.source(
            specialty,
            "native_question",
            path,
            question,
            evidence=evidence,
            metadata={"target_specialty": item.get("target_specialty")},
        )
        questions.append({"source_ref": source_ref, **item, "related_evidence": evidence["background"]})

    evidence_needs = []
    for index, item in enumerate(professional.get("evidence_gaps") or []):
        path = f"professional_conclusions.evidence_gaps[{index}]"
        required = str(item.get("missing_information") or "").strip()
        evidence = _related_evidence_refs(item.get("related_evidence") or [], registry)
        source_ref = registry.source(
            specialty,
            "evidence_gap",
            path,
            required,
            evidence=evidence,
        )
        evidence_needs.append(
            {"source_ref": source_ref, **item, "related_evidence": evidence["background"]}
        )

    return {
        "specialty": specialty,
        "specialty_question": professional.get("specialty_question"),
        "assessability": professional.get("assessability"),
        "boundaries": list(professional.get("boundaries") or []),
        "native_conclusions": conclusions,
        "native_questions": questions,
        "evidence_needs": evidence_needs,
    }


def _formal_evidence_refs(
    evidence: dict[str, Any], registry: _Registry
) -> dict[str, list[str]]:
    return {
        role: [
            registry.pointer(pointer)
            for pointer in evidence.get(role) or []
            if isinstance(pointer, dict)
        ]
        for role in EVIDENCE_ROLES
    }


def _related_evidence_refs(
    pointers: list[dict[str, Any]], registry: _Registry
) -> dict[str, list[str]]:
    return {
        role: (
            [registry.pointer(pointer) for pointer in pointers if isinstance(pointer, dict)]
            if role == "background"
            else []
        )
        for role in EVIDENCE_ROLES
    }


def _ordered_unique(values: Iterable[Any]) -> list[Any]:
    return list(dict.fromkeys(values))


class MDTChairAgent:
    def __init__(
        self,
        llm: LLMClient,
        *,
        prompt_path: str | Path,
        temperature: float = 0.0,
        max_tokens: int = 12000,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.0,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.prompt = load_text(prompt_path)
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
    ) -> "MDTChairAgent":
        config = load_yaml(config_path)
        if "prompt" not in config:
            raise ValueError("MDT chair config is missing prompt")
        return cls(
            llm,
            prompt_path=config["prompt"],
            temperature=float(config.get("temperature", 0.0)),
            max_tokens=int(config.get("max_tokens", 12000)),
            max_attempts=int(config.get("max_attempts", 2)),
            retry_backoff_seconds=float(config.get("retry_backoff_seconds", 2)),
            event_callback=event_callback,
        )

    def integrate(self, bundle: ChairPromptBundle) -> tuple[MDTChairIntegration, dict]:
        compact_json = prompt_json(bundle.prompt_input)
        output_schema = (
            "由 API 的严格 JSON Schema response_format 提供。"
            if self.generator.response_format_mode == "json_schema"
            else prompt_schema_json(MDTChairIntegration)
        )
        prompt = render_template(
            self.prompt,
            {"chair_input": compact_json, "output_schema": output_schema},
        )
        result, trace = self.generator.generate(
            schema_model=MDTChairIntegration,
            schema_name="mdt_chair_integration",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            extra_validation=lambda value: resolve_chair_references(value, bundle),
        )
        trace["prompt_components"] = {
            "total_chars": len(prompt),
            "chair_input_chars": len(compact_json),
            "output_schema_chars": len(output_schema),
            "source_reference_count": len(bundle.source_registry),
            "evidence_reference_count": len(bundle.evidence_registry),
        }
        return result, trace

    def synthesize(self, bundle: ChairPromptBundle) -> tuple[MDTChairIntegration, dict]:
        """Compatibility alias for callers using the v1 method name."""
        return self.integrate(bundle)


def resolve_chair_references(
    result: MDTChairIntegration,
    bundle: ChairPromptBundle,
) -> MDTChairIntegration:
    result.case_id = bundle.case_id
    _validate_unique_ids(result)
    for conclusion in result.integrated_conclusions:
        _resolve_cited(conclusion, bundle, "native_conclusion")
        conclusion.specialties = _ordered_unique(
            citation.specialty for citation in conclusion.source_citations
        )
        conclusion.limitations = _ordered_unique(
            limitation
            for ref in conclusion.source_refs
            for limitation in bundle.source_metadata[ref].get("limitations", [])
        )

    retained_questions = []
    for question in result.questions:
        unknown = set(question.source_refs) - set(bundle.source_registry)
        if unknown:
            raise ValueError(f"Unknown specialty source refs: {sorted(unknown)}")
        question.source_refs = _ordered_unique(
            ref
            for ref in question.source_refs
            if bundle.source_registry[ref].source_type == "native_question"
        )
        if not question.source_refs:
            continue
        if len(question.source_refs) == 1:
            question.question = bundle.source_registry[question.source_refs[0]].quote
        retained_questions.append(question)
    result.questions = retained_questions

    for question in result.questions:
        _resolve_cited(question, bundle, "native_question")
        question.raised_by = _ordered_unique(
            citation.specialty for citation in question.source_citations
        )
        question.target_specialties = _ordered_unique(
            bundle.source_metadata[ref].get("target_specialty")
            for ref in question.source_refs
            if bundle.source_metadata[ref].get("target_specialty") in SPECIALTIES
        )
        for answer in question.answers:
            _resolve_cited(answer, bundle, "native_conclusion")
            if not any(
                citation.specialty == answer.specialty
                for citation in answer.source_citations
            ):
                raise ValueError(
                    f"Question answer by {answer.specialty} must cite that specialty's native conclusion"
                )
        answered = {answer.specialty for answer in question.answers}
        targets = set(question.target_specialties)
        if question.status == "disputed" and len(answered) >= 2:
            continue
        question.status = (
            "answered"
            if targets and targets.issubset(answered)
            else "partially_answered"
            if answered
            else "unanswered"
        )

    retained_needs = []
    for need in result.evidence_needs:
        unknown = set(need.source_refs) - set(bundle.source_registry)
        if unknown:
            raise ValueError(f"Unknown specialty source refs: {sorted(unknown)}")
        need.source_refs = _ordered_unique(
            ref
            for ref in need.source_refs
            if bundle.source_registry[ref].source_type
            in {"evidence_gap", "native_conclusion"}
        )
        gap_refs = [
            ref
            for ref in need.source_refs
            if bundle.source_registry[ref].source_type == "evidence_gap"
        ]
        if not gap_refs:
            continue
        if len(gap_refs) == 1:
            need.required_information = bundle.source_registry[gap_refs[0]].quote
        retained_needs.append(need)
    result.evidence_needs = retained_needs

    for need in result.evidence_needs:
        _resolve_cited(
            need,
            bundle,
            {"evidence_gap", "native_conclusion"},
            required_source_type="evidence_gap",
        )
        need.raised_by = _ordered_unique(
            citation.specialty
            for citation in need.source_citations
            if citation.source_type == "evidence_gap"
        )
        need.provided_by = _ordered_unique(
            citation.specialty
            for citation in need.source_citations
            if citation.source_type == "native_conclusion"
        )
    _resolve_conflicts(result.conflicts, result, bundle)
    return result


def _resolve_cited(
    statement: CitedChairStatement,
    bundle: ChairPromptBundle,
    expected_source_type: str | set[str],
    *,
    required_source_type: str | None = None,
) -> None:
    statement.source_refs = _ordered_unique(statement.source_refs)
    unknown = set(statement.source_refs) - set(bundle.source_registry)
    if unknown:
        raise ValueError(f"Unknown specialty source refs: {sorted(unknown)}")
    allowed = (
        {expected_source_type}
        if isinstance(expected_source_type, str)
        else expected_source_type
    )
    wrong_type = [
        ref
        for ref in statement.source_refs
        if bundle.source_registry[ref].source_type not in allowed
    ]
    if wrong_type:
        raise ValueError(
            f"Expected {expected_source_type} source refs; got incompatible refs: {wrong_type}"
        )
    if required_source_type and not any(
        bundle.source_registry[ref].source_type == required_source_type
        for ref in statement.source_refs
    ):
        raise ValueError(f"At least one {required_source_type} source ref is required")
    statement.source_citations = [bundle.source_registry[ref] for ref in statement.source_refs]
    evidence = {}
    for role in EVIDENCE_ROLES:
        refs = _ordered_unique(
            evidence_ref
            for source_ref in statement.source_refs
            for evidence_ref in bundle.source_evidence[source_ref][role]
        )
        evidence[role] = [bundle.evidence_registry[ref] for ref in refs]
    statement.evidence = ChairEvidenceBundle(**evidence)
    guidelines = [
        pointer
        for source_ref in statement.source_refs
        for pointer in bundle.source_guidelines[source_ref]
    ]
    statement.guideline_evidence = list(
        {
            json.dumps(pointer.model_dump(mode="json"), ensure_ascii=False, sort_keys=True): pointer
            for pointer in guidelines
        }.values()
    )


def _validate_unique_ids(result: MDTChairIntegration) -> None:
    for label, values in (
        ("conclusion_id", [item.conclusion_id for item in result.integrated_conclusions]),
        ("conflict_id", [item.conflict_id for item in result.conflicts]),
        ("question_id", [item.question_id for item in result.questions]),
        ("need_id", [item.need_id for item in result.evidence_needs]),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"{label} values must be unique")


def _resolve_conflicts(
    conflicts: list[CrossSpecialtyConflict],
    result: MDTChairIntegration,
    bundle: ChairPromptBundle,
) -> None:
    question_ids = {item.question_id for item in result.questions}
    need_ids = {item.need_id for item in result.evidence_needs}
    expected_status = {
        (False, False): "unresolved",
        (True, False): "pending_clarification",
        (False, True): "pending_evidence",
        (True, True): "pending_clarification_and_evidence",
    }
    for conflict in conflicts:
        specialties = []
        stances = set()
        for position in conflict.positions:
            _resolve_cited(position, bundle, "native_conclusion")
            cited_specialties = {item.specialty for item in position.source_citations}
            if cited_specialties != {position.specialty}:
                raise ValueError(
                    "Each conflict position must cite only native conclusions from its specialty"
            )
            specialties.append(position.specialty)
            stances.add(position.stance)
        if len(set(specialties)) < 2:
            raise ValueError("A cross-specialty conflict requires positions from at least two specialties")
        if stances != {"affirms", "denies"}:
            raise ValueError(
                "A cross-specialty conflict requires both an affirming and a denying position"
            )
        conflict.specialties = _ordered_unique(specialties)
        conflict.related_question_ids = _ordered_unique(conflict.related_question_ids)
        conflict.related_evidence_need_ids = _ordered_unique(conflict.related_evidence_need_ids)
        unknown_questions = set(conflict.related_question_ids) - question_ids
        if unknown_questions:
            raise ValueError(f"Unknown related question IDs: {sorted(unknown_questions)}")
        unknown_needs = set(conflict.related_evidence_need_ids) - need_ids
        if unknown_needs:
            raise ValueError(f"Unknown related evidence need IDs: {sorted(unknown_needs)}")
        status = expected_status[
            (bool(conflict.related_question_ids), bool(conflict.related_evidence_need_ids))
        ]
        if conflict.status != status:
            raise ValueError(
                f"Conflict {conflict.conflict_id} status must be {status} for its linked resolution items"
            )
