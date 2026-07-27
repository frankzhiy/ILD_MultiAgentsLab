import json

import pytest

from src.agents.mdt_chair.agent import (
    MDTChairAgent,
    _discussion_previous_view,
    _materialize_evidence_need_conversion_refs,
    _validate_review_destinations,
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
    apply_review_outcomes,
    append_round_responses,
    reconcile_discussion_references,
)
from src.agents.mdt_discussion.models import (
    SpecialtyAnswerReview,
    SpecialtyRoundResponse,
    SpecialtyTaskAnswer,
)
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
    source_type = {
        "native_conclusion": "specialty_assessment",
        "native_question": "interspecialty_question",
        "evidence_gap": "assessment_evidence_need",
    }.get(source_type, source_type)
    return [
        ref
        for ref, source in bundle.source_registry.items()
        if source.specialty == specialty and source.source_type == source_type
    ][occurrence]


def assessable_conflict_outputs():
    values = outputs()
    radiology = values["thoracic_radiology"]["professional_conclusions"][
        "conclusions"
    ][0]
    radiology.update({
        "role": "primary",
        "conclusion_type": "morphologic_pattern",
        "status": "favored",
        "statement": "现有影像首选形态解释为模式 B。",
    })
    return values


def conflict_ledger_payload(bundle, nature, claims):
    payload = ledger_payload(bundle)
    boundary_group = payload["claim_groups"][1]
    payload["claim_groups"] = [{
        "label": "需要跨专科协调的判断",
        "disposition": "conflict",
        "conflict_nature": nature,
        "comparison_target": "当前主要诊断或形态解释",
        "comparison_conditions": "当前时点、现有资料和可比较的判断层级。",
        "why_incompatible": "两项判断不能同时作为当前首选。",
        "decision_impact": "影响诊断信度和下一步检查路径。",
        "claims": claims,
    }, boundary_group]
    return payload


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
                    "professional_level": "disease_diagnosis",
                    "position_role": "preferred",
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
                        "professional_level": "assessability",
                        "position_role": "boundary",
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
                "decision_role": "non_blocking_refinement",
                "coverage_source_refs": [],
            },
            {
                "source_refs": [pathology_data_request],
                "required_information": "完整 HRCT 影像和病变分布。",
                "decision_role": "non_blocking_refinement",
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
            "resolution_status": "partially_resolved",
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
    assert {item["source_type"] for item in specialty["specialty_assessments"]} == {
        "specialty_assessment"
    }
    assert {item["source_type"] for item in specialty["interspecialty_questions"]} == {
        "interspecialty_question"
    }
    assert {item["source_type"] for item in specialty["evidence_needs"]} == {
        "assessment_evidence_need"
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
    assert result.schema_version == "mdt_chair.v8"
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
    assert boundary.source_citations[0].source_type == "interspecialty_question"
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
    reviews = [SpecialtyAnswerReview(
        review_id=f"{answer.answer_id}-RV-pulmonology",
        issue_id=answer.issue_id,
        answer_id=answer.answer_id,
        reviewer_specialty="pulmonology",
        outcome="accept_answer",
        rationale="回答已覆盖原问题。",
    )]
    current_outputs = append_round_responses(initial_outputs, responses, reviews)
    current_bundle = build_chair_prompt_bundle(
        "case-1", current_outputs, source_seed=initial_bundle
    )
    previous_view, preserved = _discussion_previous_view(previous, current_bundle)
    stable_question_ref = previous_view["questions"][0]["source_refs"][0]
    assert stable_question_ref == previous.questions[0].source_refs[0]
    assert stable_question_ref in current_bundle.source_registry
    assert current_bundle.source_registry[stable_question_ref].source_type == (
        "interspecialty_question"
    )
    assert stable_question_ref in preserved["IntegratedQuestion"]["source_refs"]
    payload = integration_payload(current_bundle)
    payload["questions"][0]["source_refs"] = [
        source_ref(current_bundle, "pulmonology", "evidence_gap"),
        source_ref(current_bundle, "rheumatology", "native_conclusion"),
    ]
    payload["questions"][0]["question"] = "主持人本轮生成了语义差异很大的问题文本。"
    payload["questions"][0]["answers"][0]["source_refs"] = [
        source_ref(current_bundle, "pulmonology", "evidence_gap")
    ]
    payload["evidence_needs"][0]["source_refs"] = [
        source_ref(current_bundle, "rheumatology", "native_conclusion")
    ]
    result = MDTChairIntegration.model_validate(payload)

    reconcile_discussion_references(result, previous, responses, current_bundle, reviews)
    result = resolve_chair_references(result, current_bundle)

    question = result.questions[0]
    assert {item.source_type for item in question.source_citations} == {
        "interspecialty_question"
    }
    assert {
        current_bundle.source_registry[ref].source_type
        for ref in question.related_evidence_need_source_refs
    } <= {"interspecialty_question", "assessment_evidence_need"}
    assert all(
        len({citation.specialty for citation in item.source_citations}) == 1
        for item in question.answers
    )
    assert question.answers[-1].answer == answer.answer
    assert {item.source_type for item in question.answers[-1].source_citations} == {
        "specialty_assessment"
    }
    assert all(
        citation.source_type
        in {
            "interspecialty_question",
            "assessment_evidence_need",
            "specialty_assessment",
        }
        for need in result.evidence_needs
        for citation in need.source_citations
    )
    assert result.evidence_needs[0].required_information == (
        previous.evidence_needs[0].required_information
    )


def test_discussion_bundle_does_not_reclassify_initial_questions():
    initial_outputs = outputs()
    initial_bundle = build_chair_prompt_bundle("case-1", initial_outputs)
    discussion_bundle = build_chair_prompt_bundle(
        "case-1",
        initial_outputs,
        discussion_round=1,
        source_seed=initial_bundle,
    )

    initial_question_refs = {
        ref
        for ref, source in initial_bundle.source_registry.items()
        if source.source_type == "interspecialty_question"
    }
    registered_discussion_refs = {
        ref
        for ref, source in discussion_bundle.source_registry.items()
        if source.source_type == "interspecialty_question"
    }

    assert registered_discussion_refs == initial_question_refs
    assert discussion_bundle.question_refs_to_classify == set()
    assert all(
        specialty["interspecialty_questions"] == []
        for specialty in discussion_bundle.prompt_input["specialties"]
    )
    resolve_semantic_ledger(ChairSemanticLedger(), discussion_bundle)


def test_discussion_source_refs_are_append_only_across_rounds():
    initial_outputs = outputs()
    initial_bundle = build_chair_prompt_bundle("case-1", initial_outputs)
    old_questions = {
        item.quote: ref
        for ref, item in initial_bundle.source_registry.items()
        if item.source_type == "interspecialty_question"
    }
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-pulmonology-A",
        task_id="R01-Q001-pulmonology",
        issue_type="question",
        issue_id="Q001",
        answerability="answered",
        answer="基于现有资料完成回答。",
        confidence="moderate",
        medical_basis="现有资料足以形成有限判断。",
        changed_from_previous=True,
    )
    cumulative = append_round_responses(
        initial_outputs,
        [SpecialtyRoundResponse(
            case_id="case-1",
            round_number=1,
            specialty="pulmonology",
            answers=[answer],
        )],
        [],
    )

    current = build_chair_prompt_bundle(
        "case-1",
        cumulative,
        discussion_round=1,
        source_seed=initial_bundle,
    )

    assert {
        item.quote: ref
        for ref, item in current.source_registry.items()
        if item.source_type == "interspecialty_question"
    } == old_questions
    answer_ref = next(
        ref
        for ref, metadata in current.source_metadata.items()
        if metadata.get("assessment_id") == answer.answer_id
    )
    assert int(answer_ref[1:]) > max(int(ref[1:]) for ref in old_questions.values())


def test_discussion_ignores_llm_reclassification_of_stable_questions():
    bundle = build_chair_prompt_bundle("case-1", outputs(), discussion_round=1)
    prior_refs = {
        ref
        for ref, item in bundle.source_registry.items()
        if item.source_type == "interspecialty_question"
    }
    bundle.already_classified_question_refs = prior_refs
    payload = ledger_payload(build_chair_prompt_bundle("case-1", outputs()))

    result = resolve_semantic_ledger(
        ChairSemanticLedger.model_validate(payload),
        bundle,
    )

    assert result.question_routes == []
    assert any(
        event["action"] == "ignored_already_classified_questions"
        for event in bundle.normalization_events
    )


def test_discussion_bundle_classifies_new_candidates_only_in_their_round():
    initial_outputs = outputs()
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-thoracic_radiology-A",
        task_id="R01-Q001-thoracic_radiology",
        issue_type="question",
        issue_id="Q001",
        answerability="answered",
        answer="形成新的有限影像判断。",
        confidence="moderate",
        medical_basis="基于现有材料。",
        changed_from_previous=True,
        new_questions=[{
            "target_specialty": "pulmonology",
            "question": "现有低氧是否能由肺实质异常充分解释？",
            "why_it_matters": "限定低氧归因。",
            "decision_unlocked": "决定是否并行评估其他机制。",
            "related_evidence": [],
        }],
        evidence_gaps=[{
            "available_information": "已有文字摘要。",
            "missing_information": "缺少原始HRCT。",
            "why_it_matters": "影响影像判断。",
            "decision_unlocked": "可完成形态评价。",
            "related_evidence": [],
        }],
    )
    responses = [SpecialtyRoundResponse(
        case_id="case-1",
        round_number=1,
        specialty="thoracic_radiology",
        answers=[answer],
    )]
    reviews = [SpecialtyAnswerReview(
        review_id=f"{answer.answer_id}-RV-pulmonology",
        issue_id=answer.issue_id,
        answer_id=answer.answer_id,
        reviewer_specialty="pulmonology",
        outcome="convert_to_evidence_need",
        rationale="当前判断已成立，原始影像只用于进一步明确。",
        evidence_gap={
            "available_information": "已有文字摘要。",
            "missing_information": "缺少原始HRCT。",
            "why_it_matters": "可进一步提高影像判断的明确度。",
            "decision_unlocked": "进一步明确形态评价。",
            "related_evidence": [],
        },
    )]
    cumulative = append_round_responses(initial_outputs, responses, reviews)

    first_round = build_chair_prompt_bundle(
        "case-1",
        cumulative,
        discussion_round=1,
    )
    second_round = build_chair_prompt_bundle(
        "case-1",
        cumulative,
        discussion_round=2,
    )

    assert len(first_round.question_refs_to_classify) == 0
    assert len(first_round.evidence_need_refs_to_classify) == 1
    assert all(
        not specialty["interspecialty_questions"]
        for specialty in first_round.prompt_input["specialties"]
    )
    assert second_round.question_refs_to_classify == set()
    assert second_round.evidence_need_refs_to_classify == set()


