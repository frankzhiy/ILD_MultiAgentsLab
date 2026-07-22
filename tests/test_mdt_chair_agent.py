import json

import pytest

from src.agents.mdt_chair.agent import (
    MDTChairAgent,
    build_chair_prompt_bundle,
    build_semantic_evidence_catalog,
    resolve_chair_references,
)
from src.agents.mdt_chair.models import CrossSpecialtyConflict, MDTChairIntegration
from src.llm.base import LLMResponse
from src.llm.prompting import llm_value


SPECIALTIES = (
    "pulmonology",
    "thoracic_radiology",
    "rheumatology",
    "pathology",
)


def pointer(quote="病例原文证据。"):
    return {
        "segment_id": "seg_001",
        "graph_unit_id": "seg_001_gu_001",
        "evidence_ids": ["seg_001_gu_001_ev_001"],
        "node_ids": ["seg_001_gu_001::prop_001"],
        "quote": quote,
    }


def evidence_bundle():
    return {
        "supporting": [pointer()],
        "weakening": [pointer()],
        "discriminating": [pointer()],
        "background": [pointer()],
    }


def guideline():
    return {
        "chunk_id": "guideline_chunk_1",
        "relevance": "用于限定该判断。",
        "application": "适用于当前病例的证据边界。",
        "guideline_id": "guideline_1",
        "title": "ILD 指南",
        "organization": "Test",
        "year": 2025,
        "source_file": "guideline.pdf",
        "page": 1,
        "section_path": ["诊断"],
        "quote": "指南原文。",
    }


def outputs():
    values = {}
    labels = {
        "pulmonology": "呼吸科认为临床表现支持慢性纤维化性间质性肺病。",
        "thoracic_radiology": "影像科认为现有描述提示纤维化性改变，但未直接阅片。",
        "rheumatology": "风湿科认为当前结缔组织病归因证据不足。",
        "pathology": "病理科认为未提供材料，组织学模式不可评价。",
    }
    for specialty in SPECIALTIES:
        questions = []
        gaps = []
        if specialty == "pulmonology":
            questions = [
                {
                    "target_specialty": "thoracic_radiology",
                    "question": "请影像科解释现有影像描述支持何种模式，以及判断边界。",
                    "why_it_matters": "影响临床与影像结论的整合层级。",
                    "decision_unlocked": "明确现有影像观点可支持到何种程度。",
                    "related_evidence": [pointer()],
                }
            ]
            gaps = [
                {
                    "available_information": "已有肺功能异常的概括描述。",
                    "missing_information": "完整肺功能原始数值和时间序列。",
                    "why_it_matters": "影响进展与严重度评价。",
                    "decision_unlocked": "完成纵向生理变化判断。",
                    "related_evidence": [pointer()],
                }
            ]
        values[specialty] = {
            "professional_conclusions": {
                "specialty_question": f"{specialty} 当前能回答什么？",
                "assessability": "partially_assessable",
                "conclusions": [
                    {
                        "conclusion_id": f"{specialty}_conclusion_1",
                        "role": "primary",
                        "conclusion_type": "working_diagnosis",
                        "statement": labels[specialty],
                        "status": "favored",
                        "medical_basis": "由当前病例证据综合形成。",
                        "decision_impact": "限定本轮 MDT 判断范围。",
                        "evidence": evidence_bundle(),
                        "guideline_evidence": [guideline()],
                        "limitations": ["仅限当前输入。"],
                    }
                ],
                "interspecialty_questions": questions,
                "evidence_gaps": gaps,
                "boundaries": ["不是最终 MDT 诊断。"],
            },
            "clinical_reasoning": {"must_not_enter_prompt": "内部推理不得输入主持人。"},
        }
    return values


def source_ref(bundle, specialty, source_type):
    return next(
        ref
        for ref, source in bundle.source_registry.items()
        if source.specialty == specialty and source.source_type == source_type
    )


