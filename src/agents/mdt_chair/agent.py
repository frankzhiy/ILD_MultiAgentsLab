"""Cross-specialty integration of the four formal specialty outputs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable

from src.agents.mdt_chair.models import (
    ChairSemanticLedger,
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
    normalization_events: list[dict[str, Any]] = field(default_factory=list)


def _source_ref_schema_constraints(
    bundle: ChairPromptBundle,
    *,
    semantic_ledger: ChairSemanticLedger | None = None,
) -> dict[str, dict[str, set[str]]]:
    refs = {
        source_type: {
            ref
            for ref, source in bundle.source_registry.items()
            if source.source_type == source_type
        }
        for source_type in ("native_conclusion", "native_question", "evidence_gap")
    }
    conclusions = refs["native_conclusion"]
    questions = refs["native_question"]
    needs = questions | refs["evidence_gap"]
    if semantic_ledger is None:
        return {
            "LedgerAtomicClaim": {"source_ref": conclusions},
            "LedgerQuestionRoute": {"source_refs": questions},
            "LedgerAnswerLink": {"source_refs": conclusions},
            "LedgerEvidenceNeedGroup": {
                "source_refs": needs,
                "coverage_source_refs": conclusions,
            },
        }
    claims = {
        disposition: {
            claim.source_ref
            for group in semantic_ledger.claim_groups
            if group.disposition == disposition
            for claim in group.claims
        }
        for disposition in ("integrated", "boundary", "conflict")
    }
    question_routes = [
        route
        for route in semantic_ledger.question_routes
        if route.route in {"question", "mixed"}
    ]
    questions = {
        ref
        for route in question_routes
        for ref in route.source_refs
    }
    answers = {
        ref
        for route in question_routes
        for answer in route.answer_links
        for ref in answer.source_refs
    }
    needs = {
        ref
        for group in semantic_ledger.evidence_need_groups
        for ref in group.source_refs
    }
    coverage = {
        ref
        for group in semantic_ledger.evidence_need_groups
        for ref in group.coverage_source_refs
    }
    return {
        "IntegratedConclusion": {"source_refs": claims["integrated"]},
        "AssessmentBoundary": {
            "source_refs": claims["boundary"] | questions,
            "related_evidence_need_source_refs": needs,
        },
        "ConflictPosition": {"source_refs": claims["conflict"]},
        "CrossSpecialtyConflict": {
            "related_question_source_refs": questions,
            "related_evidence_need_source_refs": needs,
        },
        "IntegratedQuestion": {
            "source_refs": questions,
            "related_evidence_need_source_refs": needs,
        },
        "QuestionAnswer": {"source_refs": answers},
        "EvidenceNeed": {"source_refs": needs | coverage},
    }


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
            ref = _canonical_evidence_ref(value)
            self._evidence_keys[key] = ref
            existing = self.evidence.get(ref)
            if existing is None:
                self.evidence[ref] = CaseEvidenceCitation(evidence_ref=ref, **value)
            else:
                self.evidence[ref] = CaseEvidenceCitation(
                    evidence_ref=ref,
                    segment_id=existing.segment_id or value["segment_id"],
                    graph_unit_id=existing.graph_unit_id or value["graph_unit_id"],
                    evidence_ids=_ordered_unique([*existing.evidence_ids, *value["evidence_ids"]]),
                    proposition_ids=_ordered_unique([
                        *existing.proposition_ids,
                        *value["proposition_ids"],
                    ]),
                    node_ids=_ordered_unique([*existing.node_ids, *value["node_ids"]]),
                    quote="\n".join(_ordered_unique([
                        item
                        for item in [existing.quote, value["quote"]]
                        if item
                    ])),
                )
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
        local_proposition_ids = [
            proposition_id.rsplit("::", 1)[-1]
            for proposition_id in proposition_ids
        ]
        propositions = unit.get("propositions", {})
        if proposition_ids:
            evidence_ids = _ordered_unique(
                evidence_id
                for proposition_id in local_proposition_ids
                for evidence_id in propositions.get(proposition_id, {}).get("evidence_ids", [])
            )
            quote = "\n".join(
                propositions.get(proposition_id, {}).get("quote", "")
                for proposition_id in local_proposition_ids
                if propositions.get(proposition_id, {}).get("quote")
            )
            proposition_ids = [
                f"{unit_id}::{proposition_id}"
                for proposition_id in local_proposition_ids
            ]
            node_ids = [
                proposition_id
                for proposition_id in proposition_ids
                if proposition_id in unit.get("node_ids", set())
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


def _canonical_evidence_ref(pointer: dict[str, Any]) -> str:
    proposition_ids = list(pointer.get("proposition_ids") or [])
    if len(proposition_ids) == 1:
        return proposition_ids[0]
    evidence_ids = list(pointer.get("evidence_ids") or [])
    if len(evidence_ids) == 1:
        return evidence_ids[0]
    graph_unit_id = str(pointer.get("graph_unit_id") or "")
    if graph_unit_id:
        return graph_unit_id
    raise ValueError("A case evidence pointer must contain a semantic_graphing identifier")


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
                "conclusion_id": item.get("conclusion_id"),
                "original_statement": statement,
                "limitations": list(item.get("limitations") or []),
            },
        )
        conclusions.append(
            {
                "source_ref": source_ref,
                "source_type": "native_conclusion",
                "conclusion_id": item.get("conclusion_id"),
                "role": item.get("role"),
                "conclusion_type": item.get("conclusion_type"),
                "statement": statement,
                "status": item.get("status"),
                "medical_basis": medical_basis,
                "decision_impact": decision_impact,
                "evidence": evidence,
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
            metadata={
                "target_specialty": item.get("target_specialty"),
                "why_it_matters": item.get("why_it_matters"),
                "decision_unlocked": item.get("decision_unlocked"),
            },
        )
        questions.append(
            {
                **item,
                "source_ref": source_ref,
                "source_type": "native_question",
                "related_evidence": evidence["background"],
            }
        )

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
            metadata={
                "available_information": item.get("available_information"),
                "why_it_matters": item.get("why_it_matters"),
                "decision_unlocked": item.get("decision_unlocked"),
            },
        )
        evidence_needs.append(
            {
                **item,
                "source_ref": source_ref,
                "source_type": "evidence_gap",
                "related_evidence": evidence["background"],
            }
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
        ledger_prompt_path: str | Path,
        prompt_path: str | Path,
        temperature: float = 0.0,
        max_tokens: int = 12000,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.0,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.ledger_prompt = load_text(ledger_prompt_path)
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
        if "ledger_prompt" not in config or "prompt" not in config:
            raise ValueError("MDT chair config is missing ledger_prompt or prompt")
        return cls(
            llm,
            ledger_prompt_path=config["ledger_prompt"],
            prompt_path=config["prompt"],
            temperature=float(config.get("temperature", 0.0)),
            max_tokens=int(config.get("max_tokens", 12000)),
            max_attempts=int(config.get("max_attempts", 2)),
            retry_backoff_seconds=float(config.get("retry_backoff_seconds", 2)),
            event_callback=event_callback,
        )

    def integrate(
        self,
        bundle: ChairPromptBundle,
        *,
        discussion_previous: MDTChairIntegration | None = None,
        discussion_responses: list[Any] | None = None,
    ) -> tuple[MDTChairIntegration, dict]:
        compact_json = prompt_json(bundle.prompt_input)
        ledger_schema = (
            "由 API 的严格 JSON Schema response_format 提供。"
            if self.generator.response_format_mode == "json_schema"
            else prompt_schema_json(ChairSemanticLedger)
        )
        ledger_prompt = render_template(
            self.ledger_prompt,
            {"chair_input": compact_json, "output_schema": ledger_schema},
        )
        ledger, ledger_trace = self.generator.generate(
            schema_model=ChairSemanticLedger,
            schema_name="mdt_chair_semantic_ledger",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=ledger_prompt,
            extra_validation=lambda value: resolve_semantic_ledger(value, bundle),
            string_field_constraints=_source_ref_schema_constraints(bundle),
        )

        ledger_json = prompt_json(ledger.model_dump(mode="json"))
        output_schema = (
            "由 API 的严格 JSON Schema response_format 提供。"
            if self.generator.response_format_mode == "json_schema"
            else prompt_schema_json(MDTChairIntegration)
        )
        synthesis_prompt = render_template(
            self.prompt,
            {
                "chair_input": compact_json,
                "topic_ledger": ledger_json,
                "output_schema": output_schema,
            },
        )
        def resolve(value: MDTChairIntegration) -> MDTChairIntegration:
            if discussion_previous is not None:
                from src.agents.mdt_discussion.integration import (
                    reconcile_discussion_references,
                )

                reconcile_discussion_references(
                    value,
                    discussion_previous,
                    discussion_responses or [],
                    bundle,
                )
            return resolve_chair_references(
                value,
                bundle,
                None if discussion_previous is not None else ledger,
            )

        result, synthesis_trace = self.generator.generate(
            schema_model=MDTChairIntegration,
            schema_name="mdt_chair_integration",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=synthesis_prompt,
            extra_validation=resolve,
            string_field_constraints=_source_ref_schema_constraints(
                bundle, semantic_ledger=ledger
            ),
        )
        trace = {
            "semantic_ledger": ledger.model_dump(mode="json"),
            "ledger_generation": ledger_trace,
            "integration_generation": synthesis_trace,
            "semantic_ledger_normalizations": bundle.normalization_events,
        }
        trace["prompt_components"] = {
            "total_chars": len(ledger_prompt) + len(synthesis_prompt),
            "ledger_prompt_chars": len(ledger_prompt),
            "integration_prompt_chars": len(synthesis_prompt),
            "chair_input_chars": len(compact_json),
            "topic_ledger_chars": len(ledger_json),
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
    ledger: ChairSemanticLedger | None = None,
) -> MDTChairIntegration:
    """Backfill deterministic IDs and provenance without judging medical semantics."""

    result.case_id = bundle.case_id
    result.schema_version = "mdt_chair.v5"
    for index, conclusion in enumerate(result.integrated_conclusions, 1):
        conclusion.conclusion_id = f"IC{index:03d}"
        _resolve_cited(
            conclusion,
            bundle,
            "native_conclusion",
            context=f"integrated_conclusions[{index - 1}].source_refs",
        )
        conclusion.supporting_specialties = _ordered_unique(
            citation.specialty for citation in conclusion.source_citations
        )
        conclusion.limitations = _ordered_unique(
            limitation
            for ref in conclusion.source_refs
            for limitation in bundle.source_metadata[ref].get("limitations", [])
        )

    for index, boundary in enumerate(result.assessment_boundaries, 1):
        boundary.boundary_id = f"B{index:03d}"
        _resolve_cited(
            boundary,
            bundle,
            {"native_conclusion", "native_question"},
            context=f"assessment_boundaries[{index - 1}].source_refs",
        )
        _require_refs(
            boundary.related_evidence_need_source_refs,
            bundle,
            {"native_question", "evidence_gap"},
            context=(
                f"assessment_boundaries[{index - 1}]"
                ".related_evidence_need_source_refs"
            ),
        )
        boundary.specialties = _ordered_unique(
            citation.specialty for citation in boundary.source_citations
        )

    for index, need in enumerate(result.evidence_needs, 1):
        need.need_id = f"EN{index:03d}"
        _resolve_cited(
            need,
            bundle,
            {"native_question", "evidence_gap", "native_conclusion"},
            context=f"evidence_needs[{index - 1}].source_refs",
        )
        need.raised_by = _ordered_unique(
            citation.specialty
            for citation in need.source_citations
            if citation.source_type in {"native_question", "evidence_gap"}
        )
        need.provided_by = _ordered_unique(
            citation.specialty
            for citation in need.source_citations
            if citation.source_type == "native_conclusion"
        )

    for index, question in enumerate(result.questions, 1):
        question.question_id = f"Q{index:03d}"
        _resolve_cited(
            question,
            bundle,
            "native_question",
            context=f"questions[{index - 1}].source_refs",
        )
        _require_refs(
            question.related_evidence_need_source_refs,
            bundle,
            {"native_question", "evidence_gap"},
            context=f"questions[{index - 1}].related_evidence_need_source_refs",
        )
        question.raised_by = _ordered_unique(
            citation.specialty for citation in question.source_citations
        )
        question.target_specialties = _ordered_unique(
            bundle.source_metadata[ref].get("target_specialty")
            for ref in question.source_refs
            if bundle.source_metadata[ref].get("target_specialty") in SPECIALTIES
        )
        for answer_index, answer in enumerate(question.answers):
            _resolve_cited(
                answer,
                bundle,
                "native_conclusion",
                context=(
                    f"questions[{index - 1}].answers[{answer_index}].source_refs"
                ),
            )
            if answer.source_citations:
                answer.specialty = answer.source_citations[0].specialty
        question.responded_by = _ordered_unique(
            answer.specialty for answer in question.answers
        )
        question.awaiting_specialties = [
            specialty
            for specialty in question.target_specialties
            if specialty not in question.responded_by
        ]
        question.response_status = (
            "all_responded"
            if question.target_specialties and not question.awaiting_specialties
            else "partially_responded"
            if question.responded_by
            else "none_responded"
        )

    _link_output_items(result)
    _resolve_conflicts(result.conflicts, result, bundle)
    if ledger is not None:
        _validate_output_refs_against_ledger(result, ledger)
    return result


def resolve_semantic_ledger(
    ledger: ChairSemanticLedger,
    bundle: ChairPromptBundle,
) -> ChairSemanticLedger:
    """Resolve ledger IDs and check only that selected source IDs exist."""

    for topic_index, group in enumerate(ledger.claim_groups, 1):
        group.topic_id = f"T{topic_index:03d}"
        for claim_index, claim in enumerate(group.claims, 1):
            claim.claim_id = f"{group.topic_id}-A{claim_index:03d}"
            _require_refs(
                [claim.source_ref],
                bundle,
                {"native_conclusion"},
                context=f"claim_groups[{topic_index - 1}].claims[{claim_index - 1}].source_ref",
            )
    for index, route in enumerate(ledger.question_routes, 1):
        route.route_id = f"R{index:03d}"
        _require_refs(
            route.source_refs,
            bundle,
            {"native_question"},
            context=f"question_routes[{index - 1}].source_refs",
        )
        route.target_specialties = _ordered_unique(
            bundle.source_metadata[ref].get("target_specialty")
            for ref in route.source_refs
            if bundle.source_metadata[ref].get("target_specialty") in SPECIALTIES
        )
        answer_links = []
        for answer_index, answer in enumerate(route.answer_links):
            answer.source_refs = _drop_incompatible_known_refs(
                answer.source_refs,
                bundle,
                {"native_conclusion"},
                context=(
                    f"question_routes[{index - 1}].answer_links[{answer_index}].source_refs"
                ),
            )
            if not answer.source_refs:
                continue
            _require_refs(
                answer.source_refs,
                bundle,
                {"native_conclusion"},
                context=(
                    f"question_routes[{index - 1}].answer_links[{answer_index}].source_refs"
                ),
            )
            answer.specialty = bundle.source_registry[answer.source_refs[0]].specialty
            answer_links.append(answer)
        route.answer_links = answer_links
    for index, group in enumerate(ledger.evidence_need_groups, 1):
        group.group_id = f"NG{index:03d}"
        _require_refs(
            group.source_refs,
            bundle,
            {"native_question", "evidence_gap"},
            context=f"evidence_need_groups[{index - 1}].source_refs",
        )
        group.coverage_source_refs = _drop_incompatible_known_refs(
            group.coverage_source_refs,
            bundle,
            {"native_conclusion"},
            context=f"evidence_need_groups[{index - 1}].coverage_source_refs",
        )
        _require_refs(
            group.coverage_source_refs,
            bundle,
            {"native_conclusion"},
            context=f"evidence_need_groups[{index - 1}].coverage_source_refs",
        )
    return ledger


def _drop_incompatible_known_refs(
    refs: Iterable[str],
    bundle: ChairPromptBundle,
    allowed_types: set[str],
    *,
    context: str,
) -> list[str]:
    """Remove only known, wrongly-typed refs and retain an audit record."""
    kept = []
    dropped = []
    for ref in _ordered_unique(refs):
        source = bundle.source_registry.get(ref)
        if source is None or source.source_type in allowed_types:
            kept.append(ref)
        else:
            dropped.append({"source_ref": ref, "source_type": source.source_type})
    if dropped:
        bundle.normalization_events.append(
            {
                "context": context,
                "action": "dropped_incompatible_known_source_refs",
                "allowed_source_types": sorted(allowed_types),
                "dropped": dropped,
            }
        )
    return kept


def _resolve_cited(
    statement: CitedChairStatement,
    bundle: ChairPromptBundle,
    expected_source_type: str | set[str],
    *,
    context: str,
) -> None:
    statement.source_refs = _ordered_unique(statement.source_refs)
    allowed = (
        {expected_source_type}
        if isinstance(expected_source_type, str)
        else expected_source_type
    )
    _require_refs(statement.source_refs, bundle, allowed, context=context)
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


def _require_refs(
    refs: Iterable[str],
    bundle: ChairPromptBundle,
    allowed_types: set[str],
    *,
    context: str = "source_refs",
) -> None:
    unique_refs = _ordered_unique(refs)
    unknown = set(unique_refs) - set(bundle.source_registry)
    if unknown:
        raise ValueError(f"{context}: unknown specialty source refs: {sorted(unknown)}")
    wrong_type = [
        ref
        for ref in unique_refs
        if bundle.source_registry[ref].source_type not in allowed_types
    ]
    if wrong_type:
        observed = {
            ref: bundle.source_registry[ref].source_type
            for ref in wrong_type
        }
        raise ValueError(
            f"{context}: incompatible specialty source refs {observed}; "
            f"expected source types {sorted(allowed_types)}"
        )


def _link_output_items(result: MDTChairIntegration) -> None:
    def ids_for(refs: Iterable[str], items: Iterable[Any], id_field: str) -> list[str]:
        selected = set(refs)
        return [
            getattr(item, id_field)
            for item in items
            if selected.intersection(item.source_refs)
        ]

    for boundary in result.assessment_boundaries:
        boundary.related_evidence_need_ids = ids_for(
            boundary.related_evidence_need_source_refs,
            result.evidence_needs,
            "need_id",
        )
    for question in result.questions:
        question.related_evidence_need_ids = ids_for(
            question.related_evidence_need_source_refs,
            result.evidence_needs,
            "need_id",
        )


def _validate_output_refs_against_ledger(
    result: MDTChairIntegration,
    ledger: ChairSemanticLedger,
) -> None:
    """Keep links auditable; do not enforce model-level medical judgments."""

    ledger_refs = {
        claim.source_ref
        for group in ledger.claim_groups
        for claim in group.claims
    }
    ledger_refs.update(
        ref for route in ledger.question_routes for ref in route.source_refs
    )
    ledger_refs.update(
        ref
        for route in ledger.question_routes
        for answer in route.answer_links
        for ref in answer.source_refs
    )
    ledger_refs.update(
        ref for group in ledger.evidence_need_groups for ref in group.source_refs
    )
    ledger_refs.update(
        ref
        for group in ledger.evidence_need_groups
        for ref in group.coverage_source_refs
    )
    output_refs = {
        ref
        for collection in (
            result.integrated_conclusions,
            result.assessment_boundaries,
            result.questions,
            result.evidence_needs,
        )
        for item in collection
        for ref in item.source_refs
    }
    unknown = output_refs - ledger_refs
    if unknown:
        raise ValueError(f"Output source refs are absent from semantic ledger: {sorted(unknown)}")


def _resolve_conflicts(
    conflicts: list[CrossSpecialtyConflict],
    result: MDTChairIntegration,
    bundle: ChairPromptBundle,
) -> None:
    expected_status = {
        (False, False): "unresolved",
        (True, False): "pending_clarification",
        (False, True): "pending_evidence",
        (True, True): "pending_clarification_and_evidence",
    }
    for index, conflict in enumerate(conflicts, 1):
        conflict.conflict_id = f"CF{index:03d}"
        _require_refs(
            conflict.related_question_source_refs,
            bundle,
            {"native_question"},
            context=f"conflicts[{index - 1}].related_question_source_refs",
        )
        _require_refs(
            conflict.related_evidence_need_source_refs,
            bundle,
            {"native_question", "evidence_gap"},
            context=f"conflicts[{index - 1}].related_evidence_need_source_refs",
        )
        specialties = []
        for position_index, position in enumerate(conflict.positions):
            _resolve_cited(
                position,
                bundle,
                "native_conclusion",
                context=(
                    f"conflicts[{index - 1}].positions[{position_index}].source_refs"
                ),
            )
            if position.source_citations:
                position.specialty = position.source_citations[0].specialty
            specialties.append(position.specialty)
        conflict.specialties = _ordered_unique(specialties)
        conflict.related_question_ids = [
            item.question_id
            for item in result.questions
            if set(conflict.related_question_source_refs).intersection(item.source_refs)
        ]
        conflict.related_evidence_need_ids = [
            item.need_id
            for item in result.evidence_needs
            if set(conflict.related_evidence_need_source_refs).intersection(item.source_refs)
        ]
        conflict.status = expected_status[
            (bool(conflict.related_question_ids), bool(conflict.related_evidence_need_ids))
        ]