def test_discussion_agent_updates_previous_issues_without_reclassifying_them():
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
    )
    responses = [SpecialtyRoundResponse(
        case_id="case-1",
        round_number=1,
        specialty="thoracic_radiology",
        answers=[answer],
    )]
    reviews = [SpecialtyAnswerReview(
        review_id=f"{answer.answer_id}-RV-pulmonology",
        issue_id=answer.issue_id,
        answer_id=answer.answer_id,
        reviewer_specialty="pulmonology",
        outcome="request_clarification",
        rationale="仍需限定可支持到什么层级。",
    )]
    current_outputs = append_round_responses(initial_outputs, responses, reviews)
    bundle = build_chair_prompt_bundle(
        "case-1",
        current_outputs,
        discussion_round=1,
        source_seed=initial_bundle,
    )
    ledger_value = ledger_payload(bundle)
    ledger_value["question_routes"] = []
    ledger_value["evidence_need_groups"] = []

    class FakeLLM:
        supports_json_schema = True

        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            payload = ledger_value if self.calls == 1 else integration_payload(bundle)
            return LLMResponse(
                content=json.dumps(payload, ensure_ascii=False),
                raw={"usage": {}},
            )

    agent = MDTChairAgent(
        FakeLLM(),
        ledger_prompt_path="src/prompts/mdt_chair/semantic_ledger.md",
        prompt_path="src/prompts/mdt_chair/initial_synthesis.md",
        max_attempts=1,
    )
    result, trace = agent.integrate(
        bundle,
        discussion_previous=previous,
        discussion_responses=responses,
        discussion_reviews=reviews,
    )

    assert trace["semantic_ledger"]["question_routes"] == []
    assert result.questions[0].question_id == "Q001"
    assert result.questions[0].answers[-1].answer == answer.answer


