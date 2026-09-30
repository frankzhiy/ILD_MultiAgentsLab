"""Build evidence registries and run the current expert synthesis protocol."""
from __future__ import annotations
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from src.agents.mdt_chair.models import CaseEvidenceCitation, SpecialtySourceCitation
from src.guidelines.models import GuidelineEvidencePointer
from src.guidelines.runtime import GuidelineRuntime
from src.llm.base import LLMClient
from src.llm.structured import StructuredLLMGenerator
from src.utils.config import load_text, load_yaml

SPECIALTIES = ("pulmonology", "thoracic_radiology", "rheumatology", "pathology")
EVIDENCE_ROLES = ("supporting", "weakening", "discriminating", "qualifying", "background")

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
    question_refs_to_classify: set[str] = field(default_factory=set)
    evidence_need_refs_to_classify: set[str] = field(default_factory=set)
    already_classified_question_refs: set[str] = field(default_factory=set)


class _Registry:
    def __init__(
        self,
        semantic_evidence: dict[str, dict[str, Any]] | None = None,
        seed: ChairPromptBundle | None = None,
    ) -> None:
        self.sources: dict[str, SpecialtySourceCitation] = dict(
            seed.source_registry if seed is not None else {}
        )
        self.evidence: dict[str, CaseEvidenceCitation] = dict(seed.evidence_registry if seed else {})
        self.source_evidence: dict[str, dict[str, list[str]]] = {
            ref: {
                role: list(evidence.get(role, []))
                for role in EVIDENCE_ROLES
            }
            for ref, evidence in (
                seed.source_evidence.items() if seed is not None else []
            )
        }
        self.source_guidelines: dict[str, list[GuidelineEvidencePointer]] = {
            ref: list(values)
            for ref, values in (
                seed.source_guidelines.items() if seed is not None else []
            )
        }
        self.source_metadata: dict[str, dict[str, Any]] = {
            ref: dict(values)
            for ref, values in (
                seed.source_metadata.items() if seed is not None else []
            )
        }
        self._source_keys: dict[tuple[str, str, str, str, str], str] = {
            (
                item.specialty,
                item.source_type,
                item.source_path,
                item.quote,
                str(self.source_metadata.get(ref, {}).get("version_id") or ""),
            ): ref
            for ref, item in self.sources.items()
        }
        self._next_source_number = max(
            (
                int(ref[1:])
                for ref in self.sources
                if ref.startswith("S") and ref[1:].isdigit()
            ),
            default=0,
        ) + 1
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
        key = (
            specialty,
            source_type,
            source_path,
            quote,
            str((metadata or {}).get("version_id") or ""),
        )
        if key not in self._source_keys:
            ref = f"S{self._next_source_number:03d}"
            self._next_source_number += 1
            self._source_keys[key] = ref
            self.sources[ref] = SpecialtySourceCitation(
                source_ref=ref,
                specialty=specialty,
                source_type=source_type,
                source_subtype=(
                    "discussion_answer"
                    if (metadata or {}).get("origin") == "discussion_answer"
                    else str((metadata or {}).get("assessment_type") or source_type)
                ),
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
                    quote=_merge_quotes(existing.quote, value["quote"]),
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
    semantic_evidence: dict[str, dict[str, Any]] | None = None,
    discussion_round: int | None = None,
    source_seed: ChairPromptBundle | None = None,
) -> ChairPromptBundle:
    """Project assessments plus only the questions and gaps new to this round."""
    missing = sorted(set(SPECIALTIES) - set(specialty_outputs))
    if missing:
        raise ValueError(f"Missing specialty outputs: {missing}")
    registry = _Registry(semantic_evidence, source_seed)
    compact = [
        _compact_specialty(
            specialty,
            specialty_outputs[specialty],
            registry,
            discussion_round=discussion_round,
        )
        for specialty in SPECIALTIES
    ]
    for unit_id, unit in (semantic_evidence or {}).items():
        if unit.get("evidence_blocks"):
            registry.pointer({"graph_unit_id": unit_id, "segment_id": unit.get("segment_id", ""),
                              "evidence_ids": list(unit["evidence_blocks"])})
    prompt_input = {
        "case_id": case_id,
        "specialties": compact,
    }
    question_refs = {
        ref
        for ref, source in registry.sources.items()
        if source.source_type == "interspecialty_question"
        and (
            discussion_round is None
            or registry.source_metadata[ref].get("discussion_round")
            == discussion_round
        )
    }
    evidence_need_refs = {
        ref
        for ref, source in registry.sources.items()
        if source.source_type == "assessment_evidence_need"
        and (
            discussion_round is None
            or registry.source_metadata[ref].get("discussion_round")
            == discussion_round
        )
    }
    return ChairPromptBundle(
        case_id,
        prompt_input,
        registry.sources,
        registry.evidence,
        registry.source_evidence,
        registry.source_guidelines,
        registry.source_metadata,
        question_refs_to_classify=question_refs,
        evidence_need_refs_to_classify=evidence_need_refs,
    )


