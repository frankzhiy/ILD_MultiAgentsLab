import json

import pytest

from src.agents.mdt_chair.agent import (
    MDTChairAgent,
    build_chair_prompt_bundle,
    build_semantic_evidence_catalog,
    resolve_chair_references,
    resolve_semantic_ledger,
)
from src.agents.mdt_chair.models import (
    ChairSemanticLedger,
    MDTChairIntegration,
)
from src.agents.mdt_discussion.integration import (
    append_round_responses,
    reconcile_discussion_references,
    stabilize_integration_ids,
)
from src.agents.mdt_discussion.models import SpecialtyRoundResponse, SpecialtyTaskAnswer
from src.llm.base import LLMResponse


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
        "weakening": [],
        "discriminating": [],
        "background": [],
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
        "quote": "指南运行时不应进入主持人输入。",
    }


def outputs():
    labels = {
        "pulmonology": "当前临床资料支持纤维化性间质性肺病框架。",
        "thoracic_radiology": "未提供原始影像，具体形态模式不可评价。",
        "rheumatology": "现有资料不足以评价结缔组织病归因。",
        "pathology": "未提供病理材料，组织学模式不可评价。",
    }
    values = {}
    for specialty in SPECIALTIES:
        questions = []
        gaps = []
        if specialty == "pulmonology":
            questions = [{
                "target_specialty": "thoracic_radiology",
                "question": "请影像科解释现有文字描述能够支持到什么层级。",
                "why_it_matters": "限定影像结论层级。",
                "decision_unlocked": "区分影像回应与完整资料需求。",
                "related_evidence": [pointer()],
            }]
            gaps = [{
                "available_information": "仅有肺功能异常概述。",
                "missing_information": "完整肺功能原始数值和时间序列。",
                "why_it_matters": "影响进展评价。",
                "decision_unlocked": "完成纵向生理变化判断。",
                "related_evidence": [pointer()],
            }]
        if specialty == "pathology":
            questions = [{
                "target_specialty": "thoracic_radiology",
                "question": "请提供完整 HRCT 影像和病变分布信息。",
                "why_it_matters": "核对取材代表性。",
                "decision_unlocked": "评价取材与异常区域是否对应。",
                "related_evidence": [],
            }]
        values[specialty] = {
            "professional_conclusions": {
                "specialty_question": f"{specialty} 当前能回答什么？",
                "assessability": "partially_assessable",
                "conclusions": [{
                    "conclusion_id": f"{specialty}_1",
                    "role": "primary" if specialty == "pulmonology" else "scope_or_evaluability",
                    "conclusion_type": "working_diagnosis" if specialty == "pulmonology" else "assessability",
                    "statement": labels[specialty],
                    "status": "favored" if specialty == "pulmonology" else "not_assessable",
                    "medical_basis": "由当前病例资料形成。",
                    "decision_impact": "限定本轮讨论范围。",
                    "evidence": evidence_bundle(),
                    "guideline_evidence": [guideline()],
                    "limitations": ["仅限当前输入。"],
                }],
                "interspecialty_questions": questions,
                "evidence_gaps": gaps,
                "boundaries": ["不是最终 MDT 诊断。"],
            },
            "clinical_reasoning": {"must_not_enter_prompt": "内部推理不得输入主持人。"},
        }
    return values


def source_ref(bundle, specialty, source_type, occurrence=0):
    return [
        ref
        for ref, source in bundle.source_registry.items()
        if source.specialty == specialty and source.source_type == source_type
    ][occurrence]