def test_discussion_accept_boundary_moves_stable_question_to_boundary():
    initial_outputs = outputs()
    initial_bundle = build_chair_prompt_bundle("case-1", initial_outputs)
    previous = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(initial_bundle)),
        initial_bundle,
        resolved_ledger(initial_bundle),
    )
    question = previous.questions[0]
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-thoracic_radiology-A",
        task_id="R01-Q001-thoracic_radiology",
        issue_type="question",
        issue_id=question.question_id,
        answerability="partially_answered",
        answer="现有资料能够形成判断边界，但不能完成实体判断。",
        confidence="moderate",
        medical_basis="缺少关键原始影像。",
        changed_from_previous=True,
        remaining_limitation="没有原始影像便不能完成模式判断。",
    )
    responses = [SpecialtyRoundResponse(
        case_id="case-1",
        round_number=1,
        specialty="thoracic_radiology",
        answers=[answer],
    )]
    reviews = [SpecialtyAnswerReview(
        review_id=f"{answer.answer_id}-RV-pulmonology",
        issue_id=question.question_id,
        answer_id=answer.answer_id,
        reviewer_specialty="pulmonology",
        outcome="accept_boundary",
        rationale="没有关键影像便不能完成该判断。",
    )]
    current_outputs = append_round_responses(initial_outputs, responses, reviews)
    bundle = build_chair_prompt_bundle(
        "case-1",
        current_outputs,
        discussion_round=1,
        source_seed=initial_bundle,
    )
    ledger_value = ledger_payload(bundle)
    ledger_value["question_routes"] = []
    ledger_value["evidence_need_groups"] = []
    integration_value = integration_payload(bundle)

    class FakeLLM:
        supports_json_schema = True

        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            value = ledger_value if self.calls == 1 else integration_value
            return LLMResponse(content=json.dumps(value, ensure_ascii=False), raw={})

    result, _ = MDTChairAgent(
        FakeLLM(),
        ledger_prompt_path="src/prompts/mdt_chair/semantic_ledger.md",
        prompt_path="src/prompts/mdt_chair/initial_synthesis.md",
        max_attempts=1,
    ).integrate(
        bundle,
        discussion_previous=previous,
        discussion_responses=responses,
        discussion_reviews=reviews,
    )
    apply_review_outcomes(result, reviews)

    assert question.source_refs == previous.questions[0].source_refs
    assert any(
        set(question.source_refs).intersection(boundary.source_refs)
        for boundary in result.assessment_boundaries
    )
    assert result.questions == []


