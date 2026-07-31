import json

import pytest

from src.agents.mdt_chair.models import MDTChairIntegration
from src.agents.mdt_discussion.final_report import FinalReportAgent, build_discussion_audit
from src.agents.mdt_discussion.integration import (
    append_round_responses,
    apply_review_outcomes,
    build_review_dispositions,
    decide_discussion_continuation,
    stabilize_integration_ids,
)
from src.agents.mdt_discussion.models import (
    DiscussionAnswerClaimDraft,
    DiscussionProposition,
    DiscussionRound,
    DiscussionEvidenceUseDraft,
    MDTFinalReport,
    SpecialtyAnswerReview,
    SpecialtyRoundResponse,
    SpecialtyTaskAnswer,
    SpecialtyTaskAnswerDraft,
)
from src.agents.mdt_discussion.prompt_projection import (
    build_chair_prompt_view,
    build_issue_chair_prompt_view,
    build_specialty_discussion_prompt_view,
    build_specialty_initial_prompt_view,
)
from src.agents.mdt_discussion.routing import build_discussion_tasks, group_tasks_by_specialty
from src.agents.mdt_discussion.specialty_agent import (
    SpecialtyDiscussionAgent,
    _resolve_answer,
    discussion_evidence_schema_constraints,
)
from src.llm.base import LLMResponse
from src.llm.structured import json_schema_response_format


DIAGNOSTIC_DIMENSIONS = [
    "ild_presence",
    "radiologic_pattern",
    "histopathologic_pattern",
    "mdt_diagnosis",
    "etiologic_attribution",
    "disease_behavior",
    "acute_or_comorbid_factors",
]


def final_report_v2_payload(*, chair_item_id="IC001"):
    return {
        "clinical_report": {
            "overall_conclusion": "纤维化性间质性肺病工作诊断，具体类型待分类。",
            "overall_confidence": "moderate",
            "integrated_summary": "模式与病因分别保留判断边界。",
            "diagnostic_matrix": [
                {
                    "dimension": dimension,
                    "statement": f"{dimension} 的当前判断。",
                    "status": (
                        "favored" if dimension in {"ild_presence", "mdt_diagnosis"}
                        else "not_assessable"
                    ),
                    "confidence": (
                        "moderate" if dimension in {"ild_presence", "mdt_diagnosis"}
                        else "unknown"
                    ),
                    "role": (
                        "primary" if dimension in {"ild_presence", "mdt_diagnosis"}
                        else "boundary"
                    ),
                    "medical_basis": "保留医学依据",
                    "chair_item_ids": [chair_item_id],
                    "limitations": [],
                }
                for dimension in DIAGNOSTIC_DIMENSIONS
            ],
            "differential_diagnoses": [{
                "rank": 1,
                "diagnosis": "特发性肺纤维化",
                "confidence": "low",
                "rationale": "缺少可评价 HRCT，仅保留为鉴别。",
                "chair_item_ids": [chair_item_id],
            }],
        },
    }


def documents():
    propositions = {
        "segments": [{
            "units": [{
                "graph_unit_id": "gu-1",
                "propositions": [{
                    "proposition_id": "prop-1",
                    "concept_text": "存在低氧",
                    "status": "present",
                    "certainty": "high",
                    "modifiers": [],
                    "evidence": {"evidence_ids": ["ev-1"]},
                }],
            }],
        }],
    }
    graphs = {
        "segments": [{
            "units": [{
                "graph_unit_id": "gu-1",
                "evidence_blocks": [{"evidence_id": "ev-1", "text": "静息低氧"}],
                "nodes": [{
                    "node_id": "gu-1::prop-1",
                    "node_type": "proposition",
                    "semantic_type": "finding",
                    "label": "存在低氧",
                    "status": "present",
                    "certainty": "high",
                    "evidence": {"evidence_ids": ["ev-1"]},
                    "metadata": {"rationale": "test"},
                }],
                "edges": [],
            }],
        }],
    }
    return propositions, graphs


def chair_question(*, resolution_status="unresolved"):
    return {
        "questions": [{
            "question_id": "Q001",
            "question": "低氧的主要归因是什么？",
            "target_specialties": ["pulmonology"],
            "resolution_status": resolution_status,
            "remaining_clarification": "区分肺实质与肺血管因素。",
            "evidence": {
                "supporting": [{
                    "evidence_ref": "gu-1:ev-1",
                    "segment_id": "seg-1",
                    "graph_unit_id": "gu-1",
                    "evidence_ids": ["ev-1"],
                    "quote": "静息低氧",
                }],
            },
        }],
        "conflicts": [],
    }


def chair_question_model():
    payload = chair_question()
    payload["questions"][0].update({
        "source_refs": ["S001"],
        "answer_summary": "当前尚未形成完整回答。",
        "why_it_matters": "影响低氧归因。",
        "decision_unlocked": "明确主要机制。",
    })
    return MDTChairIntegration.model_validate(payload)