def ledger_payload(bundle):
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    rheumatology = source_ref(bundle, "rheumatology", "native_conclusion")
    pathology = source_ref(bundle, "pathology", "native_conclusion")
    pulmonary_question = source_ref(bundle, "pulmonology", "native_question")
    pathology_data_request = source_ref(bundle, "pathology", "native_question")
    pulmonary_gap = source_ref(bundle, "pulmonology", "evidence_gap")
    return {
        "claim_groups": [
            {
                "label": "纤维化性 ILD 框架",
                "disposition": "integrated",
                "claims": [{
                    "source_ref": pulmonary,
                    "statement": "支持纤维化性 ILD 框架。",
                    "subject": "当前肺部疾病",
                    "dimension": "工作诊断层级",
                    "timeframe": "当前",
                    "evidence_scope": "现有临床资料",
                    "epistemic_status": "affirms",
                }],
            },
            {
                "label": "专科不可评价边界",
                "disposition": "boundary",
                "claims": [
                    {
                        "source_ref": ref,
                        "statement": "资料不足，不可评价。",
                        "subject": subject,
                        "dimension": "专业判断",
                        "timeframe": "当前",
                        "evidence_scope": "现有资料",
                        "epistemic_status": "not_assessable",
                    }
                    for ref, subject in (
                        (radiology, "影像模式"),
                        (rheumatology, "风湿归因"),
                        (pathology, "组织学模式"),
                    )
                ],
            },
        ],
        "question_routes": [
            {
                "source_refs": [pulmonary_question],
                "route": "question",
                "normalized_question": "现有影像观点可支持到什么层级？",
                "evidence_requirement": "",
                "target_specialties": ["thoracic_radiology"],
                "answer_links": [{
                    "specialty": "thoracic_radiology",
                    "source_refs": [radiology],
                    "relation": "evidence_boundary",
                }],
            },
            {
                "source_refs": [pathology_data_request],
                "route": "evidence_need",
                "normalized_question": "",
                "evidence_requirement": "完整 HRCT 影像和病变分布。",
                "target_specialties": ["thoracic_radiology"],
                "answer_links": [],
            },
        ],
        "evidence_need_groups": [
            {
                "source_refs": [pulmonary_gap],
                "required_information": "完整肺功能原始数值和时间序列。",
                "coverage_source_refs": [],
            },
            {
                "source_refs": [pathology_data_request],
                "required_information": "完整 HRCT 影像和病变分布。",
                "coverage_source_refs": [],
            },
        ],
    }


def integration_payload(bundle):
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    rheumatology = source_ref(bundle, "rheumatology", "native_conclusion")
    pathology = source_ref(bundle, "pathology", "native_conclusion")
    pulmonary_question = source_ref(bundle, "pulmonology", "native_question")
    pathology_data_request = source_ref(bundle, "pathology", "native_question")
    pulmonary_gap = source_ref(bundle, "pulmonology", "evidence_gap")
    return {
        "integrated_conclusions": [{
            "statement": "现有临床资料支持在纤维化性间质性肺病框架内继续讨论。",
            "medical_basis": "呼吸科形成了实体判断，其余专科的不可评价内容不作为支持者。",
            "decision_impact": "限定本轮可整合到疾病框架层级。",
            "role": "primary",
            "conclusion_type": "working_diagnosis",
            "status": "favored",
            "limitations": [],
            "source_refs": [pulmonary],
        }],
        "assessment_boundaries": [{
            "topic": "影像、风湿及病理判断边界",
            "scope": "other",
            "status": "not_assessable",
            "statement": "三科现有资料不足，相关专业层级不可评价。",
            "reason": "缺少原始影像、风湿评价资料和病理材料。",
            "decision_impact": "不能形成影像模式、风湿归因或组织学模式结论。",
            "related_evidence_need_source_refs": [pathology_data_request],
            "source_refs": [radiology, rheumatology, pathology],
        }],
        "conflicts": [],
        "questions": [{
            "question": "现有影像观点能够支持到什么层级？",
            "answers": [{
                "specialty": "thoracic_radiology",
                "relation": "evidence_boundary",
                "answer": "现有文字不足以形成具体影像模式判断。",
                "source_refs": [radiology],
            }],
            "resolution_status": "blocked_by_evidence",
            "answer_summary": "影像科已经回应其当前判断边界。",
            "remaining_clarification": "实体影像问题仍受资料限制。",
            "why_it_matters": "避免把回应误当作问题已经解决。",
            "decision_unlocked": "区分回应状态和解决状态。",
            "related_evidence_need_source_refs": [pathology_data_request],
            "source_refs": [pulmonary_question],
        }],
        "evidence_needs": [
            {
                "status": "missing",
                "required_information": "完整肺功能原始数值和时间序列。",
                "available_information": "仅有异常概述。",
                "remaining_information": "仍缺原始数值和对应时间点。",
                "why_it_matters": "影响进展评价。",
                "decision_unlocked": "完成纵向生理变化判断。",
                "source_refs": [pulmonary_gap],
            },
            {
                "status": "missing",
                "required_information": "完整 HRCT 影像和病变分布。",
                "available_information": "仅有不完整文字描述。",
                "remaining_information": "仍缺原始影像与完整分布。",
                "why_it_matters": "影响取材代表性和影像模式评价。",
                "decision_unlocked": "核对主要异常区域。",
                "source_refs": [pathology_data_request],
            },
        ],
    }