def test_discussion_projects_only_requester_approved_evidence_need():
    initial_outputs = outputs()
    initial_bundle = build_chair_prompt_bundle("case-1", initial_outputs)
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
    reviews = [SpecialtyAnswerReview(
        review_id=f"{answer.answer_id}-RV-pulmonology",
        issue_id=answer.issue_id,
        answer_id=answer.answer_id,
        reviewer_specialty="pulmonology",
        outcome="convert_to_evidence_need",
        rationale="当前有限判断成立，原始影像只用于进一步明确。",
        evidence_gap={
            "available_information": "已有影像文字摘要。",
            "missing_information": "缺少可比原始HRCT。",
            "why_it_matters": "可进一步提高影像判断确定性。",
            "decision_unlocked": "进一步明确影像判断。",
            "related_evidence": [],
        },
    )]
    current_outputs = append_round_responses(initial_outputs, responses, reviews)
    current_bundle = build_chair_prompt_bundle(
        "case-1",
        current_outputs,
        discussion_round=1,
        source_seed=initial_bundle,
    )
    new_gap_ref = next(
        ref
        for ref, item in current_bundle.source_registry.items()
        if item.source_type == "assessment_evidence_need"
        and item.quote == "缺少可比原始HRCT。"
    )
    assert current_bundle.question_refs_to_classify == set()
    assert current_bundle.evidence_need_refs_to_classify == {new_gap_ref}
    assert current_bundle.source_metadata[new_gap_ref]["discussion_issue_id"] == "Q001"
    assert current_bundle.source_metadata[new_gap_ref]["discussion_disposition"] == (
        "convert_to_evidence_need"
    )
    previous = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(initial_bundle)),
        initial_bundle,
        resolved_ledger(initial_bundle),
    )
    result = MDTChairIntegration.model_validate(integration_payload(current_bundle))

    _materialize_evidence_need_conversion_refs(
        result, previous, reviews, current_bundle
    )

    assert any(new_gap_ref in need.source_refs for need in result.evidence_needs)

    ledger_value = ledger_payload(current_bundle)
    ledger_value["question_routes"] = []
    ledger_value["evidence_need_groups"] = []

    class FakeLLM:
        supports_json_schema = True

        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            value = (
                ledger_value
                if self.calls == 1
                else integration_payload(current_bundle)
            )
            return LLMResponse(content=json.dumps(value, ensure_ascii=False), raw={})

    resolved, _ = MDTChairAgent(
        FakeLLM(),
        ledger_prompt_path="src/prompts/mdt_chair/semantic_ledger.md",
        prompt_path="src/prompts/mdt_chair/initial_synthesis.md",
        max_attempts=1,
    ).integrate(
        current_bundle,
        discussion_previous=previous,
        discussion_responses=responses,
        discussion_reviews=reviews,
    )

    assert any(new_gap_ref in need.source_refs for need in resolved.evidence_needs)