def integration_payload(bundle):
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    question = source_ref(bundle, "pulmonology", "native_question")
    gap = source_ref(bundle, "pulmonology", "evidence_gap")
    return {
        "integrated_conclusions": [
            {
                "conclusion_id": "integrated_1",
                "statement": "综合临床表现和当前影像文字描述，病例可被限定为纤维化性间质性肺病框架，但由于影像科未直接阅片，具体形态学模式仍不能据此确认。",
                "medical_basis": "临床与影像意见在纤维化框架上相互支持，同时受影像资料层级限制。",
                "decision_impact": "可继续围绕纤维化性 ILD 讨论，但不能越级形成影像模式结论。",
                "role": "primary",
                "conclusion_type": "working_diagnosis",
                "status": "favored",
                "limitations": ["影像科未直接阅片。"],
                "source_refs": [pulmonary, radiology],
            }
        ],
        "conflicts": [],
        "questions": [
            {
                "question_id": "question_1",
                "question": "现有影像观点能够支持到纤维化框架还是具体模式层级？",
                "answers": [
                    {
                        "specialty": "thoracic_radiology",
                        "answer": "当前只能支持纤维化性改变，不能确认具体模式。",
                        "source_refs": [radiology],
                    }
                ],
                "status": "answered",
                "answer_summary": "影像科现有正式结论已限定其可回答层级。",
                "remaining_clarification": "无",
                "why_it_matters": "避免把文字描述升级为直接阅片结论。",
                "decision_unlocked": "明确本轮整合的影像证据边界。",
                "source_refs": [question],
            }
        ],
        "evidence_needs": [
            {
                "need_id": "need_1",
                "status": "partially_available",
                "required_information": "用于纵向评价的完整肺功能数值和时间序列。",
                "available_information": "已有肺功能异常的概括描述。",
                "remaining_information": "缺少可核对的原始数值与时间点。",
                "provided_by": ["pulmonology"],
                "why_it_matters": "影响进展与严重度评价。",
                "decision_unlocked": "完成纵向生理变化判断。",
                "source_refs": [gap, pulmonary],
            }
        ],
    }


def integration(bundle):
    return MDTChairIntegration.model_validate(integration_payload(bundle))


def test_prompt_projection_uses_only_formal_conclusions_and_preserves_roles():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    compact = json.dumps(bundle.prompt_input, ensure_ascii=False)
    assert "内部推理不得输入主持人" not in compact
    assert all(name in compact for name in ("supporting", "weakening", "discriminating", "background"))
    assert "指南原文" in compact
    assert compact.count("病例原文证据。") == 1
    assert set(bundle.prompt_input["specialties"][0]) == {
        "specialty",
        "specialty_question",
        "assessability",
        "boundaries",
        "native_conclusions",
        "native_questions",
        "evidence_needs",
    }


def test_reference_resolution_backfills_all_evidence_roles_and_guidelines():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    result = resolve_chair_references(integration(bundle), bundle)
    conclusion = result.integrated_conclusions[0]
    assert conclusion.specialties == ["pulmonology", "thoracic_radiology"]
    assert all(getattr(conclusion.evidence, role) for role in ("supporting", "weakening", "discriminating", "background"))
    assert len(conclusion.guideline_evidence) == 1
    assert conclusion.limitations == ["仅限当前输入。"]
    question = result.questions[0]
    assert question.raised_by == ["pulmonology"]
    assert question.target_specialties == ["thoracic_radiology"]
    assert question.status == "answered"
    assert question.answers[0].source_citations[0].specialty == "thoracic_radiology"
    assert result.evidence_needs[0].raised_by == ["pulmonology"]
    assert result.evidence_needs[0].provided_by == ["pulmonology"]


def test_conflict_positions_keep_each_specialtys_evidence_and_linked_question():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    result = integration(bundle)
    result.conflicts = [
        CrossSpecialtyConflict.model_validate({
            "conflict_id": "conflict_1",
            "topic": "现有影像文字能否支持具体形态模式",
            "conflict_domain": "morphologic_interpretation",
            "status": "pending_clarification",
            "shared_claim": "现有影像文字已经足以确认具体形态模式。",
            "comparison_conditions": "基于当前同一批影像文字资料，不引入原始图像。",
            "positions": [
                {
                    "specialty": "pulmonology",
                    "stance": "affirms",
                    "position": "临床整合倾向将现有资料纳入纤维化性 ILD 工作诊断。",
                    "source_refs": [pulmonary],
                },
                {
                    "specialty": "thoracic_radiology",
                    "stance": "denies",
                    "position": "未直接阅片时不能将文字描述升级为具体形态模式。",
                    "source_refs": [radiology],
                },
            ],
            "why_incompatible": "两项立场针对同一影像资料可支持的形态层级，不能同时作为已确认模式使用。",
            "decision_impact": "本轮不能将具体形态模式作为已整合结论。",
            "resolution_requirement": "需由影像科澄清现有文字描述的可解释范围。",
            "related_question_ids": ["question_1"],
            "related_evidence_need_ids": [],
        })
    ]

    resolved = resolve_chair_references(result, bundle)
    conflict = resolved.conflicts[0]
    assert conflict.specialties == ["pulmonology", "thoracic_radiology"]
    assert conflict.positions[0].source_citations[0].specialty == "pulmonology"
    assert conflict.positions[1].source_citations[0].specialty == "thoracic_radiology"
    assert conflict.positions[0].evidence.supporting
    assert conflict.related_question_ids == ["question_1"]


