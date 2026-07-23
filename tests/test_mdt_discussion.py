import json

import pytest

from src.agents.mdt_discussion.final_report import FinalReportAgent
from src.agents.mdt_discussion.models import (
    DiscussionProposition,
    DiscussionRound,
    DiscussionEvidenceUseDraft,
    SpecialtyRoundResponse,
    SpecialtyTaskAnswer,
    SpecialtyTaskAnswerDraft,
)
from src.agents.mdt_discussion.prompt_projection import (
    build_chair_prompt_view,
    build_specialty_initial_prompt_view,
)
from src.agents.mdt_discussion.routing import build_discussion_tasks, group_tasks_by_specialty
from src.agents.mdt_discussion.specialty_agent import (
    SpecialtyDiscussionAgent,
    _resolve_answer,
)
from src.llm.base import LLMResponse


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
            "professional_conclusions": {"marker": "正式专科结论"},
            "clinical_reasoning": {"marker": "不应进入会中提示的内部推理"},
        },
        chair_result=expanded_chair_result(),
    )

    assert answer.task_id == task.task_id
    assert answer.answer_id == f"{task.task_id}-A"
    assert "保留主席语义结论" in trace["prompt"]
    assert "正式专科结论" in trace["prompt"]
    assert "静息低氧" in trace["prompt"]
    assert "gu-1::prop-1" in trace["prompt"]
    assert "不应进入会中提示的内部推理" not in trace["prompt"]
    assert "不应进入共享提示的完整专科原文" not in trace["prompt"]


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


def test_does_not_repeat_an_evidence_blocked_issue_after_one_discussion_attempt():
    propositions, graphs = documents()
    first_task = build_discussion_tasks(
        chair_result=chair_question(resolution_status="blocked_by_evidence"),
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
        chair_result=chair_question(resolution_status="blocked_by_evidence"),
        clinical_propositions=propositions,
        local_graphs=graphs,
        round_number=2,
        previous_rounds=[previous],
    )

    assert tasks == []