def test_partially_answered_question_remains_on_public_board():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    result = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(bundle)),
        bundle,
        resolved_ledger(bundle),
    )
    question = result.questions[0]
    assert question.response_status == "all_responded"
    assert question.answer_status == "partially_answered"
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
    bundle = build_chair_prompt_bundle("case-1", assessable_conflict_outputs())
    payload = integration_payload(bundle)
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    question = source_ref(bundle, "pulmonology", "native_question")
    data_request = source_ref(bundle, "pathology", "native_question")
    payload["conflicts"] = [{
        "topic": "同一资料下的直接相反判断",
        "conflict_nature": "direct_contradiction",
        "conflict_domain": "diagnostic_interpretation",
        "comparison_target": "当前资料直接确认命题 X。",
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
    ledger_payload_value = conflict_ledger_payload(bundle, "direct_contradiction", [
        {
            "source_ref": pulmonary,
            "statement": "直接肯定命题 X。",
            "subject": "命题 X",
            "dimension": "诊断判断",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "disease_diagnosis",
            "position_role": "preferred",
            "epistemic_status": "affirms",
        },
        {
            "source_ref": radiology,
            "statement": "直接否定命题 X。",
            "subject": "命题 X",
            "dimension": "诊断判断",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "disease_diagnosis",
            "position_role": "preferred",
            "epistemic_status": "denies",
        },
    ])
    ledger = resolve_semantic_ledger(
        ChairSemanticLedger.model_validate(ledger_payload_value), bundle
    )
    result = resolve_chair_references(
        MDTChairIntegration.model_validate(payload), bundle, ledger
    )
    conflict = result.conflicts[0]
    assert conflict.conflict_id == "CF001"
    assert conflict.specialties == ["pulmonology", "thoracic_radiology"]
    assert conflict.related_question_ids == ["Q001"]
    assert conflict.related_evidence_need_ids == ["EN002"]
    assert conflict.status == "pending_clarification_and_evidence"


def test_initial_ledger_accepts_decision_relevant_discordance():
    bundle = build_chair_prompt_bundle("case-1", assessable_conflict_outputs())
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    payload = conflict_ledger_payload(bundle, "decision_relevant_discordance", [
        {
            "source_ref": pulmonary,
            "statement": "当前首选模式 A。",
            "subject": "当前主要形态解释",
            "dimension": "形态模式",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "morphologic_pattern",
            "position_role": "preferred",
            "epistemic_status": "possible",
        },
        {
            "source_ref": radiology,
            "statement": "当前首选模式 B。",
            "subject": "当前主要形态解释",
            "dimension": "形态模式",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "morphologic_pattern",
            "position_role": "preferred",
            "epistemic_status": "possible",
        },
    ])

    ledger = resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)

    assert ledger.claim_groups[0].conflict_nature == "decision_relevant_discordance"


def test_initial_integration_exposes_decision_relevant_discordance():
    bundle = build_chair_prompt_bundle("case-1", assessable_conflict_outputs())
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    ledger_payload_value = conflict_ledger_payload(
        bundle,
        "decision_relevant_discordance",
        [
            {
                "source_ref": ref,
                "statement": statement,
                "subject": "当前主要形态解释",
                "dimension": "形态模式",
                "timeframe": "当前",
                "evidence_scope": "现有资料",
                "professional_level": "morphologic_pattern",
                "position_role": "preferred",
                "epistemic_status": "possible",
            }
            for ref, statement in (
                (pulmonary, "当前首选模式 A。"),
                (radiology, "当前首选模式 B。"),
            )
        ],
    )
    ledger = resolve_semantic_ledger(
        ChairSemanticLedger.model_validate(ledger_payload_value), bundle
    )
    payload = integration_payload(bundle)
    payload["conflicts"] = [{
        "topic": "主要形态解释不同",
        "conflict_nature": "decision_relevant_discordance",
        "conflict_domain": "morphologic_interpretation",
        "comparison_target": "当前主要形态解释",
        "comparison_conditions": "当前时点和现有资料。",
        "positions": [
            {"stance": "favors", "position": "首选模式 A。", "source_refs": [pulmonary]},
            {"stance": "favors", "position": "首选模式 B。", "source_refs": [radiology]},
        ],
        "why_incompatible": "不能同时作为当前首选形态解释。",
        "decision_impact": "影响诊断信度和取材策略。",
        "resolution_requirement": "由 MDT 比较影像与临床依据。",
        "related_question_source_refs": [],
        "related_evidence_need_source_refs": [],
    }]

    result = resolve_chair_references(
        MDTChairIntegration.model_validate(payload), bundle, ledger
    )

    assert result.conflicts[0].conflict_nature == "decision_relevant_discordance"
    assert {item.stance for item in result.conflicts[0].positions} == {"favors"}


def test_initial_ledger_rejects_tentative_alternatives_as_discordance():
    values = assessable_conflict_outputs()
    values["thoracic_radiology"]["professional_conclusions"]["conclusions"][0][
        "status"
    ] = "possible"
    bundle = build_chair_prompt_bundle("case-1", values)
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    payload = conflict_ledger_payload(bundle, "decision_relevant_discordance", [
        {
            "source_ref": pulmonary,
            "statement": "模式 A 可能。",
            "subject": "形态解释",
            "dimension": "形态模式",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "morphologic_pattern",
            "position_role": "tentative",
            "epistemic_status": "possible",
        },
        {
            "source_ref": radiology,
            "statement": "模式 B 可能。",
            "subject": "形态解释",
            "dimension": "形态模式",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "morphologic_pattern",
            "position_role": "tentative",
            "epistemic_status": "possible",
        },
    ])

    with pytest.raises(ValueError, match="preferred primary specialty assessments"):
        resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)