def resolved_ledger(bundle):
    return resolve_semantic_ledger(
        ChairSemanticLedger.model_validate(ledger_payload(bundle)), bundle
    )


def test_prompt_projection_excludes_internal_reasoning_and_runtime_guidelines():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    compact = json.dumps(bundle.prompt_input, ensure_ascii=False)
    assert "内部推理不得输入主持人" not in compact
    assert "指南运行时不应进入主持人输入" not in compact
    assert "guideline_evidence" not in compact
    assert "supporting" in compact
    assert any(bundle.source_guidelines.values())


def test_prompt_projection_labels_each_specialty_source_type():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    specialty = bundle.prompt_input["specialties"][0]
    assert {item["source_type"] for item in specialty["native_conclusions"]} == {
        "native_conclusion"
    }
    assert {item["source_type"] for item in specialty["native_questions"]} == {
        "native_question"
    }
    assert {item["source_type"] for item in specialty["evidence_needs"]} == {
        "evidence_gap"
    }


def test_llm_json_schemas_do_not_contain_program_generated_ids():
    schemas = json.dumps(
        [ChairSemanticLedger.model_json_schema(), MDTChairIntegration.model_json_schema()]
    )
    for field in (
        "claim_id", "topic_id", "route_id", "group_id", "conclusion_id",
        "boundary_id", "conflict_id", "question_id", "need_id", "case_id",
    ):
        assert f'"{field}"' not in schemas


def test_program_backfills_v5_ids_provenance_and_separates_boundaries():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    ledger = resolved_ledger(bundle)
    result = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(bundle)), bundle, ledger
    )
    assert result.schema_version == "mdt_chair.v5"
    assert result.case_id == "case-1"
    assert result.integrated_conclusions[0].conclusion_id == "IC001"
    assert result.integrated_conclusions[0].supporting_specialties == ["pulmonology"]
    assert result.integrated_conclusions[0].guideline_evidence
    assert result.assessment_boundaries[0].boundary_id == "B001"
    assert result.assessment_boundaries[0].specialties == [
        "thoracic_radiology", "rheumatology", "pathology"
    ]
    assert result.questions[0].question_id == "Q001"
    assert [item.need_id for item in result.evidence_needs] == ["EN001", "EN002"]


def test_assessment_boundary_accepts_unresolved_native_question_source():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = integration_payload(bundle)
    question_ref = source_ref(bundle, "pulmonology", "native_question")
    payload["assessment_boundaries"][0]["source_refs"] = [question_ref]

    result = resolve_chair_references(
        MDTChairIntegration.model_validate(payload), bundle, resolved_ledger(bundle)
    )

    boundary = result.assessment_boundaries[0]
    assert boundary.source_citations[0].source_type == "native_question"
    assert boundary.specialties == ["pulmonology"]


def test_discussion_program_rebuilds_question_answer_and_evidence_need_refs():
    initial_outputs = outputs()
    initial_bundle = build_chair_prompt_bundle("case-1", initial_outputs)
    previous = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(initial_bundle)),
        initial_bundle,
        resolved_ledger(initial_bundle),
    )
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-thoracic_radiology-A",
        task_id="R01-Q001-thoracic_radiology",
        issue_type="question",
        issue_id="Q001",
        answerability="partially_answered",
        answer="现有文字只能支持有限影像表型。",
        confidence="moderate",
        medical_basis="缺少原始影像。",
        changed_from_previous=True,
        remaining_limitation="仍需完整HRCT。",
    )
    responses = [SpecialtyRoundResponse(
        case_id="case-1",
        round_number=1,
        specialty="thoracic_radiology",
        answers=[answer],
    )]
    current_outputs = append_round_responses(initial_outputs, responses)
    current_bundle = build_chair_prompt_bundle("case-1", current_outputs)
    payload = integration_payload(current_bundle)
    payload["questions"][0]["source_refs"] = [
        source_ref(current_bundle, "pulmonology", "evidence_gap"),
        source_ref(current_bundle, "rheumatology", "native_conclusion"),
    ]
    payload["questions"][0]["answers"][0]["source_refs"] = [
        source_ref(current_bundle, "pulmonology", "evidence_gap")
    ]
    payload["evidence_needs"][0]["source_refs"] = [
        source_ref(current_bundle, "rheumatology", "native_conclusion")
    ]
    result = MDTChairIntegration.model_validate(payload)

    reconcile_discussion_references(result, previous, responses, current_bundle)
    result = resolve_chair_references(result, current_bundle)

    question = result.questions[0]
    assert {item.source_type for item in question.source_citations} == {"native_question"}
    assert question.answers[-1].answer == answer.answer
    assert {item.source_type for item in question.answers[-1].source_citations} == {
        "native_conclusion"
    }
    assert all(
        citation.source_type in {"native_question", "evidence_gap", "native_conclusion"}
        for need in result.evidence_needs
        for citation in need.source_citations
    )
    assert result.evidence_needs[0].required_information == (
        previous.evidence_needs[0].required_information
    )