def expanded_chair_result():
    return {
        "integrated_conclusions": [{
            "conclusion_id": "IC001",
            "statement": "保留主席语义结论",
            "medical_basis": "保留医学依据",
            "status": "favored",
            "supporting_specialties": ["pulmonology"],
            "source_refs": ["S001"],
            "source_citations": [{
                "source_ref": "S001",
                "specialty": "pulmonology",
                "source_type": "native_conclusion",
                "source_path": "professional_conclusions.conclusions[0]",
                "quote": "不应进入共享提示的完整专科原文",
            }],
            "evidence": {
                "links": [{
                    "evidence_ref": "E001",
                    "segment_id": "seg-1",
                    "graph_unit_id": "gu-1",
                    "evidence_ids": ["ev-1"],
                    "proposition_ids": ["gu-1::prop-1"],
                    "node_ids": ["gu-1::prop-1"],
                    "quote": "不应在主席共享视图中重复的病例原文",
                    "target_claim_id": "T001-A001",
                    "relation": "supports",
                    "rationale": "该证据图直接支持这一原子判断。",
                }],
                "supporting": [{
                    "evidence_ref": "E001",
                    "segment_id": "seg-1",
                    "graph_unit_id": "gu-1",
                    "evidence_ids": ["ev-1"],
                    "proposition_ids": ["gu-1::prop-1"],
                    "node_ids": ["gu-1::prop-1"],
                    "quote": "不应在主席共享视图中重复的病例原文",
                }],
                "weakening": [],
                "discriminating": [],
                "background": [],
            },
            "guideline_evidence": [{
                "chunk_id": "guide:p001:c001",
                "relevance": "相关",
                "application": "用于校准当前判断",
                "title": "运行时指南标题",
                "quote": "不应进入共享提示的完整指南原文",
            }],
        }],
        "assessment_boundaries": [],
        "conflicts": [],
        "questions": [],
        "evidence_needs": [],
    }


def chair_conclusion(*, conclusion_id, source_refs, statement):
    return {
        "conclusion_id": conclusion_id,
        "source_refs": source_refs,
        "source_citations": [
            {
                "source_ref": source_ref,
                "specialty": "pulmonology",
                "source_type": "specialty_assessment",
                "source_path": f"specialty_assessments.{source_ref}",
                "quote": f"{source_ref} 原话",
            }
            for source_ref in source_refs
        ],
        "evidence": {},
        "guideline_evidence": [],
        "statement": statement,
        "medical_basis": "医学依据。",
        "decision_impact": "影响诊断。",
        "role": "primary",
        "conclusion_type": "working_diagnosis",
        "status": "favored",
    }


def test_chair_prompt_view_keeps_semantics_and_compacts_provenance():
    view = build_chair_prompt_view(expanded_chair_result())
    conclusion = view["integrated_conclusions"][0]

    assert conclusion["conclusion_id"] == "IC001"
    assert conclusion["statement"] == "保留主席语义结论"
    assert conclusion["supporting_specialties"] == ["pulmonology"]
    assert conclusion["source_citations"] == [{
        "source_ref": "S001",
        "specialty": "pulmonology",
        "source_type": "native_conclusion",
    }]
    assert conclusion["evidence"]["supporting"] == [{
        "evidence_ref": "E001",
        "graph_unit_id": "gu-1",
        "evidence_ids": ["ev-1"],
        "proposition_ids": ["gu-1::prop-1"],
    }]
    assert conclusion["evidence"]["links"] == [{
        "evidence_ref": "E001",
        "graph_unit_id": "gu-1",
        "evidence_ids": ["ev-1"],
        "proposition_ids": ["gu-1::prop-1"],
        "target_claim_id": "T001-A001",
        "relation": "supports",
        "rationale": "该证据图直接支持这一原子判断。",
    }]
    assert conclusion["guideline_evidence"] == [{
        "chunk_id": "guide:p001:c001",
        "relevance": "相关",
        "application": "用于校准当前判断",
    }]
    compact = json.dumps(view, ensure_ascii=False)
    assert "完整专科原文" not in compact
    assert "病例原文" not in compact
    assert "完整指南原文" not in compact


def test_stable_chair_ids_remain_unique_when_two_items_match_one_prior_item():
    previous = MDTChairIntegration.model_validate({
        "integrated_conclusions": [chair_conclusion(
            conclusion_id="IC001",
            source_refs=["S001"],
            statement="既有结论。",
        )],
    })
    current = MDTChairIntegration.model_validate({
        "integrated_conclusions": [
            chair_conclusion(
                conclusion_id="",
                source_refs=["S001"],
                statement="更新后的既有结论。",
            ),
            chair_conclusion(
                conclusion_id="",
                source_refs=["S001", "S002"],
                statement="由相同来源扩展出的另一层级结论。",
            ),
        ],
    })

    stabilized = stabilize_integration_ids(current, previous)

    assert [item.conclusion_id for item in stabilized.integrated_conclusions] == [
        "IC001",
        "IC002",
    ]