def test_initial_ledger_rejects_direct_contradiction_across_levels():
    bundle = build_chair_prompt_bundle("case-1", assessable_conflict_outputs())
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    payload = conflict_ledger_payload(bundle, "direct_contradiction", [
        {
            "source_ref": pulmonary,
            "statement": "肯定疾病诊断 X。",
            "subject": "疾病 X",
            "dimension": "疾病诊断",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "disease_diagnosis",
            "position_role": "preferred",
            "epistemic_status": "affirms",
        },
        {
            "source_ref": radiology,
            "statement": "否定形态模式 X。",
            "subject": "模式 X",
            "dimension": "形态模式",
            "timeframe": "当前",
            "evidence_scope": "现有资料",
            "professional_level": "morphologic_pattern",
            "position_role": "preferred",
            "epistemic_status": "denies",
        },
    ])

    with pytest.raises(ValueError, match="one professional level"):
        resolve_semantic_ledger(ChairSemanticLedger.model_validate(payload), bundle)


def test_complete_boundary_answer_is_removed_from_public_question_board():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = integration_payload(bundle)
    payload["questions"][0]["answer_status"] = "boundary_answered"
    payload["questions"][0].pop("resolution_status", None)

    result = resolve_chair_references(
        MDTChairIntegration.model_validate(payload), bundle, resolved_ledger(bundle)
    )
    assert result.questions == []