def test_discussion_keeps_new_questions_and_evidence_needs_with_stable_ids():
    initial_outputs = outputs()
    initial_bundle = build_chair_prompt_bundle("case-1", initial_outputs)
    previous = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(initial_bundle)),
        initial_bundle,
        resolved_ledger(initial_bundle),
    )
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-thoracic_radiology-A",
        task_id="R01-Q001-thoracic_radiology",
        issue_type="question",
        issue_id="Q001",
        answerability="answered",
        answer="现有材料不能确认具体模式，但可确认有限纤维化表型。",
        confidence="moderate",
        medical_basis="现有资料只有影像文字摘要。",
        changed_from_previous=True,
        remaining_limitation="缺少可比原始影像。",
        new_questions=[{
            "target_specialty": "pulmonology",
            "question": "现有低氧程度能否由肺实质异常充分解释？",
            "why_it_matters": "限定肺实质异常的临床贡献。",
            "decision_unlocked": "决定是否需要并行考虑心肺血管因素。",
            "related_evidence": [],
        }],
        evidence_gaps=[{
            "available_information": "已有影像文字摘要。",
            "missing_information": "缺少可比原始HRCT。",
            "why_it_matters": "影响影像模式和进展判断。",
            "decision_unlocked": "提高影像判断确定性。",
            "related_evidence": [],
        }],
    )
    responses = [SpecialtyRoundResponse(
        case_id="case-1",
        round_number=1,
        specialty="thoracic_radiology",
        answers=[answer],
    )]
    current_outputs = append_round_responses(initial_outputs, responses)
    current_bundle = build_chair_prompt_bundle("case-1", current_outputs)
    payload = integration_payload(current_bundle)
    new_question_ref = next(
        ref
        for ref, item in current_bundle.source_registry.items()
        if item.source_type == "native_question"
        and item.quote == "现有低氧程度能否由肺实质异常充分解释？"
    )
    new_gap_ref = next(
        ref
        for ref, item in current_bundle.source_registry.items()
        if item.source_type == "evidence_gap"
        and item.quote == "缺少可比原始HRCT。"
    )
    payload["questions"].append({
        "question": "现有低氧程度能否由肺实质异常充分解释？",
        "answers": [],
        "resolution_status": "unresolved",
        "answer_summary": "尚无呼吸科会中回答。",
        "remaining_clarification": "请基于现有材料判断。",
        "why_it_matters": "限定肺实质异常的临床贡献。",
        "decision_unlocked": "决定是否需要并行考虑心肺血管因素。",
        "related_evidence_need_source_refs": [],
        "source_refs": [new_question_ref],
    })
    payload["evidence_needs"].append({
        "status": "missing",
        "required_information": "缺少可比原始HRCT。",
        "available_information": "已有影像文字摘要。",
        "remaining_information": "仍缺可比原始HRCT。",
        "why_it_matters": "影响影像模式和进展判断。",
        "decision_unlocked": "提高影像判断确定性。",
        "source_refs": [new_gap_ref],
    })
    result = MDTChairIntegration.model_validate(payload)

    reconcile_discussion_references(result, previous, responses, current_bundle)
    result = resolve_chair_references(result, current_bundle)
    result = stabilize_integration_ids(result, previous)

    assert result.questions[0].question_id == "Q001"
    assert result.questions[0].question == previous.questions[0].question
    assert result.questions[-1].question_id == "Q002"
    assert result.questions[-1].question == "现有低氧程度能否由肺实质异常充分解释？"
    assert result.evidence_needs[0].need_id == "EN001"
    assert any(
        need.required_information == "缺少可比原始HRCT。"
        for need in result.evidence_needs
    )