def test_specialty_initial_prompt_view_keeps_only_two_formal_sections():
    view = build_specialty_initial_prompt_view({
        "professional_conclusions": {"marker": "正式结论"},
        "clinical_reasoning": {"marker": "内部推理"},
    })

    assert view == {
        "specialty_assessments": {
            "specialty_question": None,
            "assessability": None,
            "assessments": [],
            "evidence_gaps": [],
            "boundaries": [],
        },
        "interspecialty_questions": {"questions": []},
    }


def test_discussion_prompt_views_keep_only_the_current_issue_and_compact_baseline():
    chair = expanded_chair_result()
    chair.update(chair_question())
    chair["questions"][0]["answer_summary"] = "当前问题摘要"
    chair_view = build_issue_chair_prompt_view(chair, "Q001")
    specialty_view = build_specialty_discussion_prompt_view({
        "professional_conclusions": {
            "conclusions": [{
                "conclusion_id": "C001",
                "statement": "正式专科结论",
                "status": "possible",
                "medical_basis": "简要依据",
                "decision_impact": "不应重复传入",
                "evidence": {"supporting": [{"quote": "不应重复传入的原文"}]},
            }],
            "boundaries": ["既有边界"],
        },
    })

    assert chair_view["issue"]["question_id"] == "Q001"
    assert "保留主席语义结论" not in json.dumps(chair_view, ensure_ascii=False)
    assert specialty_view["specialty_assessments"][0]["statement"] == "正式专科结论"
    assert "不应重复传入的原文" not in json.dumps(specialty_view, ensure_ascii=False)


def test_routes_declared_specialties_and_builds_an_evidence_analysis_packet():
    propositions, graphs = documents()
    tasks = build_discussion_tasks(
        chair_result=chair_question(),
        clinical_propositions=propositions,
        local_graphs=graphs,
        round_number=1,
        previous_rounds=[],
    )

    assert set(group_tasks_by_specialty(tasks)) == {"pulmonology"}
    candidate = tasks[0].evidence_candidates[0]
    assert candidate.evidence_fragments == [{"evidence_id": "ev-1", "text": "静息低氧"}]
    assert candidate.propositions[0].proposition_id == "gu-1::prop-1"
    assert candidate.graph_nodes[0]["label"] == "存在低氧"
    assert "metadata" not in candidate.graph_nodes[0]


def test_program_backfills_answer_and_evidence_ids_from_the_selected_task():
    proposition = DiscussionProposition(
        proposition_id="gu-1::prop-1",
        concept_text="存在低氧",
        status="present",
        certainty="high",
    )
    propositions, graphs = documents()
    task = build_discussion_tasks(
        chair_result=chair_question(),
        clinical_propositions=propositions,
        local_graphs=graphs,
        round_number=1,
        previous_rounds=[],
    )[0]
    draft = SpecialtyTaskAnswerDraft(
        answerability="partially_answered",
        answer="现有资料支持低氧，但不能完成相对贡献量化。",
        confidence="moderate",
        medical_basis="原文只提供静息低氧。",
        answer_claims=[DiscussionAnswerClaimDraft(
            statement="现有资料支持低氧存在，但不能完成相对贡献量化。",
            evidence_uses=[DiscussionEvidenceUseDraft(
                evidence_ref="gu-1",
                proposition_ids=[proposition.proposition_id],
                effect="supporting",
                interpretation="证明低氧存在，不能单独证明病因。",
            )],
        )],
        evidence_uses=[DiscussionEvidenceUseDraft(
            evidence_ref="gu-1",
            proposition_ids=[proposition.proposition_id],
            effect="supporting",
            interpretation="证明低氧存在，不能单独证明病因。",
        )],
        changed_from_previous=True,
        remaining_limitation="缺少肺血管评估。",
    )

    answer = _resolve_answer(task, draft, answer_id="R01-A001-pulmonology")

    assert answer.answer_id == "R01-A001-pulmonology"
    assert answer.task_id == task.task_id
    assert answer.answer == "现有资料支持低氧存在，但不能完成相对贡献量化。"
    assert answer.answer_claims[0].claim_id == "R01-A001-pulmonology-C001"
    assert answer.answer_claims[0].evidence_uses[0].quote == "静息低氧"
    assert answer.evidence_uses[0].evidence_ids == ["ev-1"]
    assert answer.evidence_uses[0].quote == "静息低氧"
    assert answer.evidence_uses[0].evidence_fragments[0]["text"] == "静息低氧"
    assert answer.evidence_uses[0].graph_nodes[0]["label"] == "存在低氧"