def test_conflict_rejects_cross_specialty_position_sources():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    result = integration(bundle)
    result.conflicts = [
        CrossSpecialtyConflict.model_validate({
            "conflict_id": "conflict_1",
            "topic": "冲突主题",
            "conflict_domain": "diagnostic_interpretation",
            "status": "unresolved",
            "shared_claim": "共同命题。",
            "comparison_conditions": "共同条件。",
            "positions": [
                {
                    "specialty": "pulmonology",
                    "stance": "affirms",
                    "position": "呼吸科立场。",
                    "source_refs": [radiology],
                },
                {
                    "specialty": "thoracic_radiology",
                    "stance": "denies",
                    "position": "影像科立场。",
                    "source_refs": [pulmonary],
                },
            ],
            "why_incompatible": "同一前提下不兼容。",
            "decision_impact": "限制整合。",
            "resolution_requirement": "需要澄清。",
            "related_question_ids": [],
            "related_evidence_need_ids": [],
        })
    ]
    with pytest.raises(ValueError, match="only native conclusions from its specialty"):
        resolve_chair_references(result, bundle)


def test_conflict_status_must_match_linked_resolution_items():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    result = integration(bundle)
    result.conflicts = [
        CrossSpecialtyConflict.model_validate({
            "conflict_id": "conflict_1",
            "topic": "冲突主题",
            "conflict_domain": "diagnostic_interpretation",
            "status": "unresolved",
            "shared_claim": "共同命题。",
            "comparison_conditions": "共同条件。",
            "positions": [
                {"specialty": "pulmonology", "stance": "affirms", "position": "呼吸科立场。", "source_refs": [pulmonary]},
                {"specialty": "thoracic_radiology", "stance": "denies", "position": "影像科立场。", "source_refs": [radiology]},
            ],
            "why_incompatible": "同一前提下不兼容。",
            "decision_impact": "限制整合。",
            "resolution_requirement": "需要澄清。",
            "related_question_ids": ["question_1"],
            "related_evidence_need_ids": [],
        })
    ]
    with pytest.raises(ValueError, match="status must be pending_clarification"):
        resolve_chair_references(result, bundle)


def test_conflict_requires_directly_opposing_positions_not_two_uncertain_views():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    result = integration(bundle)
    result.conflicts = [
        CrossSpecialtyConflict.model_validate({
            "conflict_id": "conflict_1",
            "topic": "冲突主题",
            "conflict_domain": "severity_or_trajectory",
            "status": "unresolved",
            "shared_claim": "当前资料足以确认影像学进展。",
            "comparison_conditions": "基于当前同一批可比影像资料。",
            "positions": [
                {"specialty": "pulmonology", "stance": "affirms", "position": "呼吸科认为尚不能确认。", "source_refs": [pulmonary]},
                {"specialty": "thoracic_radiology", "stance": "affirms", "position": "影像科认为尚不能确认。", "source_refs": [radiology]},
            ],
            "why_incompatible": "同一前提下不兼容。",
            "decision_impact": "限制整合。",
            "resolution_requirement": "需要澄清。",
            "related_question_ids": [],
            "related_evidence_need_ids": [],
        })
    ]
    with pytest.raises(ValueError, match="requires both an affirming and a denying position"):
        resolve_chair_references(result, bundle)


def test_reference_resolution_accepts_verbatim_integrated_statement():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    result = integration(bundle)
    source = result.integrated_conclusions[0].source_refs[0]
    result.integrated_conclusions[0].statement = (
        f"  {bundle.source_metadata[source]['original_statement']}  "
    )
    resolved = resolve_chair_references(result, bundle)
    assert resolved.integrated_conclusions[0].source_citations


