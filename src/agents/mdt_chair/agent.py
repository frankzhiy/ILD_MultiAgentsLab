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
    EvidenceNeed,
    MDTChairIntegration,
    SpecialtySourceCitation,
)
from src.guidelines.models import GuidelineEvidencePointer
from src.llm.base import LLMClient
from src.llm.prompting import prompt_json, prompt_schema_json
from src.llm.structured import StructuredLLMGenerator
from src.utils.config import load_text, load_yaml, render_template


SYSTEM_PROMPT = (
    "你是以呼吸科为主要背景的 ILD MDT 主持人。你只整合四个专科已经形成的正式初步判断、"
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
    question_refs_to_classify: set[str] = field(default_factory=set)
    evidence_need_refs_to_classify: set[str] = field(default_factory=set)
    already_classified_question_refs: set[str] = field(default_factory=set)


def _source_ref_schema_constraints(
    bundle: ChairPromptBundle,
    *,
    semantic_ledger: ChairSemanticLedger | None = None,
    preserved: dict[str, dict[str, set[str]]] | None = None,
) -> dict[str, dict[str, set[str]]]:
    refs = {
        source_type: {
            ref
            for ref, source in bundle.source_registry.items()
            if source.source_type == source_type
        }
        for source_type in (
            "specialty_assessment",
            "interspecialty_question",
            "assessment_evidence_need",
        )
    }
    assessments = refs["specialty_assessment"]
    questions = bundle.question_refs_to_classify
    needs = questions | bundle.evidence_need_refs_to_classify
    if semantic_ledger is None:
        return {
            "LedgerAtomicClaim": {"source_ref": assessments},
            "LedgerQuestionRoute": {"source_refs": questions},
            "LedgerAnswerLink": {"source_refs": assessments},
            "LedgerEvidenceNeedGroup": {
                "source_refs": needs,
                "coverage_source_refs": assessments,
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
    blocking_needs = {
        ref
        for group in semantic_ledger.evidence_need_groups
        if group.decision_role == "blocking_boundary"
        for ref in group.source_refs
    }
    refinement_needs = {
        ref
        for group in semantic_ledger.evidence_need_groups
        if group.decision_role == "non_blocking_refinement"
        for ref in group.source_refs
    }
    coverage = {
        ref
        for group in semantic_ledger.evidence_need_groups
        for ref in group.coverage_source_refs
    }
    constraints = {
        "IntegratedConclusion": {"source_refs": claims["integrated"]},
        "AssessmentBoundary": {
            "source_refs": claims["boundary"] | questions | {
                ref
                for ref in blocking_needs
                if bundle.source_registry[ref].source_type
                == "interspecialty_question"
            },
            "related_evidence_need_source_refs": blocking_needs,
        },
        "ConflictPosition": {"source_refs": claims["conflict"]},
        "CrossSpecialtyConflict": {
            "related_question_source_refs": questions,
            "related_evidence_need_source_refs": blocking_needs | refinement_needs,
        },
        "IntegratedQuestion": {
            "source_refs": questions,
            "related_evidence_need_source_refs": refinement_needs,
        },
        "QuestionAnswer": {"source_refs": answers},
        "EvidenceNeed": {"source_refs": refinement_needs | coverage},
    }
    for model_name, fields in (preserved or {}).items():
        for field_name, values in fields.items():
            constraints.setdefault(model_name, {}).setdefault(field_name, set()).update(
                values
            )
    return constraints


def _discussion_previous_view(
    previous: MDTChairIntegration,
    bundle: ChairPromptBundle,
) -> tuple[dict[str, Any], dict[str, dict[str, set[str]]]]:
    """Keep the prior five-section state on its append-only source registry."""

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            if {
                "source_ref",
                "specialty",
                "source_type",
                "source_path",
                "quote",
            } <= value.keys():
                ref = value["source_ref"]
                current = bundle.source_registry.get(ref)
                identity = (
                    value["specialty"],
                    value["source_type"],
                    value["source_path"],
                    value["quote"],
                )
                if current is None:
                    raise ValueError(f"Prior specialty source ref is absent: {ref}")
                if (
                    current.specialty,
                    current.source_type,
                    current.source_path,
                    current.quote,
                ) != identity:
                    raise ValueError(f"Prior specialty source ref changed identity: {ref}")
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    previous_data = previous.model_dump(mode="json")
    collect(previous_data)

    def refs(values: list[str]) -> set[str]:
        missing = set(values) - set(bundle.source_registry)
        if missing:
            raise ValueError(f"Prior specialty source refs are absent: {sorted(missing)}")
        return set(values)

    preserved = {
        "IntegratedConclusion": {
            "source_refs": set().union(*(
                refs(item.source_refs) for item in previous.integrated_conclusions
            )),
        },
        "AssessmentBoundary": {
            "source_refs": set().union(*(
                refs(item.source_refs) for item in previous.assessment_boundaries
            )),
            "related_evidence_need_source_refs": set().union(*(
                refs(item.related_evidence_need_source_refs)
                for item in previous.assessment_boundaries
            )),
        },
        "ConflictPosition": {
            "source_refs": set().union(*(
                refs(position.source_refs)
                for conflict in previous.conflicts
                for position in conflict.positions
            )),
        },
        "CrossSpecialtyConflict": {
            "related_question_source_refs": set().union(*(
                refs(item.related_question_source_refs) for item in previous.conflicts
            )),
            "related_evidence_need_source_refs": set().union(*(
                refs(item.related_evidence_need_source_refs)
                for item in previous.conflicts
            )),
        },
        "IntegratedQuestion": {
            "source_refs": set().union(*(
                refs(item.source_refs) for item in previous.questions
            )),
            "related_evidence_need_source_refs": set().union(*(
                refs(item.related_evidence_need_source_refs)
                for item in previous.questions
            )),
        },
        "QuestionAnswer": {
            "source_refs": set().union(*(
                refs(answer.source_refs)
                for question in previous.questions
                for answer in question.answers
            )),
        },
        "EvidenceNeed": {
            "source_refs": set().union(*(
                refs(item.source_refs) for item in previous.evidence_needs
            )),
        },
    }

    from src.agents.mdt_discussion.prompt_projection import build_chair_prompt_view

    return build_chair_prompt_view(previous_data), preserved


class _Registry:
    def __init__(
        self,
        semantic_evidence: dict[str, dict[str, Any]] | None = None,
        seed: ChairPromptBundle | None = None,
    ) -> None:
        self.sources: dict[str, SpecialtySourceCitation] = dict(
            seed.source_registry if seed is not None else {}
        )
        self.evidence: dict[str, CaseEvidenceCitation] = {}
        self.source_evidence: dict[str, dict[str, list[str]]] = {
            ref: {role: list(values) for role, values in evidence.items()}
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
        self._source_keys: dict[tuple[str, str, str, str], str] = {
            (item.specialty, item.source_type, item.source_path, item.quote): ref
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
        key = (specialty, source_type, source_path, quote)
        if key not in self._source_keys:
            ref = f"S{self._next_source_number:03d}"
            self._next_source_number += 1
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
    discussion_round: int | None = None,
    source_seed: ChairPromptBundle | None = None,
) -> ChairPromptBundle:
    """Project assessments plus only the questions and gaps new to this round."""
    del input_summaries  # retained only for compatibility with the former caller API
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
        assessments_block = output.get("professional_conclusions")
        if not isinstance(assessments_block, dict):
            raise ValueError(f"{specialty} output is missing specialty_assessments")
        questions = list(assessments_block.get("interspecialty_questions") or [])
    else:
        questions = (
            list(questions_block.get("questions") or [])
            if isinstance(questions_block, dict)
            else []
        )

    projected_assessments = []
    assessment_items = (
        assessments_block.get("assessments")
        or assessments_block.get("conclusions")
        or []
    )
    for index, item in enumerate(assessment_items):
        path = f"specialty_assessments.assessments[{index}]"
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
            "specialty_assessment",
            path,
            quote,
            evidence=evidence,
            guidelines=list(item.get("guideline_evidence") or []),
            metadata={
                "assessment_id": item.get("assessment_id") or item.get("conclusion_id"),
                "original_statement": statement,
                "role": item.get("role"),
                "assessment_type": item.get("assessment_type") or item.get("conclusion_type"),
                "status": item.get("status"),
                "limitations": list(item.get("limitations") or []),
                "origin": item.get("origin", "initial_assessment"),
                "answered_question_id": item.get("answered_question_id", ""),
            },
        )
        projected_assessments.append(
            {
                "source_ref": source_ref,
                "source_type": "specialty_assessment",
                "assessment_id": item.get("assessment_id") or item.get("conclusion_id"),
                "role": item.get("role"),
                "assessment_type": item.get("assessment_type") or item.get("conclusion_type"),
                "statement": statement,
                "status": item.get("status"),
                "medical_basis": medical_basis,
                "decision_impact": decision_impact,
                "evidence": evidence,
                "limitations": list(item.get("limitations") or []),
            }
        )

    projected_questions = []
    for index, item in enumerate(questions):
        path = f"interspecialty_questions.questions[{index}]"
        question = str(item.get("question") or "").strip()
        evidence = _related_evidence_refs(item.get("related_evidence") or [], registry)
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
        "specialty_assessments": projected_assessments,
        "interspecialty_questions": projected_questions,
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
        discussion_reviews: list[Any] | None = None,
    ) -> tuple[MDTChairIntegration, dict]:
        discussion_context: dict[str, Any] = {}
        preserved_constraints: dict[str, dict[str, set[str]]] = {}
        if discussion_previous is not None:
            from src.agents.mdt_discussion.integration import build_review_dispositions

            previous_data = discussion_previous.model_dump(mode="json")

            def collect_source_refs(value: Any) -> set[str]:
                if isinstance(value, dict):
                    return set(value.get("source_refs") or []).union(*(
                        collect_source_refs(item) for item in value.values()
                    ))
                if isinstance(value, list):
                    return set().union(*(collect_source_refs(item) for item in value))
                return set()

            bundle.already_classified_question_refs = {
                ref
                for ref in collect_source_refs(previous_data)
                if ref in bundle.source_registry
                and bundle.source_registry[ref].source_type
                == "interspecialty_question"
            }
            previous_view, preserved_constraints = _discussion_previous_view(
                discussion_previous,
                bundle,
            )
            answer_ids = {
                answer.answer_id
                for response in discussion_responses or []
                for answer in response.answers
            }
            preserved_constraints["QuestionAnswer"]["source_refs"].update(
                ref
                for ref, metadata in bundle.source_metadata.items()
                if metadata.get("assessment_id") in answer_ids
            )
            review_dispositions = build_review_dispositions(
                discussion_previous,
                discussion_reviews or [],
            )
            answer_refs_by_issue: dict[str, set[str]] = {}
            for response in discussion_responses or []:
                for answer in response.answers:
                    answer_refs_by_issue.setdefault(answer.issue_id, set()).update(
                        ref
                        for ref, metadata in bundle.source_metadata.items()
                        if metadata.get("assessment_id") == answer.answer_id
                    )
            for issue_id, disposition in review_dispositions.items():
                question_refs = set(disposition["question_source_refs"])
                if disposition["destination"] == "assessment_boundary":
                    preserved_constraints["AssessmentBoundary"]["source_refs"].update(
                        question_refs | answer_refs_by_issue.get(issue_id, set())
                    )
                elif disposition["destination"] == "evidence_need":
                    need_refs = {
                        ref
                        for ref, metadata in bundle.source_metadata.items()
                        if metadata.get("discussion_issue_id") == issue_id
                        and metadata.get("discussion_disposition")
                        == "convert_to_evidence_need"
                    }
                    preserved_constraints["EvidenceNeed"]["source_refs"].update(
                        question_refs | need_refs
                    )

            discussion_context = {
                "previous_five_sections": previous_view,
                "programmatic_review_dispositions": review_dispositions,
                "round_responses": [
                    {
                        "specialty": response.specialty,
                        "answers": [
                            {
                                "answer_id": answer.answer_id,
                                "issue_id": answer.issue_id,
                                "issue_type": answer.issue_type,
                                "answerability": answer.answerability,
                                "answer": answer.answer,
                                "medical_basis": answer.medical_basis,
                                "changed_from_previous": answer.changed_from_previous,
                                "remaining_limitation": answer.remaining_limitation,
                                "evidence_gaps": [
                                    item.model_dump(mode="json")
                                    for item in answer.evidence_gaps
                                ],
                            }
                            for answer in response.answers
                        ],
                    }
                    for response in discussion_responses or []
                ],
                "answer_reviews": [
                    {
                        "issue_id": review.issue_id,
                        "answer_id": review.answer_id,
                        "reviewer_specialty": review.reviewer_specialty,
                        "outcome": review.outcome,
                        "rationale": review.rationale,
                        "follow_up_question": (
                            review.follow_up_question.model_dump(mode="json")
                            if review.follow_up_question is not None
                            else None
                        ),
                        "evidence_gap": (
                            review.evidence_gap.model_dump(mode="json")
                            if review.evidence_gap is not None
                            else None
                        ),
                    }
                    for review in discussion_reviews or []
                ],
            }
        compact_json = prompt_json(bundle.prompt_input)
        discussion_context_json = prompt_json(discussion_context)
        ledger_schema = (
            "由 API 的严格 JSON Schema response_format 提供。"
            if self.generator.response_format_mode == "json_schema"
            else prompt_schema_json(ChairSemanticLedger)
        )
        ledger_prompt = render_template(
            self.ledger_prompt,
            {
                "chair_input": compact_json,
                "discussion_context": discussion_context_json,
                "output_schema": ledger_schema,
                "conflict_detection_scope": (
                    "当前是四科初次正式意见整合：对初次 specialty_assessments 启用"
                    "硬冲突和决策相关分歧检测。"
                    if discussion_previous is None
                    else "当前是会中重整：对累计 specialty_assessments 继续启用"
                    "硬冲突和决策相关分歧检测。复核中的 flag_incompatibility "
                    "只是冲突检测候选，必须按相同对象、时点、证据条件和专业层级"
                    "重新判断，不能直接转成正式冲突。"
                ),
            },
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
                "discussion_context": discussion_context_json,
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
                    discussion_reviews or [],
                )
                _materialize_accepted_boundary_question_refs(
                    value,
                    discussion_previous,
                    discussion_responses or [],
                    discussion_reviews or [],
                    bundle,
                )
                _materialize_evidence_need_conversion_refs(
                    value,
                    discussion_previous,
                    discussion_reviews or [],
                    bundle,
                )
            resolved = resolve_chair_references(
                value,
                bundle,
                None if discussion_previous is not None else ledger,
            )
            if discussion_previous is not None:
                _validate_review_destinations(
                    resolved,
                    discussion_previous,
                    discussion_reviews or [],
                    bundle,
                )
            return resolved

        result, synthesis_trace = self.generator.generate(
            schema_model=MDTChairIntegration,
            schema_name="mdt_chair_integration",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=synthesis_prompt,
            extra_validation=resolve,
            string_field_constraints=_source_ref_schema_constraints(
                bundle,
                semantic_ledger=ledger,
                preserved=preserved_constraints,
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
            "discussion_context_chars": len(discussion_context_json),
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
    result.schema_version = "mdt_chair.v8"
    for index, conclusion in enumerate(result.integrated_conclusions, 1):
        conclusion.conclusion_id = f"IC{index:03d}"
        _resolve_cited(
            conclusion,
            bundle,
            "specialty_assessment",
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
            {"specialty_assessment", "interspecialty_question"},
            context=f"assessment_boundaries[{index - 1}].source_refs",
        )
        _require_refs(
            boundary.related_evidence_need_source_refs,
            bundle,
            {"interspecialty_question", "assessment_evidence_need"},
            context=(
                f"assessment_boundaries[{index - 1}]"
                ".related_evidence_need_source_refs"
            ),
        )
        boundary.specialties = _ordered_unique(
            citation.specialty for citation in boundary.source_citations
        )
        boundary.assessment_source_refs = [
            citation.source_ref
            for citation in boundary.source_citations
            if citation.source_type == "specialty_assessment"
        ]
        boundary.question_source_refs = [
            citation.source_ref
            for citation in boundary.source_citations
            if citation.source_type == "interspecialty_question"
        ]

    for index, need in enumerate(result.evidence_needs, 1):
        need.need_id = f"EN{index:03d}"
        _resolve_cited(
            need,
            bundle,
            {
                "interspecialty_question",
                "assessment_evidence_need",
                "specialty_assessment",
            },
            context=f"evidence_needs[{index - 1}].source_refs",
        )
        need.raised_by = _ordered_unique(
            citation.specialty
            for citation in need.source_citations
            if citation.source_type
            in {"interspecialty_question", "assessment_evidence_need"}
        )
        need.provided_by = _ordered_unique(
            citation.specialty
            for citation in need.source_citations
            if citation.source_type == "specialty_assessment"
        )
        need.assessment_source_refs = [
            citation.source_ref
            for citation in need.source_citations
            if citation.source_type
            in {"specialty_assessment", "assessment_evidence_need"}
        ]
        need.question_source_refs = [
            citation.source_ref
            for citation in need.source_citations
            if citation.source_type == "interspecialty_question"
        ]

    for index, question in enumerate(result.questions, 1):
        question.question_id = f"Q{index:03d}"
        _resolve_cited(
            question,
            bundle,
            "interspecialty_question",
            context=f"questions[{index - 1}].source_refs",
        )
        _require_refs(
            question.related_evidence_need_source_refs,
            bundle,
            {"interspecialty_question", "assessment_evidence_need"},
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
        allowed_by_specialty: dict[str, list[str]] = {}
        relation_by_ref: dict[str, str] = {}
        if ledger is not None:
            for route in ledger.question_routes:
                if route.route not in {"question", "mixed"} or not set(
                    route.source_refs
                ).intersection(question.source_refs):
                    continue
                for link in route.answer_links:
                    allowed_by_specialty.setdefault(link.specialty, []).extend(
                        link.source_refs
                    )
                    relation_by_ref.update(
                        {ref: link.relation for ref in link.source_refs}
                    )

        normalized_answers = []
        for answer_index, answer in enumerate(question.answers):
            context = f"questions[{index - 1}].answers[{answer_index}].source_refs"
            answer.source_refs = _drop_incompatible_known_refs(
                answer.source_refs,
                bundle,
                {"specialty_assessment"},
                context=context,
            )
            _require_refs(
                answer.source_refs,
                bundle,
                {"specialty_assessment"},
                context=context,
            )
            if ledger is not None:
                allowed_refs = {
                    ref for refs in allowed_by_specialty.values() for ref in refs
                }
                eligible_refs = [
                    ref for ref in answer.source_refs if ref in allowed_refs
                ]
            else:
                eligible_refs = [
                    ref
                    for ref in answer.source_refs
                    if bundle.source_registry[ref].specialty
                    in question.target_specialties
                ]
            dropped_refs = [
                ref for ref in answer.source_refs if ref not in eligible_refs
            ]
            if dropped_refs:
                bundle.normalization_events.append(
                    {
                        "context": context,
                        "action": "dropped_invalid_question_answer_source_refs",
                        "target_specialties": question.target_specialties,
                        "dropped": dropped_refs,
                    }
                )
            if not eligible_refs and len(allowed_by_specialty) == 1:
                eligible_refs = _ordered_unique(
                    next(iter(allowed_by_specialty.values()))
                )
                bundle.normalization_events.append(
                    {
                        "context": context,
                        "action": "restored_question_answer_source_refs_from_ledger",
                        "restored": eligible_refs,
                    }
                )

            refs_by_specialty: dict[str, list[str]] = {}
            for ref in eligible_refs:
                specialty = bundle.source_registry[ref].specialty
                refs_by_specialty.setdefault(specialty, []).append(ref)
            for specialty, refs in refs_by_specialty.items():
                normalized = answer.model_copy(deep=True)
                normalized.source_refs = refs
                if ledger is not None:
                    relations = {relation_by_ref[ref] for ref in refs}
                    normalized.relation = (
                        "partial_answer"
                        if "partial_answer" in relations
                        else "evidence_boundary"
                        if relations == {"evidence_boundary"}
                        else "direct_answer"
                    )
                _resolve_cited(
                    normalized,
                    bundle,
                    "specialty_assessment",
                    context=context,
                )
                normalized.specialty = specialty
                normalized_answers.append(normalized)
        question.answers = normalized_answers
        question.responded_by = _ordered_unique(
            answer.specialty for answer in question.answers
        )
        question.awaiting_specialties = [
            specialty
            for specialty in question.target_specialties
            if specialty not in question.responded_by
        ]
        if not question.answers:
            question.answer_status = "unanswered"
        elif question.awaiting_specialties and question.answer_status in {
            "answered",
            "boundary_answered",
        }:
            question.answer_status = "partially_answered"
        question.response_status = (
            "all_responded"
            if question.target_specialties and not question.awaiting_specialties
            else "partially_responded"
            if question.responded_by
            else "none_responded"
        )
        if question.answer_status in {"answered", "boundary_answered"}:
            question.review_status = (
                "not_reviewed" if ledger is not None else "awaiting_review"
            )
            question.discussion_status = (
                "not_started" if ledger is not None else "awaiting_requester_review"
            )
            question.closure_type = None
        elif question.answer_status == "blocked_by_evidence":
            question.review_status = "converted_to_evidence_need"
            question.discussion_status = "waiting_for_new_evidence"
            question.closure_type = "converted_to_evidence_need"
        else:
            question.review_status = "not_reviewed"
            question.discussion_status = "awaiting_answer"
            question.closure_type = None

    # The public board contains only questions that still need an explicit answer.
    result.questions = [
        question
        for question in result.questions
        if question.answer_status in {"unanswered", "partially_answered"}
    ]
    for index, question in enumerate(result.questions, 1):
        question.question_id = f"Q{index:03d}"

    _link_output_items(result)
    _resolve_conflicts(result.conflicts, result, bundle)
    if ledger is not None:
        _validate_output_refs_against_ledger(result, ledger)
    return result


def _validate_review_destinations(
    result: MDTChairIntegration,
    previous: MDTChairIntegration,
    reviews: list[Any],
    bundle: ChairPromptBundle,
) -> None:
    from src.agents.mdt_discussion.integration import build_review_dispositions

    dispositions = build_review_dispositions(previous, reviews)
    for issue_id, disposition in dispositions.items():
        question_refs = set(disposition["question_source_refs"])
        destination = disposition["destination"]
        if destination == "assessment_boundary" and not any(
            question_refs.intersection(boundary.source_refs)
            for boundary in result.assessment_boundaries
        ):
            raise ValueError(
                f"Accepted boundary for {issue_id} must appear in assessment_boundaries"
            )
        if destination == "evidence_need":
            need_refs = {
                ref
                for ref, metadata in bundle.source_metadata.items()
                if metadata.get("discussion_issue_id") == issue_id
                and metadata.get("discussion_disposition")
                == "convert_to_evidence_need"
            }
            if not need_refs or not any(
                need_refs.intersection(need.source_refs)
                for need in result.evidence_needs
            ):
                raise ValueError(
                    f"Evidence-need conversion for {issue_id} must appear in evidence_needs"
                )


def _materialize_accepted_boundary_question_refs(
    result: MDTChairIntegration,
    previous: MDTChairIntegration,
    responses: list[Any],
    reviews: list[Any],
    bundle: ChairPromptBundle,
) -> None:
    """Attach accepted question provenance to its uniquely matching boundary."""

    from src.agents.mdt_discussion.integration import build_review_dispositions

    response_specialties = {
        answer.issue_id: response.specialty
        for response in responses
        for answer in response.answers
    }
    questions = {question.question_id: question for question in previous.questions}
    for issue_id, disposition in build_review_dispositions(previous, reviews).items():
        if disposition["destination"] != "assessment_boundary":
            continue
        question = questions[issue_id]
        question_refs = set(question.source_refs)
        if any(
            question_refs.intersection(boundary.source_refs)
            for boundary in result.assessment_boundaries
        ):
            continue
        specialty = response_specialties.get(issue_id)
        candidates = [
            boundary
            for boundary in result.assessment_boundaries
            if specialty and any(
                bundle.source_registry[ref].specialty == specialty
                for ref in boundary.source_refs
            )
        ]
        if len(candidates) == 1:
            candidates[0].source_refs = _ordered_unique(
                [*candidates[0].source_refs, *question.source_refs]
            )


def _materialize_evidence_need_conversion_refs(
    result: MDTChairIntegration,
    previous: MDTChairIntegration,
    reviews: list[Any],
    bundle: ChairPromptBundle,
) -> None:
    """Create the requester-approved evidence need when the LLM omits it."""

    from src.agents.mdt_discussion.integration import build_review_dispositions

    for issue_id, disposition in build_review_dispositions(previous, reviews).items():
        if disposition["destination"] != "evidence_need":
            continue
        need_refs = {
            ref
            for ref, metadata in bundle.source_metadata.items()
            if metadata.get("discussion_issue_id") == issue_id
            and metadata.get("discussion_disposition") == "convert_to_evidence_need"
        }
        if not need_refs or any(
            need_refs.intersection(need.source_refs) for need in result.evidence_needs
        ):
            continue
        review = next(
            (
                review
                for review in reviews
                if review.issue_id == issue_id
                and review.outcome == "convert_to_evidence_need"
                and review.evidence_gap is not None
            ),
            None,
        )
        if review is None:
            continue
        gap = review.evidence_gap
        result.evidence_needs.append(EvidenceNeed(
            status="missing",
            required_information=gap.missing_information,
            available_information=gap.available_information,
            remaining_information=gap.missing_information,
            why_it_matters=gap.why_it_matters,
            decision_unlocked=gap.decision_unlocked,
            source_refs=sorted(need_refs),
        ))


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
                {"specialty_assessment"},
                context=f"claim_groups[{topic_index - 1}].claims[{claim_index - 1}].source_ref",
            )
        _validate_conflict_group(group, bundle, topic_index - 1)
    normalized_routes = []
    for route in ledger.question_routes:
        ignored = [
            ref
            for ref in route.source_refs
            if ref in bundle.already_classified_question_refs
        ]
        if ignored:
            route.source_refs = [ref for ref in route.source_refs if ref not in ignored]
            bundle.normalization_events.append({
                "context": "question_routes",
                "action": "ignored_already_classified_questions",
                "dropped": ignored,
            })
        if route.source_refs:
            normalized_routes.append(route)
    ledger.question_routes = normalized_routes

    for index, route in enumerate(ledger.question_routes, 1):
        route.route_id = f"R{index:03d}"
        _require_refs(
            route.source_refs,
            bundle,
            {"interspecialty_question"},
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
                {"specialty_assessment"},
                context=(
                    f"question_routes[{index - 1}].answer_links[{answer_index}].source_refs"
                ),
            )
            if not answer.source_refs:
                continue
            _require_refs(
                answer.source_refs,
                bundle,
                {"specialty_assessment"},
                context=(
                    f"question_routes[{index - 1}].answer_links[{answer_index}].source_refs"
                ),
            )
            eligible_refs = [
                ref
                for ref in answer.source_refs
                if bundle.source_registry[ref].specialty in route.target_specialties
            ]
            dropped_refs = [
                ref for ref in answer.source_refs if ref not in eligible_refs
            ]
            if dropped_refs:
                bundle.normalization_events.append(
                    {
                        "context": (
                            f"question_routes[{index - 1}]"
                            f".answer_links[{answer_index}].source_refs"
                        ),
                        "action": "dropped_non_target_specialty_answers",
                        "target_specialties": route.target_specialties,
                        "dropped": dropped_refs,
                    }
                )
            answer.source_refs = eligible_refs
            if not answer.source_refs:
                continue
            specialties = {
                bundle.source_registry[ref].specialty for ref in answer.source_refs
            }
            if len(specialties) != 1:
                raise ValueError(
                    f"question_routes[{index - 1}].answer_links[{answer_index}] "
                    "must cite assessments from exactly one target specialty"
                )
            answer.specialty = next(iter(specialties))
            answer_links.append(answer)
        route.answer_links = answer_links
    for index, group in enumerate(ledger.evidence_need_groups, 1):
        group.group_id = f"NG{index:03d}"
        _require_refs(
            group.source_refs,
            bundle,
            {"interspecialty_question", "assessment_evidence_need"},
            context=f"evidence_need_groups[{index - 1}].source_refs",
        )
        group.coverage_source_refs = _drop_incompatible_known_refs(
            group.coverage_source_refs,
            bundle,
            {"specialty_assessment"},
            context=f"evidence_need_groups[{index - 1}].coverage_source_refs",
        )
        _require_refs(
            group.coverage_source_refs,
            bundle,
            {"specialty_assessment"},
            context=f"evidence_need_groups[{index - 1}].coverage_source_refs",
        )
    expected_questions = bundle.question_refs_to_classify
    routed_question_refs = [
        ref for route in ledger.question_routes for ref in route.source_refs
    ]
    routed_questions = set(routed_question_refs)
    duplicates = sorted({
        ref for ref in routed_question_refs if routed_question_refs.count(ref) > 1
    })
    if expected_questions != routed_questions or duplicates:
        missing = sorted(expected_questions - routed_questions)
        unknown = sorted(routed_questions - expected_questions)
        raise ValueError(
            "Every in-scope question must be classified exactly once; "
            f"missing={missing}, unknown={unknown}, duplicates={duplicates}"
        )
    return ledger


def _validate_conflict_group(group, bundle: ChairPromptBundle, index: int) -> None:
    context = f"claim_groups[{index}]"
    if group.disposition != "conflict":
        if group.conflict_nature is not None:
            raise ValueError(f"{context}.conflict_nature is only valid for conflict groups")
        return
    if group.conflict_nature is None:
        raise ValueError(f"{context}.conflict_nature is required")
    for field_name in (
        "comparison_target",
        "comparison_conditions",
        "why_incompatible",
        "decision_impact",
    ):
        if not str(getattr(group, field_name)).strip():
            raise ValueError(f"{context}.{field_name} is required")
    specialties = {
        bundle.source_registry[claim.source_ref].specialty for claim in group.claims
    }
    if len(specialties) < 2:
        raise ValueError(f"{context} must compare at least two specialties")
    if group.conflict_nature == "direct_contradiction":
        statuses = {claim.epistemic_status for claim in group.claims}
        if statuses != {"affirms", "denies"}:
            raise ValueError(
                f"{context} direct_contradiction requires affirms and denies claims"
            )
        levels = {claim.professional_level for claim in group.claims}
        if len(levels) != 1:
            raise ValueError(
                f"{context} direct_contradiction requires one professional level"
            )
        invalid = [
            claim.source_ref
            for claim in group.claims
            if bundle.source_metadata[claim.source_ref].get("status")
            not in {"supported", "favored"}
        ]
        if invalid:
            raise ValueError(
                f"{context} direct_contradiction requires assessable specialty assessments: "
                f"{invalid}"
            )
        return
    invalid = [
        claim.source_ref
        for claim in group.claims
        if claim.position_role != "preferred"
        or claim.epistemic_status
        in {"indeterminate", "not_assessable", "not_applicable"}
        or bundle.source_metadata[claim.source_ref].get("role") != "primary"
        or bundle.source_metadata[claim.source_ref].get("status")
        not in {"supported", "favored"}
    ]
    if invalid:
        raise ValueError(
            f"{context} decision_relevant_discordance requires assessable "
            f"preferred primary specialty assessments: {invalid}"
        )


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
    for index, conflict in enumerate(result.conflicts):
        refs = {
            ref for position in conflict.positions for ref in position.source_refs
        }
        matched = any(
            group.disposition == "conflict"
            and group.conflict_nature == conflict.conflict_nature
            and refs.issubset({claim.source_ref for claim in group.claims})
            for group in ledger.claim_groups
        )
        if not matched:
            raise ValueError(
                f"conflicts[{index}] is not supported by a matching semantic-ledger "
                "conflict group"
            )


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
            {"interspecialty_question"},
            context=f"conflicts[{index - 1}].related_question_source_refs",
        )
        _require_refs(
            conflict.related_evidence_need_source_refs,
            bundle,
            {"interspecialty_question", "assessment_evidence_need"},
            context=f"conflicts[{index - 1}].related_evidence_need_source_refs",
        )
        specialties = []
        for position_index, position in enumerate(conflict.positions):
            _resolve_cited(
                position,
                bundle,
                "specialty_assessment",
                context=(
                    f"conflicts[{index - 1}].positions[{position_index}].source_refs"
                ),
            )
            if position.source_citations:
                position.specialty = position.source_citations[0].specialty
            specialties.append(position.specialty)
        conflict.specialties = _ordered_unique(specialties)
        if len(conflict.specialties) < 2:
            raise ValueError(
                f"conflicts[{index - 1}] must compare at least two specialties"
            )
        refs = [ref for position in conflict.positions for ref in position.source_refs]
        invalid = [
            ref
            for ref in refs
            if bundle.source_metadata[ref].get("status")
            not in {"supported", "favored"}
            or (
                conflict.conflict_nature == "decision_relevant_discordance"
                and bundle.source_metadata[ref].get("role") != "primary"
            )
        ]
        if invalid:
            raise ValueError(
                f"conflicts[{index - 1}] requires assessable"
                f"{' preferred primary' if conflict.conflict_nature == 'decision_relevant_discordance' else ''} "
                f"specialty assessments: {invalid}"
            )
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