def test_specialty_discussion_generates_one_answer_for_one_task():
    class FakeLLM:
        supports_json_schema = False

        def complete(self, messages, *, temperature, max_tokens, response_format=None):
            content = '''{
                "answerability":"partially_answered",
                "answer":"低氧存在，但当前资料不能完成病因贡献量化。",
                "confidence":"moderate",
                "medical_basis":"原文片段只证实静息低氧。",
                "answer_claims":[{
                    "statement":"低氧存在，但当前资料不能完成病因贡献量化。",
                    "evidence_uses":[{
                        "evidence_ref":"gu-1",
                        "proposition_ids":["gu-1::prop-1"],
                        "effect":"supporting",
                        "interpretation":"证明低氧存在，不能单独证明病因。"
                    }],
                    "guideline_evidence":[]
                }],
                "evidence_uses":[{
                    "evidence_ref":"gu-1",
                    "proposition_ids":["gu-1::prop-1"],
                    "effect":"supporting",
                    "interpretation":"证明低氧存在，不能单独证明病因。"
                }],
                "changed_from_previous":false,
                "remaining_limitation":"缺少肺血管评估。"
            }'''
            return LLMResponse(content=content, raw={"choices": [{}]})

    propositions, graphs = documents()
    task = build_discussion_tasks(
        chair_result=chair_question(),
        clinical_propositions=propositions,
        local_graphs=graphs,
        round_number=1,
        previous_rounds=[],
    )[0]
    agent = SpecialtyDiscussionAgent(
        FakeLLM(),
        specialty="pulmonology",
        config={"guideline_retrieval": {"enabled": False}},
    )

    answer, trace = agent.respond_to_task(
        task=task,
        specialty_initial_output={
            "professional_conclusions": {
                "conclusions": [{
                    "conclusion_id": "C001",
                    "statement": "正式专科结论",
                    "status": "possible",
                    "medical_basis": "既有依据",
                }],
                "boundaries": [],
            },
            "clinical_reasoning": {"marker": "不应进入会中提示的内部推理"},
        },
        chair_result={**expanded_chair_result(), **chair_question()},
    )

    assert answer.task_id == task.task_id
    assert answer.answer_id == f"{task.task_id}-A"
    assert "保留主席语义结论" not in trace["prompt"]
    assert "正式专科结论" in trace["prompt"]
    assert "静息低氧" in trace["prompt"]
    assert "gu-1::prop-1" in trace["prompt"]
    assert "不应进入会中提示的内部推理" not in trace["prompt"]
    assert "不应进入共享提示的完整专科原文" not in trace["prompt"]
    assert "`remaining_clarification` 是本轮真正需要解决的部分" in trace["prompt"]
    assert "不能确认”也可以是对问题的完整回答" in trace["prompt"]


def test_requester_review_uses_only_the_current_question_and_answer():
    class FakeLLM:
        supports_json_schema = False

        def complete(self, messages, *, temperature, max_tokens, response_format=None):
            assert max_tokens == 2500
            return LLMResponse(
                content='{"outcome":"accept_boundary","rationale":"已明确当前资料边界。"}',
                raw={"choices": [{}]},
            )

    propositions, graphs = documents()
    task = build_discussion_tasks(
        chair_result=chair_question(),
        clinical_propositions=propositions,
        local_graphs=graphs,
        round_number=1,
        previous_rounds=[],
    )[0]
    answer = SpecialtyTaskAnswer(
        answer_id=f"{task.task_id}-A",
        task_id=task.task_id,
        issue_type="question",
        issue_id=task.issue_id,
        answerability="partially_answered",
        answer="现有资料只能确认低氧存在，不能完成病因归因。",
        confidence="moderate",
        medical_basis="缺少肺血管资料。",
        changed_from_previous=False,
        remaining_limitation="仍需肺血管评估。",
    )
    agent = SpecialtyDiscussionAgent(
        FakeLLM(),
        specialty="rheumatology",
        config={"guideline_retrieval": {"enabled": False}},
    )

    review, trace = agent.review_answer(task=task, answer=answer)

    assert review.outcome == "accept_boundary"
    assert review.answer_id == answer.answer_id
    assert task.prompt in trace["prompt"]
    assert answer.answer in trace["prompt"]
    assert "integrated_conclusions" not in trace["prompt"]
    assert "guideline_context" not in trace["prompt"]


def test_review_outcome_closes_a_boundary_without_calling_it_resolved():
    payload = chair_question()
    payload["questions"][0].update({
        "source_refs": ["source-1"],
        "answer_summary": "现有资料只能形成边界。",
        "why_it_matters": "影响病因判断。",
        "decision_unlocked": "明确当前讨论处置。",
    })
    result = MDTChairIntegration.model_validate(payload)
    question = result.questions[0]
    question.raised_by = ["rheumatology"]
    review = SpecialtyAnswerReview(
        review_id="review-1",
        issue_id="Q001",
        answer_id="answer-1",
        reviewer_specialty="rheumatology",
        outcome="accept_boundary",
        rationale="接受现有证据边界。",
    )

    apply_review_outcomes(result, [review])

    assert question.discussion_status == "closed_this_round"
    assert question.closure_type == "boundary_answer"
    assert question.answer_status == "boundary_answered"
    assert question.review_status == "accepted_boundary"
    assert question.reviewed_by == ["rheumatology"]
    assert result.questions == []