def test_question_and_need_sources_are_projected_without_relaxing_answer_sources():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    native_question = source_ref(bundle, "pulmonology", "native_question")
    native_conclusion = source_ref(bundle, "pulmonology", "native_conclusion")

    mixed_question = integration(bundle)
    mixed_question.questions[0].question = "主持人扩展的新问题。"
    mixed_question.questions[0].source_refs = [
        native_question,
        native_conclusion,
    ]
    resolved = resolve_chair_references(mixed_question, bundle)
    assert resolved.questions[0].source_refs == [native_question]
    assert resolved.questions[0].question == bundle.source_registry[native_question].quote

    synthetic_question = integration(bundle)
    synthetic_question.questions[0].source_refs = [native_conclusion]
    assert resolve_chair_references(synthetic_question, bundle).questions == []

    wrong_answer = integration(bundle)
    wrong_answer.questions[0].answers[0].source_refs = [
        source_ref(bundle, "pulmonology", "native_conclusion")
    ]
    with pytest.raises(ValueError, match="must cite that specialty"):
        resolve_chair_references(wrong_answer, bundle)

    gap = source_ref(bundle, "pulmonology", "evidence_gap")
    mixed_need = integration(bundle)
    mixed_need.evidence_needs[0].required_information = "主持人扩展的新需求。"
    mixed_need.evidence_needs[0].source_refs = [
        gap,
        native_question,
        native_conclusion,
    ]
    resolved = resolve_chair_references(mixed_need, bundle)
    assert resolved.evidence_needs[0].source_refs == [gap, native_conclusion]
    assert resolved.evidence_needs[0].required_information == bundle.source_registry[gap].quote

    synthetic_need = integration(bundle)
    synthetic_need.evidence_needs[0].source_refs = [native_conclusion]
    assert resolve_chair_references(synthetic_need, bundle).evidence_needs == []


def test_question_status_is_recomputed_from_valid_native_answers():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    result = integration(bundle)
    result.questions[0].answers = []
    result.questions[0].status = "answered"
    resolved = resolve_chair_references(result, bundle)
    assert resolved.questions[0].status == "unanswered"


def test_question_accepts_multiple_answers_from_one_specialty():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    result = integration(bundle)
    result.questions[0].answers.append(result.questions[0].answers[0].model_copy())
    resolved = resolve_chair_references(result, bundle)
    assert len(resolved.questions[0].answers) == 2
    assert resolved.questions[0].status == "answered"


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
                        "propositions": [],
                    }
                ],
            }
        ]
    }
    graphs = {"segments": [{"units": [{"graph_unit_id": "seg_001_gu_001", "segment_id": "seg_001", "nodes": []}]}]}
    catalog = build_semantic_evidence_catalog(propositions, graphs)
    source = outputs()
    source["pulmonology"]["professional_conclusions"]["conclusions"][0]["evidence"]["supporting"][0].update(
        {"segment_id": "wrong", "quote": "wrong", "node_ids": []}
    )
    bundle = build_chair_prompt_bundle("case-1", source, semantic_evidence=catalog)
    evidence = next(iter(bundle.evidence_registry.values()))
    assert evidence.segment_id == "seg_001"
    assert evidence.quote == "规范原文。"


def test_agent_integrate_and_synthesize_alias_use_one_structured_call():
    bundle = build_chair_prompt_bundle("case-1", outputs())

    class FakeLLM:
        supports_json_schema = True

        def __init__(self):
            self.calls = []

        def complete(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            return LLMResponse(
                content=json.dumps(llm_value(integration(bundle)), ensure_ascii=False),
                raw={"usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            )

    llm = FakeLLM()
    agent = MDTChairAgent(
        llm,
        prompt_path="src/prompts/mdt_chair/initial_synthesis.md",
        max_attempts=1,
    )
    result, trace = agent.integrate(bundle)
    assert len(llm.calls) == 1
    assert result.integrated_conclusions[0].source_citations
    assert trace["prompt_components"]["evidence_reference_count"] == 1
    alias_result, _ = agent.synthesize(bundle)
    assert len(llm.calls) == 2
    assert alias_result.questions[0].status == "answered"
