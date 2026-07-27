import json

import pytest

from src.agents.mdt_chair.models import MDTChairIntegration
from src.agents.mdt_discussion.final_report import FinalReportAgent
from src.agents.mdt_discussion.integration import (
    append_round_responses,
    apply_review_outcomes,
)
from src.agents.mdt_discussion.models import (
    DiscussionAnswerClaimDraft,
    DiscussionProposition,
    DiscussionRound,
    DiscussionEvidenceUseDraft,
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
    assert conclusion["guideline_evidence"] == [{
        "chunk_id": "guide:p001:c001",
        "relevance": "相关",
        "application": "用于校准当前判断",
    }]
    compact = json.dumps(view, ensure_ascii=False)
    assert "完整专科原文" not in compact
    assert "病例原文" not in compact
    assert "完整指南原文" not in compact


def test_specialty_initial_prompt_view_keeps_only_formal_conclusions():
    view = build_specialty_initial_prompt_view({
        "professional_conclusions": {"marker": "正式结论"},
        "clinical_reasoning": {"marker": "内部推理"},
    })

    assert view == {"marker": "正式结论"}


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
    assert specialty_view["conclusions"][0]["statement"] == "正式专科结论"
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
                evidence_ref="gu-1:ev-1",
                proposition_ids=[proposition.proposition_id],
                effect="supporting",
                interpretation="证明低氧存在，不能单独证明病因。",
            )],
        )],
        evidence_uses=[DiscussionEvidenceUseDraft(
            evidence_ref="gu-1:ev-1",
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
                        "evidence_ref":"gu-1:ev-1",
                        "proposition_ids":["gu-1::prop-1"],
                        "effect":"supporting",
                        "interpretation":"证明低氧存在，不能单独证明病因。"
                    }],
                    "guideline_evidence":[]
                }],
                "evidence_uses":[{
                    "evidence_ref":"gu-1:ev-1",
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
    assert "始终先回答 `prompt` 中的原始临床问题" in trace["prompt"]
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
    assert question.reviewed_by == ["rheumatology"]


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
    expected = ["gu-1:ev-1"]
    top_level = schema["properties"]["evidence_uses"]["items"]["properties"]
    claim_level = schema["$defs"]["DiscussionAnswerClaimDraft"]["properties"][
        "evidence_uses"
    ]["items"]["properties"]

    for properties in (top_level, claim_level):
        assert properties["evidence_ref"]["enum"] == expected
        assert properties["proposition_ids"]["items"]["enum"] == ["gu-1::prop-1"]


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

    assert properties["evidence_ref"]["enum"] == ["gu-1:ev-1"]
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
    _, trace = agent.generate(
        case_id="case-1",
        chair_result=expanded_chair_result(),
        rounds=[],
        stop_reason="讨论结束。",
    )

    assert "保留主席语义结论" in trace["prompt"]
    assert "保留医学依据" in trace["prompt"]
    assert "不应进入共享提示的完整专科原文" not in trace["prompt"]
    assert "不应在主席共享视图中重复的病例原文" not in trace["prompt"]


@pytest.mark.parametrize("resolution_status", ["unresolved", "partially_resolved"])
def test_does_not_repeat_a_question_after_one_discussion_attempt(resolution_status):
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

    assert tasks == []


def test_round_response_projects_new_questions_and_evidence_gaps():
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
    professional = updated["pulmonology"]["professional_conclusions"]

    assert professional["conclusions"][0]["statement"].startswith("对议题 Q001")
    assert professional["interspecialty_questions"][0]["question"] == (
        "现有影像表现能否解释低氧程度？"
    )
    assert professional["evidence_gaps"][0]["missing_information"] == (
        "缺少右心结构和肺动脉压力数据。"
    )