def test_requester_review_deterministically_routes_boundary_before_chair():
    result = chair_question_model()
    result.questions[0].raised_by = ["rheumatology"]
    review = SpecialtyAnswerReview(
        review_id="review-1",
        issue_id="Q001",
        answer_id="answer-1",
        reviewer_specialty="rheumatology",
        outcome="accept_boundary",
        rationale="没有关键证据便不能完成判断。",
    )

    dispositions = build_review_dispositions(result, [review])

    assert dispositions["Q001"]["destination"] == "assessment_boundary"
    assert dispositions["Q001"]["question_source_refs"] == result.questions[0].source_refs


def test_evidence_need_conversion_is_closed_and_non_blocking():
    result = chair_question_model()
    result.questions[0].raised_by = ["rheumatology"]
    review = SpecialtyAnswerReview(
        review_id="review-1",
        issue_id="Q001",
        answer_id="answer-1",
        reviewer_specialty="rheumatology",
        outcome="convert_to_evidence_need",
        rationale="当前判断成立，补充资料只提高明确度。",
        evidence_gap={
            "available_information": "已有初步资料。",
            "missing_information": "缺少原始报告。",
            "why_it_matters": "可提高判断明确度。",
            "decision_unlocked": "进一步明确当前判断。",
            "related_evidence": [],
        },
    )

    dispositions = build_review_dispositions(result, [review])
    question = result.questions[0]
    apply_review_outcomes(result, [review])

    assert dispositions["Q001"]["destination"] == "evidence_need"
    assert question.answer_status == "answered"
    assert question.discussion_status == "closed_this_round"
    assert result.questions == []


def test_review_incompatibility_is_a_flag_not_a_formal_conflict_object():
    payload = chair_question()
    payload["questions"][0].update({
        "source_refs": ["source-1"],
        "answer_summary": "回答与提出方既有正式判断不兼容。",
        "why_it_matters": "需要主持人重新比较正式结论。",
        "decision_unlocked": "决定是否形成正式冲突。",
    })
    result = MDTChairIntegration.model_validate(payload)
    question = result.questions[0]
    question.raised_by = ["rheumatology"]
    review = SpecialtyAnswerReview(
        review_id="review-1",
        issue_id="Q001",
        answer_id="answer-1",
        reviewer_specialty="rheumatology",
        outcome="flag_incompatibility",
        rationale="本轮回答与风湿科正式判断直接不兼容。",
    )

    apply_review_outcomes(result, [review])

    assert question.review_status == "incompatibility_flagged"
    assert question.discussion_status == "clarification_in_progress"
    assert question.answer_status == "partially_answered"
    assert result.conflicts == []


def test_final_requester_review_closes_an_in_round_clarification():
    payload = chair_question()
    payload["questions"][0].update({
        "source_refs": ["source-1"],
        "answer_summary": "已完成澄清。",
        "why_it_matters": "影响病因判断。",
        "decision_unlocked": "明确当前讨论处置。",
    })
    result = MDTChairIntegration.model_validate(payload)
    question = result.questions[0]
    question.raised_by = ["rheumatology"]
    reviews = [
        SpecialtyAnswerReview(
            review_id="review-1",
            issue_id="Q001",
            answer_id="answer-1",
            reviewer_specialty="rheumatology",
            outcome="request_clarification",
            rationale="需要澄清。",
        ),
        SpecialtyAnswerReview(
            review_id="review-2",
            issue_id="Q001",
            answer_id="answer-2",
            reviewer_specialty="rheumatology",
            outcome="accept_answer",
            rationale="澄清后接受。",
        ),
    ]

    apply_review_outcomes(result, reviews)

    assert question.discussion_status == "closed_this_round"
    assert question.closure_type == "clarified_answer"
    assert result.questions == []


def test_round_continues_when_an_open_issue_has_material_change():
    previous = chair_question_model()
    current = previous.model_copy(deep=True)
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-pulmonology-A",
        task_id="R01-Q001-pulmonology",
        issue_type="question",
        issue_id="Q001",
        answerability="partially_answered",
        answer="本轮形成了新的部分判断。",
        confidence="moderate",
        medical_basis="存在新的专业解释。",
        changed_from_previous=True,
    )

    decision = decide_discussion_continuation(
        previous=previous,
        current=current,
        round_number=1,
        max_rounds=3,
        responses=[SpecialtyRoundResponse(
            case_id="case-1",
            round_number=1,
            specialty="pulmonology",
            answers=[answer],
        )],
        reviews=[],
    )

    assert decision["continue_discussion"] is True
    assert decision["changed_answers"] == 1


def test_unchanged_open_issue_stops_early_without_reclassification():
    previous = chair_question_model()
    current = previous.model_copy(deep=True)
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-pulmonology-A",
        task_id="R01-Q001-pulmonology",
        issue_type="question",
        issue_id="Q001",
        answerability="partially_answered",
        answer="本轮没有形成新的判断。",
        confidence="moderate",
        medical_basis="现有信息不变。",
        changed_from_previous=False,
    )

    decision = decide_discussion_continuation(
        previous=previous,
        current=current,
        round_number=1,
        max_rounds=3,
        responses=[SpecialtyRoundResponse(
            case_id="case-1",
            round_number=1,
            specialty="pulmonology",
            answers=[answer],
        )],
        reviews=[],
    )

    assert decision["continue_discussion"] is False
    assert "未形成新的专科判断" in decision["stop_reason"]
    assert current.questions[0].answer_status == "unanswered"