def test_question_response_and_resolution_are_independent():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    result = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(bundle)),
        bundle,
        resolved_ledger(bundle),
    )
    question = result.questions[0]
    assert question.response_status == "all_responded"
    assert question.resolution_status == "blocked_by_evidence"
    assert question.responded_by == ["thoracic_radiology"]
    assert question.awaiting_specialties == []
    assert question.related_evidence_need_ids == ["EN002"]


def test_reclassified_native_question_becomes_evidence_need_not_question():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    data_request = source_ref(bundle, "pathology", "native_question")
    result = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(bundle)),
        bundle,
        resolved_ledger(bundle),
    )
    assert all(data_request not in item.source_refs for item in result.questions)
    reclassified = next(
        item for item in result.evidence_needs if data_request in item.source_refs
    )
    assert reclassified.raised_by == ["pathology"]
    assert reclassified.provided_by == []


def test_empty_integrated_conclusions_is_a_valid_current_result():
    result = MDTChairIntegration.model_validate({
        "integrated_conclusions": [],
        "assessment_boundaries": [],
        "conflicts": [],
        "questions": [],
        "evidence_needs": [],
    })
    assert result.integrated_conclusions == []


def test_conflict_ids_specialties_links_and_status_are_program_backfilled():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = integration_payload(bundle)
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    question = source_ref(bundle, "pulmonology", "native_question")
    data_request = source_ref(bundle, "pathology", "native_question")
    payload["conflicts"] = [{
        "topic": "同一资料下的直接相反判断",
        "conflict_domain": "diagnostic_interpretation",
        "shared_claim": "当前资料直接确认命题 X。",
        "comparison_conditions": "同一对象、时间、资料和判断层级。",
        "positions": [
            {"stance": "affirms", "position": "直接肯定。", "source_refs": [pulmonary]},
            {"stance": "denies", "position": "直接否定。", "source_refs": [radiology]},
        ],
        "why_incompatible": "相同前提下不能同时成立。",
        "decision_impact": "阻止当前整合。",
        "resolution_requirement": "澄清既有观点并补足资料。",
        "related_question_source_refs": [question],
        "related_evidence_need_source_refs": [data_request],
    }]
    result = resolve_chair_references(
        MDTChairIntegration.model_validate(payload), bundle, resolved_ledger(bundle)
    )
    conflict = result.conflicts[0]
    assert conflict.conflict_id == "CF001"
    assert conflict.specialties == ["pulmonology", "thoracic_radiology"]
    assert conflict.related_question_ids == ["Q001"]
    assert conflict.related_evidence_need_ids == ["EN002"]
    assert conflict.status == "pending_clarification_and_evidence"


def test_unknown_source_id_is_rejected_without_medical_semantic_validator():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    payload["claim_groups"][0]["claims"][0]["source_ref"] = "S999"
    with pytest.raises(ValueError, match="unknown specialty source refs"):
        resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)


def test_integration_source_type_error_identifies_exact_field():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = integration_payload(bundle)
    payload["questions"][0]["source_refs"] = [
        source_ref(bundle, "pulmonology", "native_conclusion")
    ]

    with pytest.raises(ValueError, match=r"questions\[0\]\.source_refs"):
        resolve_chair_references(MDTChairIntegration.model_validate(payload), bundle)


def test_semantic_ledger_drops_known_gap_refs_from_answer_and_coverage_links():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    gap_ref = source_ref(bundle, "pulmonology", "evidence_gap")
    payload["question_routes"][0]["answer_links"][0]["source_refs"] = [gap_ref]
    payload["evidence_need_groups"][0]["coverage_source_refs"] = [gap_ref]

    ledger = resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)

    assert ledger.question_routes[0].answer_links == []
    assert ledger.evidence_need_groups[0].coverage_source_refs == []
    assert bundle.normalization_events == [
        {
            "context": "question_routes[0].answer_links[0].source_refs",
            "action": "dropped_incompatible_known_source_refs",
            "allowed_source_types": ["native_conclusion"],
            "dropped": [{"source_ref": gap_ref, "source_type": "evidence_gap"}],
        },
        {
            "context": "evidence_need_groups[0].coverage_source_refs",
            "action": "dropped_incompatible_known_source_refs",
            "allowed_source_types": ["native_conclusion"],
            "dropped": [{"source_ref": gap_ref, "source_type": "evidence_gap"}],
        },
    ]


