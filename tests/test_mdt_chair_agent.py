import json

import pytest

from src.agents.mdt_chair.agent import (
    MDTChairAgent,
    build_chair_prompt_bundle,
    build_semantic_evidence_catalog,
    resolve_chair_references,
)
from src.agents.mdt_chair.models import MDTChairSynthesis
from src.llm.base import LLMResponse
from src.llm.prompting import llm_value
from src.reporting.mdt_chair_report import render_mdt_chair_report


SPECIALTIES = (
    "pulmonology",
    "thoracic_radiology",
    "rheumatology",
    "pathology",
)


def pointer():
    return {
        "segment_id": "seg_001",
        "graph_unit_id": "seg_001_gu_001",
        "evidence_ids": ["seg_001_gu_001_ev_001"],
        "node_ids": ["seg_001_gu_001::prop_001"],
        "quote": "病例原文证据。",
    }


def outputs():
    common_review = [
        {"domain": "scope", "status": "partially_assessable", "rationale": "资料有限。"}
    ]
    return {
        "pulmonology": {
            "case_id": "case-1",
            "domain_reviews": common_review,
            "clinical_phenotype": {
                "assessment": "呼吸科结论。",
                "confidence": "moderate",
                "reasoning_summary": "依据病例资料。",
                "supporting_evidence": [pointer()],
            },
            "missing_data": [
                {
                    "available_information": "已有部分资料。",
                    "missing_information": "补充肺功能。",
                    "why_it_matters": "影响严重度判断。",
                    "decision_unlocked": "完成严重度评价。",
                }
            ],
        },
        "thoracic_radiology": {
            "case_id": "case-1",
            "review_coverage": common_review,
            "core_answer": {
                "answer": "影像科结论。",
                "confidence": "low",
                "reasoning_summary": "未直接阅片。",
                "supporting_evidence": [pointer()],
            },
            "specialist_questions": [],
        },
        "rheumatology": {
            "case_id": "case-1",
            "domain_reviews": common_review,
            "case_orientation": {
                "assessment": "风湿科结论。",
                "confidence": "low",
                "reasoning_summary": "表型不足。",
                "supporting_evidence": [pointer()],
            },
        },
        "pathology": {
            "case_id": "case-1",
            "domain_reviews": common_review,
            "source_assessment": {
                "assessment": "未提供病理材料。",
                "confidence": "high",
                "reasoning_summary": "不能评价组织学模式。",
            },
        },
    }


def synthesis(bundle):
    summaries = []
    for specialty in SPECIALTIES:
        item = next(
            value for value in bundle.prompt_input["specialties"] if value["specialty"] == specialty
        )
        source = item["native_conclusions"][0]
        evidence = source["supporting_evidence_refs"]
        summaries.append(
            {
                "specialty": specialty,
                "evaluation_scope": {
                    "summary": "评价范围摘要。",
                    "assessability": "partially_assessable",
                    "confidence": "moderate",
                    "source_refs": [item["evaluation"][0]["source_ref"]],
                    "evidence_refs": [],
                },
                "core_conclusions": [
                    {
                        "conclusion": source["text"],
                        "confidence": source["confidence"],
                        "source_refs": [source["source_ref"]],
                        "evidence_refs": evidence,
                    }
                ],
            }
        )
    return MDTChairSynthesis(specialty_summaries=summaries)


def test_prompt_projection_removes_large_repeated_fields():
    source = outputs()
    source["pulmonology"]["clinical_phenotype"]["guideline_evidence"] = [
        {"quote": "不应进入主持人输入" * 100}
    ]
    bundle = build_chair_prompt_bundle("case-1", source)
    compact = json.dumps(bundle.prompt_input, ensure_ascii=False)
    assert "不应进入主持人输入" not in compact
    assert "node_ids" not in compact
    assert compact.count("病例原文证据。") == 1


def test_reference_resolution_and_html(tmp_path):
    bundle = build_chair_prompt_bundle("case-1", outputs())
    result = resolve_chair_references(synthesis(bundle), bundle)
    assert result.case_id == "case-1"
    assert result.specialty_summaries[0].core_conclusions[0].source_citations
    assert result.specialty_summaries[0].core_conclusions[0].case_evidence
    path = render_mdt_chair_report(result, tmp_path / "chair.html")
    html = path.read_text(encoding="utf-8")
    assert "一、专科摘要" in html
    assert "病例原文证据" in html


def test_unknown_reference_is_rejected():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    result = synthesis(bundle)
    result.specialty_summaries[0].core_conclusions[0].source_refs = ["S999"]
    with pytest.raises(ValueError, match="Unknown specialty source refs"):
        resolve_chair_references(result, bundle)


def test_semantic_graph_catalog_repairs_specialty_locator():
    propositions = {
        "segments": [
            {
                "segment_id": "seg_001",
                "units": [
                    {
                        "graph_unit_id": "seg_001_gu_001",
                        "evidence_blocks": [
                            {"evidence_id": "seg_001_gu_001_ev_001", "text": "规范原文。"}
                        ],
                        "propositions": [
                            {
                                "proposition_id": "prop_001",
                                "evidence": {
                                    "evidence_ids": ["seg_001_gu_001_ev_001"],
                                    "quote": "规范原文",
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }
    graphs = {
        "segments": [
            {
                "units": [
                    {
                        "graph_unit_id": "seg_001_gu_001",
                        "segment_id": "seg_001",
                        "nodes": [
                            {
                                "node_id": "seg_001_gu_001::prop_001",
                                "evidence": {"evidence_ids": ["seg_001_gu_001_ev_001"]},
                            }
                        ],
                    }
                ]
            }
        ]
    }
    catalog = build_semantic_evidence_catalog(propositions, graphs)
    source = outputs()
    broken = source["pulmonology"]["clinical_phenotype"]["supporting_evidence"][0]
    broken.update({"segment_id": "wrong", "quote": "wrong", "node_ids": []})
    bundle = build_chair_prompt_bundle("case-1", source, semantic_evidence=catalog)
    evidence = next(iter(bundle.evidence_registry.values()))
    assert evidence.segment_id == "seg_001"
    assert evidence.quote == "规范原文。"
    assert evidence.node_ids == ["seg_001_gu_001::prop_001"]


def test_agent_uses_one_structured_llm_call_and_resolves_references():
    bundle = build_chair_prompt_bundle("case-1", outputs())

    class FakeLLM:
        supports_json_schema = True

        def __init__(self):
            self.calls = []

        def complete(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return LLMResponse(
                content=json.dumps(llm_value(synthesis(bundle)), ensure_ascii=False),
                raw={"usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            )

    llm = FakeLLM()
    agent = MDTChairAgent(
        llm,
        prompt_path="src/prompts/mdt_chair/initial_synthesis.md",
        max_attempts=1,
    )
    result, trace = agent.synthesize(bundle)
    assert len(llm.calls) == 1
    assert result.case_id == "case-1"
    assert result.specialty_summaries[0].core_conclusions[0].source_citations
    assert trace["prompt_components"]["evidence_reference_count"] == 1