def test_third_round_stops_and_preserves_open_issue():
    previous = chair_question_model()
    current = previous.model_copy(deep=True)

    decision = decide_discussion_continuation(
        previous=previous,
        current=current,
        round_number=3,
        max_rounds=3,
        responses=[],
        reviews=[],
    )

    assert decision["continue_discussion"] is False
    assert decision["stop_reason"] == "已达到最多3轮团队讨论。"
    assert len(current.questions) == 1


def test_third_round_reports_resolution_before_the_round_limit():
    previous = chair_question_model()
    current = previous.model_copy(deep=True)
    current.questions = []

    decision = decide_discussion_continuation(
        previous=previous,
        current=current,
        round_number=3,
        max_rounds=3,
        responses=[],
        reviews=[],
    )

    assert decision["continue_discussion"] is False
    assert decision["stop_reason"] == "当前已无仍需专科处理的问题或真实冲突。"


def test_discussion_schema_closes_evidence_uses_to_task_candidates():
    propositions, graphs = documents()
    task = build_discussion_tasks(
        chair_result=chair_question(),
        clinical_propositions=propositions,
        local_graphs=graphs,
        round_number=1,
        previous_rounds=[],
    )[0]

    schema = json_schema_response_format(
        SpecialtyTaskAnswerDraft,
        "discussion_answer",
        pointer_field_constraints=discussion_evidence_schema_constraints(task),
    )["json_schema"]["schema"]
    expected = ["gu-1"]
    top_level = schema["properties"]["evidence_uses"]["items"]["properties"]
    claim_level = schema["$defs"]["DiscussionAnswerClaimDraft"]["properties"][
        "evidence_uses"
    ]["items"]["properties"]
    related_evidence = schema["$defs"]["EvidenceGap"]["properties"][
        "related_evidence"
    ]["items"]["properties"]

    for properties in (top_level, claim_level):
        assert properties["evidence_ref"]["enum"] == expected
        assert properties["proposition_ids"]["items"]["enum"] == ["gu-1::prop-1"]
    assert related_evidence["evidence_ids"]["items"]["enum"] == ["ev-1"]


def test_discussion_schema_allows_evidence_without_extracted_propositions():
    propositions, graphs = documents()
    task = build_discussion_tasks(
        chair_result=chair_question(),
        clinical_propositions=propositions,
        local_graphs=graphs,
        round_number=1,
        previous_rounds=[],
    )[0]
    task.evidence_candidates[0].propositions = []

    schema = json_schema_response_format(
        SpecialtyTaskAnswerDraft,
        "discussion_answer",
        pointer_field_constraints=discussion_evidence_schema_constraints(task),
    )["json_schema"]["schema"]
    properties = schema["properties"]["evidence_uses"]["items"]["properties"]

    assert properties["evidence_ref"]["enum"] == ["gu-1"]
    assert properties["proposition_ids"]["maxItems"] == 0


def test_task_rejects_an_evidence_reference_without_source_text():
    propositions, graphs = documents()
    question = chair_question()
    question["questions"][0]["evidence"]["supporting"][0]["quote"] = ""
    graphs["segments"][0]["units"][0]["evidence_blocks"] = []

    with pytest.raises(ValueError, match="missing source text"):
        build_discussion_tasks(
            chair_result=question,
            clinical_propositions=propositions,
            local_graphs=graphs,
            round_number=1,
            previous_rounds=[],
        )


def test_task_without_an_evidence_reference_remains_allowed():
    propositions, graphs = documents()
    question = chair_question()
    question["questions"][0]["evidence"] = {}

    tasks = build_discussion_tasks(
        chair_result=question,
        clinical_propositions=propositions,
        local_graphs=graphs,
        round_number=1,
        previous_rounds=[],
    )

    assert len(tasks) == 1
    assert tasks[0].evidence_candidates == []


def test_final_report_uses_the_compact_chair_view():
    class FakeLLM:
        supports_json_schema = False

        def complete(self, messages, *, temperature, max_tokens, response_format=None):
            content = '''{
                "consensus_status":"consensus_with_boundaries",
                "primary_conclusion":"工作诊断",
                "diagnostic_confidence":"中等",
                "integrated_summary":"综合总结",
                "evidence_basis":["保留医学依据"],
                "assessment_boundaries":[],
                "unresolved_conflicts":[],
                "evidence_needs":[],
                "discussion_summary":"讨论总结"
            }'''
            return LLMResponse(content=content, raw={"choices": [{}]})

    agent = FinalReportAgent(
        FakeLLM(),
        config={"guideline_retrieval": {"enabled": False}},
    )
    report, trace = agent.generate(
        case_id="case-1",
        chair_result=expanded_chair_result(),
        rounds=[],
        stop_reason="讨论结束。",
    )

    assert "保留主席语义结论" in trace["prompt"]
    assert "保留医学依据" in trace["prompt"]
    assert "不应进入共享提示的完整专科原文" not in trace["prompt"]
    assert "不应在主席共享视图中重复的病例原文" not in trace["prompt"]
    assert report.consensus_status == "consensus_reached"


