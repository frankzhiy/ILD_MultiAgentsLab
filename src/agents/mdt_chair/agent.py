"""Token-conscious first-pass synthesis of specialty-agent outputs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from src.agents.mdt_chair.models import (
    CaseEvidenceCitation,
    CitedChairStatement,
    MDTChairSynthesis,
    SpecialtySourceCitation,
)
from src.llm.base import LLMClient
from src.llm.prompting import prompt_json, prompt_schema_json
from src.llm.structured import StructuredLLMGenerator
from src.utils.config import load_text, load_yaml, render_template


SYSTEM_PROMPT = (
    "你是以呼吸科为主要背景的 ILD MDT 主持人。你只进行首轮专科意见压缩、"
    "跨专科冲突识别和待解决问题识别；不裁决冲突，不输出最终 MDT 诊断或治疗方案。"
    "所有面向人的文本使用简体中文，只返回符合 schema 的 JSON。"
)

SPECIALTIES = (
    "pulmonology",
    "thoracic_radiology",
    "rheumatology",
    "pathology",
)


@dataclass
class ChairPromptBundle:
    case_id: str
    prompt_input: dict[str, Any]
    source_registry: dict[str, SpecialtySourceCitation]
    evidence_registry: dict[str, CaseEvidenceCitation]


class _Registry:
    def __init__(self, semantic_evidence: dict[str, dict[str, Any]] | None = None) -> None:
        self.sources: dict[str, SpecialtySourceCitation] = {}
        self.evidence: dict[str, CaseEvidenceCitation] = {}
        self._source_keys: dict[tuple[str, str, str], str] = {}
        self._evidence_keys: dict[str, str] = {}
        self.semantic_evidence = semantic_evidence or {}
        self.evidence_to_unit = {
            evidence_id: unit_id
            for unit_id, unit in self.semantic_evidence.items()
            for evidence_id in unit.get("evidence_blocks", {})
        }

    def source(self, specialty: str, source_path: str, quote: str) -> str:
        key = (specialty, source_path, quote)
        if key not in self._source_keys:
            ref = f"S{len(self.sources) + 1:03d}"
            self._source_keys[key] = ref
            self.sources[ref] = SpecialtySourceCitation(
                source_ref=ref,
                specialty=specialty,
                source_path=source_path,
                quote=quote,
            )
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
    missing = sorted(set(SPECIALTIES) - set(specialty_outputs))
    if missing:
        raise ValueError(f"Missing specialty outputs: {missing}")
    registry = _Registry(semantic_evidence)
    compact = [
        _compact_specialty(
            specialty,
            specialty_outputs[specialty],
            (input_summaries or {}).get(specialty, {}),
            registry,
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
    return ChairPromptBundle(case_id, prompt_input, registry.sources, registry.evidence)


def _compact_specialty(
    specialty: str,
    output: dict[str, Any],
    input_summary: dict[str, Any],
    registry: _Registry,
) -> dict[str, Any]:
    reviews = output.get("domain_reviews") or output.get("review_coverage") or []
    evaluation = []
    for index, item in enumerate(reviews):
        path = f"domain_reviews[{index}]" if "domain_reviews" in output else f"review_coverage[{index}]"
        text = str(item.get("rationale") or "").strip()
        source_ref = registry.source(specialty, path, text)
        evaluation.append(
            {
                "source_ref": source_ref,
                "domain": item.get("domain"),
                "status": item.get("status"),
                "text": text,
            }
        )

    conclusions = []
    for path, label, item in _conclusion_items(specialty, output):
        if not isinstance(item, dict):
            continue
        text = _conclusion_text(item)
        reason = str(item.get("reasoning_summary") or "").strip()
        quote = "\n".join(part for part in (text, reason) if part)
        if not quote:
            continue
        refs = _evidence_refs(item, registry)
        conclusions.append(
            {
                "source_ref": registry.source(specialty, path, quote),
                "source_path": path,
                "label": label,
                "text": text,
                "status": _status(item),
                "confidence": item.get("confidence", "unknown"),
                "reason": reason,
                "supporting_evidence_refs": refs["supporting"],
                "conflicting_evidence_refs": refs["conflicting"],
                "context_evidence_refs": refs["context"],
            }
        )

    open_items = []
    for field, kind in (
        ("specialist_dependencies", "interspecialty_question"),
        ("specialist_questions", "interspecialty_question"),
        ("missing_data", "missing_case_material"),
        ("action_items", "specialty_self_issue"),
        ("reference_observations", "specialty_self_issue"),
    ):
        for index, item in enumerate(output.get(field) or []):
            path = f"{field}[{index}]"
            text = _open_item_text(item)
            refs = _evidence_refs(item, registry)
            open_items.append(
                {
                    "source_ref": registry.source(specialty, path, text),
                    "source_path": path,
                    "kind": kind,
                    "target_specialty": item.get("specialty"),
                    "text": text,
                    "why_it_matters": item.get("why_it_matters") or item.get("reason"),
                    "decision_unlocked": item.get("decision_unlocked"),
                    "evidence_refs": _ordered_unique(
                        refs["supporting"] + refs["conflicting"] + refs["context"]
                    ),
                }
            )

    return {
        "specialty": specialty,
        "input_unit_summary": {
            key: input_summary.get(key, 0)
            for key in (
                "owned_unit_count",
                "shared_context_unit_count",
                "reference_only_unit_count",
            )
        },
        "evaluation": evaluation,
        "native_conclusions": conclusions,
        "native_open_items": open_items,
    }


def _conclusion_items(
    specialty: str, output: dict[str, Any]
) -> Iterable[tuple[str, str, Any]]:
    if specialty == "pulmonology":
        fields = (
            ("clinical_phenotype", "临床表型"),
            ("pulmonary_severity", "肺部严重度"),
            ("bronchoscopy_assessment", "支气管镜评估"),
            ("progression_assessment", "进展评估"),
            ("diagnostic_formulation", "呼吸科诊断综合"),
        )
        for field, label in fields:
            yield field, label, output.get(field)
        for index, item in enumerate(output.get("secondary_cause_assessment") or []):
            yield f"secondary_cause_assessment[{index}]", "继发病因", item
    elif specialty == "rheumatology":
        for field, label in (
            ("case_orientation", "风湿病例定向"),
            ("rheumatic_disease_formulation", "风湿病诊断综合"),
            ("ild_attribution", "ILD风湿归因"),
            ("activity_and_risk", "活动性与风险"),
        ):
            yield field, label, output.get(field)
    elif specialty == "pathology":
        yield "source_assessment", "病理材料与来源", output.get("source_assessment")
        yield "pathology_formulation", "病理综合", output.get("pathology_formulation")
        for field, label in (
            ("pattern_assessments", "组织学模式"),
            ("etiologic_associations", "病因关联"),
        ):
            for index, item in enumerate(output.get(field) or []):
                yield f"{field}[{index}]", label, item
    else:
        yield "core_answer", "影像科核心回答", output.get("core_answer")
        for index, item in enumerate(output.get("task_assessments") or []):
            yield f"task_assessments[{index}]", "影像任务结论", item


def _conclusion_text(item: dict[str, Any]) -> str:
    for field in ("assessment", "conclusion", "answer", "formulation"):
        value = item.get(field)
        if value:
            return str(value).strip()
    if "leading_diagnosis" in item:
        diagnosis = item.get("leading_diagnosis") or "未形成主导诊断"
        return f"{item.get('classification_status', '未分类')}：{diagnosis}"
    if "ppf_status" in item:
        return (
            f"近期恶化={item.get('recent_worsening')}；"
            f"急性加重={item.get('acute_exacerbation_status')}；"
            f"PPF={item.get('ppf_status')}"
        )
    if "decision" in item:
        return f"{item.get('decision')}：{item.get('clinical_question', '')}"
    return ""


def _status(item: dict[str, Any]) -> str:
    for field in (
        "classification_status",
        "answerability",
        "status",
        "attribution_strength",
        "decision",
        "ppf_status",
    ):
        if item.get(field) is not None:
            return str(item[field])
    return "not_specified"


def _open_item_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("question"),
        item.get("missing_information"),
        item.get("action"),
        item.get("observation"),
    ]
    text = next((str(part).strip() for part in parts if part), "")
    available = item.get("available_information")
    if available:
        text = f"当前：{available}\n待解决：{text}"
    return text or "未提供具体问题文本"


def _evidence_refs(value: Any, registry: _Registry) -> dict[str, list[str]]:
    grouped = {"supporting": [], "conflicting": [], "context": []}
    field_roles = {
        "supporting_evidence": "supporting",
        "source_evidence": "supporting",
        "evidence": "supporting",
        "conflicting_evidence": "conflicting",
        "related_evidence": "context",
        "context_evidence": "context",
    }

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                role = field_roles.get(key)
                if role and isinstance(child, list):
                    for pointer in child:
                        if isinstance(pointer, dict) and (
                            pointer.get("graph_unit_id")
                            or pointer.get("evidence_ids")
                            or pointer.get("proposition_ids")
                        ):
                            grouped[role].append(registry.pointer(pointer))
                elif key != "guideline_evidence":
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return {key: _ordered_unique(refs) for key, refs in grouped.items()}


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


class MDTChairAgent:
    def __init__(
        self,
        llm: LLMClient,
        *,
        prompt_path: str | Path,
        temperature: float = 0.0,
        max_tokens: int = 9000,
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
            max_tokens=int(config.get("max_tokens", 9000)),
            max_attempts=int(config.get("max_attempts", 2)),
            retry_backoff_seconds=float(config.get("retry_backoff_seconds", 2)),
            event_callback=event_callback,
        )

    def synthesize(self, bundle: ChairPromptBundle) -> tuple[MDTChairSynthesis, dict]:
        compact_json = prompt_json(bundle.prompt_input)
        output_schema = (
            "由 API 的严格 JSON Schema response_format 提供。"
            if self.generator.response_format_mode == "json_schema"
            else prompt_schema_json(MDTChairSynthesis)
        )
        prompt = render_template(
            self.prompt,
            {"chair_input": compact_json, "output_schema": output_schema},
        )

        def validate(result: MDTChairSynthesis) -> MDTChairSynthesis:
            result.case_id = bundle.case_id
            return resolve_chair_references(result, bundle)

        result, trace = self.generator.generate(
            schema_model=MDTChairSynthesis,
            schema_name="mdt_chair_initial_synthesis",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            extra_validation=validate,
        )
        trace["prompt_components"] = {
            "total_chars": len(prompt),
            "chair_input_chars": len(compact_json),
            "output_schema_chars": len(output_schema),
            "source_reference_count": len(bundle.source_registry),
            "evidence_reference_count": len(bundle.evidence_registry),
        }
        return result, trace


def resolve_chair_references(
    result: MDTChairSynthesis,
    bundle: ChairPromptBundle,
) -> MDTChairSynthesis:
    result.case_id = bundle.case_id
    conflict_ids = {item.conflict_id for item in result.conflicts}
    for statement in _cited_statements(result):
        unknown_sources = set(statement.source_refs) - set(bundle.source_registry)
        unknown_evidence = set(statement.evidence_refs) - set(bundle.evidence_registry)
        if unknown_sources:
            raise ValueError(f"Unknown specialty source refs: {sorted(unknown_sources)}")
        if unknown_evidence:
            raise ValueError(f"Unknown case evidence refs: {sorted(unknown_evidence)}")
        statement.source_refs = _ordered_unique(statement.source_refs)
        statement.evidence_refs = _ordered_unique(statement.evidence_refs)
        statement.source_citations = [
            bundle.source_registry[ref] for ref in statement.source_refs
        ]
        statement.case_evidence = [
            bundle.evidence_registry[ref] for ref in statement.evidence_refs
        ]
    for issue in result.open_issues:
        unknown = set(issue.related_conflict_ids) - conflict_ids
        if unknown:
            raise ValueError(f"Open issue references unknown conflicts: {sorted(unknown)}")
    return result


def _cited_statements(value: Any) -> Iterable[CitedChairStatement]:
    if isinstance(value, CitedChairStatement):
        yield value
    if isinstance(value, BaseModel):
        for field in type(value).model_fields:
            yield from _cited_statements(getattr(value, field))
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _cited_statements(item)