def _compact_specialty(
    specialty: str,
    output: dict[str, Any],
    registry: _Registry,
    *,
    discussion_round: int | None = None,
) -> dict[str, Any]:
    assessments_block = output.get("specialty_assessments")
    questions_block = output.get("interspecialty_questions")
    if not isinstance(assessments_block, dict):
        raise ValueError(f"{specialty} output is missing specialty_assessments")
    questions = list((questions_block or {}).get("questions") or [])

    projected_assessments = []
    projected_discussion_answers = []
    assessment_items = (
        assessments_block.get("assessments")
        or []
    )
    for index, item in enumerate(assessment_items):
        is_discussion_answer = item.get("origin") == "discussion_answer"
        source_type = (
            "discussion_answer" if is_discussion_answer else "specialty_assessment"
        )
        path = (
            f"discussion_answers[{index}]"
            if is_discussion_answer
            else f"specialty_assessments.assessments[{index}]"
        )
        statement = str(item.get("statement") or "").strip()
        medical_basis = str(item.get("medical_basis") or "").strip()
        decision_impact = str(item.get("decision_impact") or "").strip()
        quote = "\n".join(
            (
                f"初步判断：{statement}",
                f"医学依据：{medical_basis}",
                f"决策影响：{decision_impact}",
            )
        )
        evidence = _formal_evidence_refs(item.get("evidence") or {}, registry)
        source_ref = registry.source(
            specialty,
            source_type,
            path,
            quote,
            evidence=evidence,
            guidelines=list(item.get("guideline_evidence") or []),
            metadata={
                "assessment_id": item.get("assessment_id"),
                "original_statement": statement,
                "role": item.get("role"),
                "assessment_type": item.get("assessment_type"),
                "status": item.get("status"),
                "limitations": list(item.get("limitations") or []),
                "origin": item.get("origin", "initial_assessment"),
                "answered_question_id": item.get("answered_question_id", ""),
                "judgment_id": item.get("judgment_id") or item.get("assessment_id"),
                "version_id": item.get("version_id", ""),
                "previous_version_id": item.get("previous_version_id"),
                "conditions": dict(item.get("conditions") or {}),
            },
        )
        projected = {
            "source_ref": source_ref,
            "source_type": source_type,
            "assessment_id": item.get("assessment_id"),
            "role": item.get("role"),
            "assessment_type": item.get("assessment_type"),
            "statement": statement,
            "status": item.get("status"),
            "medical_basis": medical_basis,
            "assessability": item.get("assessability"),
            "direction": item.get("direction"),
            "confidence": item.get("confidence"),
            "clinical_role": item.get("clinical_role"),
            "decision_impact": decision_impact,
            "claims": list(item.get("claims") or []),
            "evidence_options": [
                {
                    "evidence_ref": evidence_ref,
                    "quote": registry.evidence[evidence_ref].quote,
                }
                for evidence_ref in _ordered_unique(
                    evidence_ref
                    for role in EVIDENCE_ROLES
                    for evidence_ref in evidence[role]
                )
            ],
            "limitations": list(item.get("limitations") or []),
            "judgment_id": item.get("judgment_id") or item.get("assessment_id"),
            "version_id": item.get("version_id", ""),
            "previous_version_id": item.get("previous_version_id"),
            "conditions": dict(item.get("conditions") or {}),
        }
        if is_discussion_answer:
            projected_discussion_answers.append(projected)
        else:
            projected_assessments.append(projected)

    projected_questions = []
    for index, item in enumerate(questions):
        path = f"interspecialty_questions.questions[{index}]"
        question = str(item.get("question") or "").strip()
        evidence = _related_evidence_refs(item.get("related_evidence") or [], registry)
        if not evidence["background"]:
            related_ids = set(item.get("related_assessment_ids") or [])
            evidence["background"] = _ordered_unique(
                option["evidence_ref"]
                for assessment in projected_assessments
                if assessment["assessment_id"] in related_ids
                for option in assessment["evidence_options"]
            )
        source_ref = registry.source(
            specialty,
            "interspecialty_question",
            path,
            question,
            evidence=evidence,
            metadata={
                "target_specialty": item.get("target_specialty"),
                "why_it_matters": item.get("why_it_matters"),
                "decision_unlocked": item.get("decision_unlocked"),
                "discussion_round": item.get("_discussion_round"),
                "discussion_issue_id": item.get("_discussion_issue_id"),
                "discussion_disposition": item.get("_discussion_disposition"),
            },
        )
        if (
            discussion_round is not None
            and item.get("_discussion_round") != discussion_round
        ):
            continue
        projected_questions.append(
            {
                **{key: value for key, value in item.items() if not key.startswith("_")},
                "source_ref": source_ref,
                "source_type": "interspecialty_question",
                "related_evidence": evidence["background"],
            }
        )

    evidence_needs = []
    for index, item in enumerate(assessments_block.get("evidence_gaps") or []):
        path = f"specialty_assessments.evidence_gaps[{index}]"
        required = str(item.get("missing_information") or "").strip()
        evidence = _related_evidence_refs(item.get("related_evidence") or [], registry)
        source_ref = registry.source(
            specialty,
            "assessment_evidence_need",
            path,
            required,
            evidence=evidence,
            metadata={
                "available_information": item.get("available_information"),
                "why_it_matters": item.get("why_it_matters"),
                "decision_unlocked": item.get("decision_unlocked"),
                "discussion_round": item.get("_discussion_round"),
                "discussion_issue_id": item.get("_discussion_issue_id"),
                "discussion_disposition": item.get("_discussion_disposition"),
            },
        )
        if (
            discussion_round is not None
            and item.get("_discussion_round") != discussion_round
        ):
            continue
        evidence_needs.append(
            {
                **{key: value for key, value in item.items() if not key.startswith("_")},
                "source_ref": source_ref,
                "source_type": "assessment_evidence_need",
                "related_evidence": evidence["background"],
            }
        )

    return {
        "specialty": specialty,
        "specialty_question": assessments_block.get("specialty_question"),
        "assessability": assessments_block.get("assessability"),
        "boundaries": list(assessments_block.get("boundaries") or []),
        "conditional_contributions": list(assessments_block.get("conditional_contributions") or []),
        "specialty_assessments": projected_assessments,
        "discussion_answers": projected_discussion_answers,
        "interspecialty_questions": projected_questions,
        "evidence_needs": evidence_needs,
    }