def test_v2_report_requires_each_diagnostic_dimension_once():
    payload = final_report_v2_payload()
    payload["clinical_report"]["diagnostic_matrix"].pop()

    with pytest.raises(ValueError, match="at least 7 items"):
        MDTFinalReport.model_validate(payload)


def test_legacy_report_is_migrated_without_inventing_diagnostic_layers():
    report = MDTFinalReport.model_validate({
        "case_id": "case-1",
        "consensus_status": "consensus_with_boundaries",
        "discussion_rounds": 1,
        "primary_conclusion": "纤维化性 ILD 工作诊断。",
        "diagnostic_confidence": "中等",
        "integrated_summary": "旧版摘要。",
        "evidence_basis": [],
        "assessment_boundaries": ["缺少 HRCT。"],
        "unresolved_conflicts": [],
        "evidence_needs": [],
        "discussion_summary": "旧版讨论摘要。",
    })

    assert report.legacy_source is True
    assert len(report.clinical_report.diagnostic_matrix) == 7
    radiology = next(
        item
        for item in report.clinical_report.diagnostic_matrix
        if item.dimension == "radiologic_pattern"
    )
    assert radiology.status == "not_assessable"
    assert radiology.confidence == "unknown"


def test_v3_report_restores_exact_provenance_from_selected_chair_items():
    class FakeLLM:
        supports_json_schema = False

        def complete(self, messages, *, temperature, max_tokens, response_format=None):
            return LLMResponse(
                content=json.dumps(final_report_v2_payload(), ensure_ascii=False),
                raw={"choices": [{}]},
            )

    report, _ = FinalReportAgent(
        FakeLLM(),
        config={"max_attempts": 1},
    ).generate(
        case_id="case-1",
        chair_result=expanded_chair_result(),
        rounds=[],
        stop_reason="讨论结束。",
    )

    assert report.schema_version == "mdt_final_report.v3"
    assert len(report.reasoning_trace) == 8
    trace = report.reasoning_trace[0]
    assert trace.source_citations[0].quote == "不应进入共享提示的完整专科原文"
    assert trace.evidence.links[0].target_claim_id == "T001-A001"
    assert trace.evidence.links[0].relation == "supports"
    assert trace.evidence.supporting[0].quote == "不应在主席共享视图中重复的病例原文"
    assert trace.guideline_evidence[0].quote == "不应进入共享提示的完整指南原文"
    assert report.research_metrics.diagnostic_claims == 8
    assert report.research_metrics.claims_with_patient_evidence == 8


def test_v2_report_rejects_unknown_chair_item_reference():
    class FakeLLM:
        supports_json_schema = False

        def complete(self, messages, *, temperature, max_tokens, response_format=None):
            return LLMResponse(
                content=json.dumps(
                    final_report_v2_payload(chair_item_id="IC999"),
                    ensure_ascii=False,
                ),
                raw={"choices": [{}]},
            )

    with pytest.raises(RuntimeError, match="unknown chair item references"):
        FinalReportAgent(
            FakeLLM(),
            config={"max_attempts": 1},
        ).generate(
            case_id="case-1",
            chair_result=expanded_chair_result(),
            rounds=[],
            stop_reason="讨论结束。",
        )


def test_discussion_audit_preserves_answer_review_and_closure():
    propositions, graphs = documents()
    baseline = chair_question_model().model_dump(mode="json")
    task = build_discussion_tasks(
        chair_result=baseline,
        clinical_propositions=propositions,
        local_graphs=graphs,
        round_number=1,
        previous_rounds=[],
    )[0]
    answer = SpecialtyTaskAnswer(
        answer_id="R01-A001-pulmonology",
        task_id=task.task_id,
        issue_type="question",
        issue_id="Q001",
        answerability="answered",
        answer="现有资料只能形成边界性回答。",
        confidence="moderate",
        medical_basis="缺少可区分病因的资料。",
        changed_from_previous=False,
        remaining_limitation="病因仍不可评价。",
    )
    review = SpecialtyAnswerReview(
        review_id="RV001",
        issue_id="Q001",
        answer_id=answer.answer_id,
        reviewer_specialty="thoracic_radiology",
        outcome="accept_boundary",
        rationale="接受当前判断边界。",
    )
    round_item = DiscussionRound(
        round_number=1,
        tasks=[task],
        specialty_responses=[SpecialtyRoundResponse(
            case_id="case-1",
            round_number=1,
            specialty="pulmonology",
            answers=[answer],
        )],
        answer_reviews=[review],
        chair_result=expanded_chair_result(),
        round_decision={"continue_discussion": False},
    )

    audit = build_discussion_audit(
        baseline,
        [round_item],
        "当前仅剩判断边界。",
    )

    assert audit.decisions[0].baseline_result == "当前尚未形成完整回答。"
    assert audit.decisions[0].rounds[0].answer == "现有资料只能形成边界性回答。"
    assert audit.decisions[0].rounds[0].reviews[0]["outcome"] == "accept_boundary"
    assert audit.decisions[0].final_status == "closed"


