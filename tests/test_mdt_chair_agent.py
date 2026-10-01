"""Self-contained checks for active protocol paths; retired ledger/report tests removed."""

import json

from src.agents.mdt_chair.agent import build_chair_prompt_bundle, build_semantic_evidence_catalog

from src.agents.common.decision_state import initialize_decision_state, record_discussion_answers, project_active_specialty_outputs


def discussion_outputs(outputs, responses, reviews=()):
    state = initialize_decision_state('case-1', outputs)
    return project_active_specialty_outputs(record_discussion_answers(state, responses, reviews, round_number=1))


from src.agents.mdt_discussion.models import SpecialtyAnswerReview, SpecialtyRoundResponse, SpecialtyTaskAnswer


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
        "chunk_id": "guideline_chunk_1", "quote_unit_ids": ["g1-u1"],
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
        "thoracic_radiology": "文字报告未记载分布，具体形态模式不可评价。",
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
                "question": "请说明现有文字记载的病变分布。",
                "why_it_matters": "核对取材代表性。",
                "decision_unlocked": "评价取材与异常区域是否对应。",
                "related_evidence": [],
            }]
        values[specialty] = {
            "specialty_assessments": {
                "specialty_question": f"{specialty} 当前能回答什么？",
                "assessability": "partially_assessable",
                "assessments": [{
                    "assessment_id": f"{specialty}_1",
                    "role": "primary" if specialty == "pulmonology" else "scope_or_evaluability",
                    "assessment_type": "working_diagnosis" if specialty == "pulmonology" else "assessability",
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
    for value in values.values():
        value['interspecialty_questions'] = {'questions': value['specialty_assessments'].pop('interspecialty_questions')}
        for item in value['specialty_assessments']['assessments']:
            item.update(assessability='partially_assessable', direction='indeterminate', confidence='unknown', clinical_role='boundary')
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


def test_question_inherits_linked_assessment_evidence_when_own_links_are_empty():
    values = outputs()
    question = values["pulmonology"]["interspecialty_questions"]["questions"][0]
    question["related_evidence"] = []
    question["related_assessment_ids"] = ["pulmonology_1"]

    bundle = build_chair_prompt_bundle("case-1", values)
    question_ref = source_ref(bundle, "pulmonology", "interspecialty_question")
    assert bundle.source_evidence[question_ref]["background"]
    assert bundle.prompt_input["specialties"][0]["interspecialty_questions"][0]["related_evidence"]


def test_prompt_projection_excludes_internal_reasoning_and_runtime_guidelines():
    bundle = build_chair_prompt_bundle("case-1", outputs())
    compact = json.dumps(bundle.prompt_input, ensure_ascii=False)
    assert "内部推理不得输入主持人" not in compact
    assert "指南运行时不应进入主持人输入" not in compact
    assert "guideline_evidence" not in compact
    assert "declared_roles" not in compact
    assert any(bundle.source_guidelines.values())
    assert "evidence_registry" not in bundle.prompt_input
    for specialty in bundle.prompt_input["specialties"]:
        for assessment in specialty["specialty_assessments"]:
            expected = {
                evidence_ref
                for values in bundle.source_evidence[assessment["source_ref"]].values()
                for evidence_ref in values
            }
            assert {
                option["evidence_ref"]
                for option in assessment["evidence_options"]
            } == expected


def test_chair_projects_relation_dimensions_into_legacy_evidence_groups():
    values = outputs()
    values["pulmonology"]["specialty_assessments"]["assessments"][0]["evidence"] = {
        "evidence_relations": [{
            **pointer(),
            "direction": "supports",
            "function": "qualifying",
        }]
    }

    bundle = build_chair_prompt_bundle("case-1", values)
    pulmonary = source_ref(bundle, "pulmonology", "specialty_assessment")

    assert bundle.source_evidence[pulmonary]["supporting"] == ["seg_001_gu_001_ev_001"]
    assert bundle.source_evidence[pulmonary]["qualifying"] == [
        "seg_001_gu_001_ev_001"
    ]


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


def test_discussion_answer_is_not_projected_as_a_formal_specialty_judgment():
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-thoracic_radiology-A",
        task_id="R01-Q001-thoracic_radiology",
        issue_type="question",
        issue_id="Q001",
        answerability="partially_answered",
        answer="现有文字只能支持有限影像表型。",
        confidence="moderate",
        medical_basis="缺少原始影像。",
        changed_from_previous=False,
    )
    response = SpecialtyRoundResponse(
        case_id="case-1",
        round_number=1,
        specialty="thoracic_radiology",
        answers=[answer],
    )
    current = discussion_outputs(outputs(), [response])

    bundle = build_chair_prompt_bundle("case-1", current)
    radiology = next(
        item
        for item in bundle.prompt_input["specialties"]
        if item["specialty"] == "thoracic_radiology"
    )

    assert len(radiology["specialty_assessments"]) == 1
    assert len(radiology["discussion_answers"]) == 1
    assert radiology["discussion_answers"][0]["source_type"] == "discussion_answer"
    assert {
        bundle.source_registry[ref].source_type
        for ref in bundle.source_registry
        if bundle.source_metadata[ref].get("assessment_id") == answer.answer_id
    } == {"discussion_answer"}


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
    cumulative = discussion_outputs(
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
            "missing_information": "报告未描述病变分布。",
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
        rationale="当前判断已成立，补充文字结果用于进一步明确。",
        evidence_gap={
            "available_information": "已有文字摘要。",
            "missing_information": "报告未描述病变分布。",
            "why_it_matters": "可进一步提高影像判断的明确度。",
            "decision_unlocked": "进一步明确形态评价。",
            "related_evidence": [],
        },
    )]
    cumulative = discussion_outputs(initial_outputs, responses, reviews)

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

    assert len(first_round.question_refs_to_classify) == 1
    assert len(first_round.evidence_need_refs_to_classify) == 1
    assert sum(len(s["interspecialty_questions"]) for s in first_round.prompt_input["specialties"]) == 1
    assert second_round.question_refs_to_classify == set()
    assert second_round.evidence_need_refs_to_classify == set()


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
    source["pulmonology"]["specialty_assessments"]["assessments"][0]["evidence"]["supporting"][0].update(
        {"segment_id": "wrong", "quote": "wrong", "node_ids": []}
    )
    bundle = build_chair_prompt_bundle("case-1", source, semantic_evidence=catalog)
    evidence = next(iter(bundle.evidence_registry.values()))
    assert evidence.segment_id == "seg_001"
    assert evidence.quote == "规范原文。"
    assert evidence.evidence_ref == "seg_001_gu_001_ev_001"
    assert "E001" not in bundle.evidence_registry

    source_with_proposition = outputs()
    source_with_proposition["pulmonology"]["specialty_assessments"]["assessments"][0]["evidence"]["supporting"][0]["proposition_ids"] = ["prop_001"]
    proposition_bundle = build_chair_prompt_bundle(
        "case-1",
        source_with_proposition,
        semantic_evidence=catalog,
    )
    proposition_evidence = next(iter(proposition_bundle.evidence_registry.values()))
    assert proposition_evidence.evidence_ref == "seg_001_gu_001::prop_001"
    assert proposition_evidence.proposition_ids == ["seg_001_gu_001::prop_001"]


def test_graph_unit_quote_is_rebuilt_from_unique_blocks_across_sources_and_rounds():
    unit_id = "seg_001_gu_001"
    blocks = {f"{unit_id}_ev_{i:03d}": text for i, text in enumerate(
        ("患者咳嗽。", "活动后气短。", "近期加重。"), 1
    )}
    ids = list(blocks)
    catalog = {unit_id: {"segment_id": "seg_001", "evidence_blocks": blocks}}
    source = outputs()
    for specialty, selected in zip(SPECIALTIES, (ids[:2], ids[1:], ids[:2], ids[1:])):
        source[specialty]["specialty_assessments"]["assessments"][0]["evidence"] = {
            "supporting": [{"graph_unit_id": unit_id, "evidence_ids": selected}]
        }
    bundle = build_chair_prompt_bundle("case-1", source, semantic_evidence=catalog)
    evidence = bundle.evidence_registry[unit_id]
    assert evidence.quote == "".join(blocks.values())
    assert set(evidence.evidence_ids) == set(ids)
    later = build_chair_prompt_bundle("case-1", source, semantic_evidence=catalog, source_seed=bundle)
    assert later.evidence_registry[unit_id].quote == evidence.quote