def _formal_evidence_refs(
    evidence: dict[str, Any], registry: _Registry
) -> dict[str, list[str]]:
    relations = evidence.get("evidence_relations")
    if isinstance(relations, list):
        grouped = {role: [] for role in EVIDENCE_ROLES}
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            ref = registry.pointer(relation)
            direction = {"supports": "supporting", "weakens": "weakening"}.get(relation.get("direction"))
            if direction:
                grouped[direction].append(ref)
            function = relation.get("function", "background")
            if function in grouped:
                grouped[function].append(ref)
        return {role: _ordered_unique(refs) for role, refs in grouped.items()}
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


def _merge_quotes(*values: str) -> str:
    quotes = [value for value in _ordered_unique(values) if value]
    return "\n".join(
        quote
        for quote in quotes
        if not any(quote != other and quote in other for other in quotes)
    )


class MDTChairAgent:
    def __init__(self, llm: LLMClient, *, prompt_path: str | Path,
                 temperature: float = 0.0, max_tokens: int = 12000,
                 max_attempts: int = 2, retry_backoff_seconds: float = 0.0,
                 guideline_runtime: GuidelineRuntime | None = None,
                 event_callback: Callable[[str, dict[str, Any]], None] | None = None):
        self.prompt = load_text(prompt_path)
        self.guideline_runtime = guideline_runtime
        self.generator = StructuredLLMGenerator(
            llm, temperature=temperature, max_tokens=max_tokens, max_attempts=max_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
            response_format_mode="json_schema" if getattr(llm, "supports_json_schema", False) else "json_object",
            event_callback=event_callback,
        )

    @classmethod
    def from_config(cls, config_path, llm, *, event_callback=None):
        config = load_yaml(config_path)
        if config.get("protocol_version") != "expert.v2":
            raise ValueError("MDT chair requires protocol_version=expert.v2")
        return cls(llm, prompt_path=config["prompt"],
                   temperature=float(config.get("temperature", 0.0)),
                   max_tokens=int(config.get("max_tokens", 12000)),
                   max_attempts=int(config.get("max_attempts", 2)),
                   retry_backoff_seconds=float(config.get("retry_backoff_seconds", 2)),
                   guideline_runtime=GuidelineRuntime.from_config(config), event_callback=event_callback)

    def integrate(self, bundle, *, discussion_previous=None, discussion_responses=None, discussion_reviews=None):
        from src.agents.mdt_chair.expert import integrate_expert
        return integrate_expert(self, bundle, discussion_previous, discussion_responses, discussion_reviews)