def test_accepted_boundary_must_be_materialized_with_original_question_source():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    previous = resolve_chair_references(
        MDTChairIntegration.model_validate(integration_payload(bundle)),
        bundle,
        resolved_ledger(bundle),
    )
    question = previous.questions[0]
    question.raised_by = ["pulmonology"]
    review = SpecialtyAnswerReview(
        review_id="review-1",
        issue_id=question.question_id,
        answer_id="answer-1",
        reviewer_specialty="pulmonology",
        outcome="accept_boundary",
        rationale="缺少关键证据，当前只能形成判断边界。",
    )
    result = previous.model_copy(deep=True)

    with pytest.raises(ValueError, match="must appear in assessment_boundaries"):
        _validate_review_destinations(result, previous, [review], bundle)

    result.assessment_boundaries[0].source_refs.extend(question.source_refs)
    _validate_review_destinations(result, previous, [review], bundle)


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


def test_mixed_specialty_answer_refs_are_repaired_from_question_ledger():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = integration_payload(bundle)
    pulmonary = source_ref(bundle, "pulmonology", "native_conclusion")
    rheumatology = source_ref(bundle, "rheumatology", "native_conclusion")
    radiology = source_ref(bundle, "thoracic_radiology", "native_conclusion")
    payload["questions"][0]["answers"][0]["source_refs"] = [
        pulmonary,
        rheumatology,
    ]

    result = resolve_chair_references(
        MDTChairIntegration.model_validate(payload), bundle, resolved_ledger(bundle)
    )

    assert result.questions[0].answers[0].source_refs == [radiology]
    assert result.questions[0].answers[0].specialty == "thoracic_radiology"
    assert [event["action"] for event in bundle.normalization_events[-2:]] == [
        "dropped_invalid_question_answer_source_refs",
        "restored_question_answer_source_refs_from_ledger",
    ]


def test_non_target_answer_without_ledger_becomes_unanswered():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = integration_payload(bundle)
    payload["questions"][0]["answers"][0]["source_refs"] = [
        source_ref(bundle, "pulmonology", "specialty_assessment")
    ]

    result = resolve_chair_references(
        MDTChairIntegration.model_validate(payload), bundle
    )

    assert result.questions[0].answers == []
    assert result.questions[0].answer_status == "unanswered"
    assert result.questions[0].response_status == "none_responded"


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
            "allowed_source_types": ["specialty_assessment"],
            "dropped": [
                {"source_ref": gap_ref, "source_type": "assessment_evidence_need"}
            ],
        },
        {
            "context": "evidence_need_groups[0].coverage_source_refs",
            "action": "dropped_incompatible_known_source_refs",
            "allowed_source_types": ["specialty_assessment"],
            "dropped": [
                {"source_ref": gap_ref, "source_type": "assessment_evidence_need"}
            ],
        },
    ]


def test_non_target_specialty_assessment_cannot_answer_a_question():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    payload = ledger_payload(bundle)
    pulmonary_assessment = source_ref(
        bundle, "pulmonology", "specialty_assessment"
    )
    payload["question_routes"][0]["answer_links"][0]["source_refs"] = [
        pulmonary_assessment
    ]

    ledger = resolve_semantic_ledger(
        ChairSemanticLedger.model_validate(payload), bundle
    )

    assert ledger.question_routes[0].target_specialties == ["thoracic_radiology"]
    assert ledger.question_routes[0].answer_links == []
    assert bundle.normalization_events[-1] == {
        "context": "question_routes[0].answer_links[0].source_refs",
        "action": "dropped_non_target_specialty_answers",
        "target_specialties": ["thoracic_radiology"],
        "dropped": [pulmonary_assessment],
    }


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