def test_semantic_graph_catalog_repairs_specialty_locator():
    propositions = {
        "segments": [{
            "segment_id": "seg_001",
            "units": [{
                "graph_unit_id": "seg_001_gu_001",
                "evidence_blocks": [{
                    "evidence_id": "seg_001_gu_001_ev_001", "text": "规范原文。"
                }],
                "propositions": [{
                    "proposition_id": "prop_001",
                    "evidence": {
                        "evidence_ids": ["seg_001_gu_001_ev_001"],
                        "quote": "规范命题原文。",
                    },
                }],
            }],
        }]
    }
    graphs = {"segments": [{"units": [{
        "graph_unit_id": "seg_001_gu_001", "segment_id": "seg_001", "nodes": []
    }]}]}
    catalog = build_semantic_evidence_catalog(propositions, graphs)
    source = outputs()
    source["pulmonology"]["professional_conclusions"]["conclusions"][0]["evidence"]["supporting"][0].update(
        {"segment_id": "wrong", "quote": "wrong", "node_ids": []}
    )
    bundle = build_chair_prompt_bundle("case-1", source, semantic_evidence=catalog)
    evidence = next(iter(bundle.evidence_registry.values()))
    assert evidence.segment_id == "seg_001"
    assert evidence.quote == "规范原文。"
    assert evidence.evidence_ref == "seg_001_gu_001_ev_001"
    assert "E001" not in bundle.evidence_registry

    source_with_proposition = outputs()
    source_with_proposition["pulmonology"]["professional_conclusions"]["conclusions"][0]["evidence"]["supporting"][0]["proposition_ids"] = ["prop_001"]
    proposition_bundle = build_chair_prompt_bundle(
        "case-1",
        source_with_proposition,
        semantic_evidence=catalog,
    )
    proposition_evidence = next(iter(proposition_bundle.evidence_registry.values()))
    assert proposition_evidence.evidence_ref == "seg_001_gu_001::prop_001"
    assert proposition_evidence.proposition_ids == ["seg_001_gu_001::prop_001"]


def test_agent_uses_ledger_then_integration_structured_calls():
    bundle = build_chair_prompt_bundle("case-1", outputs())

    class FakeLLM:
        supports_json_schema = True

        def __init__(self):
            self.calls = []

        def complete(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            payload = ledger_payload(bundle) if len(self.calls) % 2 else integration_payload(bundle)
            return LLMResponse(
                content=json.dumps(payload, ensure_ascii=False),
                raw={"usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            )

    llm = FakeLLM()
    agent = MDTChairAgent(
        llm,
        ledger_prompt_path="src/prompts/mdt_chair/semantic_ledger.md",
        prompt_path="src/prompts/mdt_chair/initial_synthesis.md",
        max_attempts=1,
    )
    result, trace = agent.integrate(bundle)
    assert len(llm.calls) == 2
    assert result.integrated_conclusions[0].conclusion_id == "IC001"
    assert trace["semantic_ledger"]["claim_groups"][0]["topic_id"] == "T001"
    assert "topic_ledger_chars" in trace["prompt_components"]
    ledger_schema = llm.calls[0][1]["response_format"]["json_schema"]["schema"]
    integration_schema = llm.calls[1][1]["response_format"]["json_schema"]["schema"]
    assert ledger_schema["$defs"]["LedgerQuestionRoute"]["properties"][
        "source_refs"
    ]["items"]["enum"] == [
        source_ref(bundle, "pulmonology", "native_question"),
        source_ref(bundle, "pathology", "native_question"),
    ]
    assert integration_schema["$defs"]["IntegratedQuestion"]["properties"][
        "source_refs"
    ]["items"]["enum"] == [
        source_ref(bundle, "pulmonology", "native_question"),
    ]
    assert integration_schema["$defs"]["QuestionAnswer"]["properties"][
        "source_refs"
    ]["items"]["enum"] == [
        source_ref(bundle, "thoracic_radiology", "native_conclusion")
    ]

    alias_result, _ = agent.synthesize(bundle)
    assert len(llm.calls) == 4
    assert alias_result.questions[0].response_status == "all_responded"