def test_final_report_cannot_claim_consensus_while_an_issue_remains_open():
    class FakeLLM:
        supports_json_schema = False

        def complete(self, messages, *, temperature, max_tokens, response_format=None):
            content = '''{
                "consensus_status":"consensus_reached",
                "primary_conclusion":"工作诊断",
                "diagnostic_confidence":"中等",
                "integrated_summary":"综合总结",
                "evidence_basis":[],
                "assessment_boundaries":[],
                "unresolved_conflicts":[],
                "evidence_needs":[],
                "discussion_summary":"讨论总结"
            }'''
            return LLMResponse(content=content, raw={"choices": [{}]})

    agent = FinalReportAgent(
        FakeLLM(),
        config={"guideline_retrieval": {"enabled": False}},
    )
    report, _ = agent.generate(
        case_id="case-1",
        chair_result=chair_question_model().model_dump(mode="json"),
        rounds=[],
        stop_reason="本轮未形成新的专科判断或可继续处理的路径。",
    )

    assert report.consensus_status == "unresolved_without_further_progress"


@pytest.mark.parametrize("resolution_status", ["unresolved", "partially_resolved"])
def test_open_question_can_continue_into_the_next_round(resolution_status):
    propositions, graphs = documents()
    first_task = build_discussion_tasks(
        chair_result=chair_question(resolution_status=resolution_status),
        clinical_propositions=propositions,
        local_graphs=graphs,
        round_number=1,
        previous_rounds=[],
    )[0]
    answer = SpecialtyTaskAnswer(
        answer_id="R01-A001-pulmonology",
        task_id=first_task.task_id,
        issue_type="question",
        issue_id="Q001",
        answerability="not_assessable",
        answer="无法进一步归因。",
        confidence="unknown",
        medical_basis="缺少必要证据。",
        changed_from_previous=False,
        remaining_limitation="缺少肺血管评估。",
    )
    previous = DiscussionRound(
        round_number=1,
        tasks=[first_task],
        specialty_responses=[SpecialtyRoundResponse(
            case_id="case-1",
            round_number=1,
            specialty="pulmonology",
            answers=[answer],
        )],
        chair_result={},
    )

    tasks = build_discussion_tasks(
        chair_result=chair_question(resolution_status=resolution_status),
        clinical_propositions=propositions,
        local_graphs=graphs,
        round_number=2,
        previous_rounds=[previous],
    )

    assert len(tasks) == 1
    assert tasks[0].round_number == 2
    assert tasks[0].prior_answers[0]["answer"] == "无法进一步归因。"


def test_round_response_does_not_route_answerer_derivatives():
    answer = SpecialtyTaskAnswer(
        answer_id="R01-Q001-pulmonology-A",
        task_id="R01-Q001-pulmonology",
        issue_type="question",
        issue_id="Q001",
        answerability="answered",
        answer="现有材料支持多因素共同参与，但不能量化相对贡献。",
        confidence="moderate",
        medical_basis="现有检查同时提示肺实质、肺血管和心脏因素。",
        changed_from_previous=True,
        remaining_limitation="相对贡献仍受资料边界限制。",
        new_questions=[{
            "target_specialty": "thoracic_radiology",
            "question": "现有影像表现能否解释低氧程度？",
            "why_it_matters": "区分肺实质与其他因素。",
            "decision_unlocked": "限定肺实质因素的贡献。",
            "related_evidence": [],
        }],
        evidence_gaps=[{
            "available_information": "已有超声心动图摘要。",
            "missing_information": "缺少右心结构和肺动脉压力数据。",
            "why_it_matters": "影响心肺血管因素的相对贡献判断。",
            "decision_unlocked": "提高相对贡献判断的确定性。",
            "related_evidence": [],
        }],
    )
    initial = {
        "pulmonology": {
            "professional_conclusions": {
                "conclusions": [],
                "interspecialty_questions": [],
                "evidence_gaps": [],
            }
        }
    }

    updated = append_round_responses(
        initial,
        [SpecialtyRoundResponse(
            case_id="case-1",
            round_number=1,
            specialty="pulmonology",
            answers=[answer],
        )],
        [SpecialtyAnswerReview(
            review_id=f"{answer.answer_id}-RV-rheumatology",
            issue_id=answer.issue_id,
            answer_id=answer.answer_id,
            reviewer_specialty="rheumatology",
            outcome="accept_answer",
            rationale="回答已覆盖原问题。",
        )],
    )
    assessments = updated["pulmonology"]["specialty_assessments"]
    questions = updated["pulmonology"]["interspecialty_questions"]["questions"]

    assert assessments["assessments"][0]["statement"].startswith("对议题 Q001")
    assert assessments["assessments"][0]["answered_question_id"] == "Q001"
    assert questions == []
    assert assessments["evidence_gaps"] == []
