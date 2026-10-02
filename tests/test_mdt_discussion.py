"""Self-contained checks for active protocol paths; retired ledger/report tests removed."""

import json

import pytest

from src.agents.common.decision_state import JudgmentChangeProposal, JudgmentContent, SpecialtyJudgmentVersion, SpecialtyJudgmentUpdate, prepare_judgment_change

from src.agents.common.initial_output import SpecialtyAssessment


from src.agents.mdt_discussion.integration import build_judgment_update_inputs, decide_discussion_continuation

from src.agents.mdt_discussion.models import DiscussionAnswerClaimDraft, DiscussionTask, DiscussionProposition, DiscussionEvidenceUseDraft, SpecialtyAnswerReview, SpecialtyRoundResponse, SpecialtyTaskAnswer, SpecialtyTaskAnswerDraft

from src.agents.mdt_discussion.prompt_projection import build_issue_chair_prompt_view, build_specialty_discussion_prompt_view, build_specialty_initial_prompt_view


from src.agents.mdt_discussion.routing import build_discussion_tasks, group_tasks_by_specialty

from src.agents.mdt_discussion.specialty_agent import SpecialtyDiscussionAgent, _resolve_answer, discussion_evidence_schema_constraints

from src.llm.base import LLMResponse

from src.llm.structured import json_schema_response_format


def documents():
    propositions = {
        "segments": [{
            "units": [{
                "graph_unit_id": "gu-1",
                "evidence_blocks": [{"evidence_id": "ev-1", "text": "静息低氧"}],
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


def current_chair_question():
    from test_expert_protocol import setup_case, run_expert
    _, bundle, team = setup_case()
    team['issues'] = [dict(issue_id='Q001', origin='chair', kind='clarification',
        question='低氧的主要归因是什么？', target_specialties=['pulmonology'],
        assignments=[dict(specialty='pulmonology', question='低氧的主要归因是什么？')],
        source_refs=[team['judgment_reviews'][0]['source_ref']],
        decision_impact='影响低氧归因', closure_criterion='区分肺实质与肺血管因素。')]
    return run_expert(bundle, [team]).model_dump(mode='json')


def chair_question_model():
    from src.agents.common.team_synthesis import MDTChairResult
    return MDTChairResult.model_validate(current_chair_question())


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
    chair = current_chair_question()
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

    assert chair_view["issue"]["issue_id"] == "Q001"
    assert "保留主席语义结论" not in json.dumps(chair_view, ensure_ascii=False)
    assert specialty_view["specialty_assessments"][0]["statement"] == "正式专科结论"
    assert "不应重复传入的原文" not in json.dumps(specialty_view, ensure_ascii=False)


def test_routes_declared_specialties_and_builds_an_evidence_analysis_packet():
    propositions, graphs = documents()
    tasks = build_discussion_tasks(
        chair_result=current_chair_question(),
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
        chair_result=current_chair_question(),
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
        chair_result=current_chair_question(),
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
        chair_result=current_chair_question(),
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
            assert max_tokens == 16000
            return LLMResponse(
                content='{"outcome":"accept_boundary","rationale":"已明确当前资料边界。"}',
                raw={"choices": [{}]},
            )

    propositions, graphs = documents()
    task = build_discussion_tasks(
        chair_result=current_chair_question(),
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


def test_requester_review_is_available_when_requester_updates_its_judgments():
    class FakeLLM:
        supports_json_schema = False

        def complete(self, messages, *, temperature, max_tokens, response_format=None):
            return LLMResponse(
                content='{"proposals":[]}',
                raw={"choices": [{}]},
            )

    propositions, graphs = documents()
    task = build_discussion_tasks(
        chair_result=current_chair_question(),
        clinical_propositions=propositions,
        local_graphs=graphs,
        round_number=1,
        previous_rounds=[],
    )[0]
    answer = SpecialtyTaskAnswer(
        answer_id=f"{task.task_id}-A",
        task_id=task.task_id,
        issue_type=task.issue_type,
        issue_id=task.issue_id,
        answerability="partially_answered",
        answer="现有资料只能确认低氧，不能完成病因归因。",
        confidence="moderate",
        medical_basis="缺少肺血管资料。",
        changed_from_previous=False,
    )
    review = SpecialtyAnswerReview(
        review_id=f"{answer.answer_id}-RV-rheumatology",
        issue_id=answer.issue_id,
        answer_id=answer.answer_id,
        reviewer_specialty="rheumatology",
        outcome="accept_boundary",
        rationale="接受当前证据边界，并重新限定本专科判断。",
    )
    agent = SpecialtyDiscussionAgent(
        FakeLLM(),
        specialty="rheumatology",
        config={"guideline_retrieval": {"enabled": False}},
    )

    update, trace = agent.propose_judgment_update(
        round_number=1,
        tasks=[task],
        answers=[answer],
        active_judgments=[],
        reviews=[review],
    )

    assert update.specialty == "rheumatology"
    assert update.proposals == []
    assert answer.answer in trace["prompt"]
    assert review.rationale in trace["prompt"]


def test_judgment_update_preserves_validated_patient_and_guideline_locations():
    assessment = SpecialtyAssessment.model_validate({
        "assessability": "partially_assessable", "direction": "supports", "confidence": "moderate", "clinical_role": "primary",
        "assessment_id": "pulmonology_001",
        "role": "primary",
        "assessment_type": "working_diagnosis",
        "statement": "现有资料支持纤维化性间质性肺病。",
        "status": "favored",
        "medical_basis": "病例文字支持该判断。",
        "decision_impact": "进入模式和病因层面的整合。",
        "claims": [{"claim_id": "claim-1", "statement": "存在间质性肺病。"}],
        "evidence": {"evidence_relations": [{
            "evidence_ids": ["ev-1"],
            "segment_id": "seg-1",
            "graph_unit_id": "gu-1",
            "node_ids": ["gu-1::prop-1"],
            "quote": "双肺纤维化改变",
            "direction": "supports",
            "function": "foundational",
        }]},
        "guideline_evidence": [{
            "chunk_id": "guide-chunk-1",
            "quote_unit_ids": ["quote-1"],
            "relevance": "定义当前诊断层级。",
            "application": "用于限定本病例结论。",
            "quote": "已核验的指南原文",
            "guideline_id": "guide-1",
            "title": "ILD guideline",
        }],
    })
    current = SpecialtyJudgmentVersion(
        judgment_id="pulmonology_001",
        version_id="pulmonology_001@v001",
        version_number=1,
        specialty="pulmonology",
        change_type="initial",
        assessment=assessment,
        content_hash="hash",
    )
    content = JudgmentContent.from_assessment(assessment)
    content.medical_basis += " 本轮回答补充了同一判断的解释。"
    proposal = JudgmentChangeProposal(
        change_type="supplement",
        target_judgment_id=current.judgment_id,
        base_version_id=current.version_id,
        proposed_content=content,
        rationale="补充本轮讨论形成的解释。",
        trigger_issue_ids=["Q001"],
        considered_source_refs=["R01-Q001-pulmonology-A"],
    )

    class FakeLLM:
        supports_json_schema = False

        def complete(self, messages, *, temperature, max_tokens, response_format=None):
            return LLMResponse(
                content=json.dumps({"proposals": [proposal.model_dump(mode="json")]}),
                raw={"choices": [{}]},
            )

    task = DiscussionTask(
        task_id="R01-Q001-pulmonology",
        round_number=1,
        issue_type="question",
        issue_id="Q001",
        specialty="pulmonology",
        prompt="当前判断是否需要更新？",
    )
    answer = SpecialtyTaskAnswer(
        answer_id=f"{task.task_id}-A",
        task_id=task.task_id,
        issue_type=task.issue_type,
        issue_id=task.issue_id,
        answerability="answered",
        answer="补充解释，但核心判断不变。",
        confidence="moderate",
        medical_basis="沿用已核验资料。",
        changed_from_previous=False,
    )
    agent = SpecialtyDiscussionAgent(
        FakeLLM(),
        specialty="pulmonology",
        config={
            "max_attempts": 1,
            "guideline_retrieval": {"enabled": False},
        },
    )

    update, _ = agent.propose_judgment_update(
        round_number=1,
        tasks=[task],
        answers=[answer],
        active_judgments=[current],
    )
    resolved = update.proposals[0].proposed_content

    assert resolved.evidence.evidence_relations[0].node_ids == ["gu-1::prop-1"]
    assert resolved.guideline_evidence[0].quote == "已核验的指南原文"
    assert resolved.guideline_evidence[0].guideline_id == "guide-1"

    proposal.proposed_content.guideline_evidence[0].chunk_id = "invented-chunk"
    update, _ = agent.propose_judgment_update(
        round_number=1,
        tasks=[task],
        answers=[answer],
        active_judgments=[current],
    )
    assert update.proposals[0].proposed_content.guideline_evidence == []
    preserved, _ = prepare_judgment_change(current, update.proposals[0])
    assert preserved.guideline_evidence[0].guideline_id == "guide-1"


def test_judgment_update_repairs_change_type_before_committing_core_change():
    assessment = SpecialtyAssessment.model_validate({
        "assessability": "partially_assessable", "direction": "supports", "confidence": "moderate", "clinical_role": "primary",
        "assessment_id": "pathology_001",
        "role": "primary",
        "assessment_type": "working_diagnosis",
        "statement": "现有切片支持纤维化。",
        "status": "favored",
        "medical_basis": "依据现有病理资料。",
        "decision_impact": "参与综合判断。",
    })
    current = SpecialtyJudgmentVersion(
        judgment_id="pathology_001",
        version_id="pathology_001@v001",
        version_number=1,
        specialty="pathology",
        change_type="initial",
        assessment=assessment,
        content_hash="hash",
    )
    content = JudgmentContent.from_assessment(assessment)
    content.statement = "现有切片支持纤维化，但不足以判定特定病理模式。"
    proposal = JudgmentChangeProposal(
        change_type="supplement",
        target_judgment_id=current.judgment_id,
        base_version_id=current.version_id,
        proposed_content=content,
        rationale="结合本轮讨论修正病理结论。",
        trigger_issue_ids=["Q001"],
        considered_source_refs=["Q001"],
    )

    class FakeLLM:
        supports_json_schema = False

        def __init__(self):
            self.calls = 0

        def complete(self, messages, *, temperature, max_tokens, response_format=None):
            self.calls += 1
            payload = (
                {"proposals": [proposal.model_dump(mode="json")]}
                if self.calls == 1
                else {"edits": [
                    {"op": "replace", "path": "/proposals/0/considered_source_refs", "value": ["R01-Q001-pathology-A"]},
                ]}
            )
            return LLMResponse(content=json.dumps(payload), raw={"choices": [{}]})

    llm = FakeLLM()
    task = DiscussionTask(
        task_id="R01-Q001-pathology",
        round_number=1,
        issue_type="question",
        issue_id="Q001",
        specialty="pathology",
        prompt="本轮讨论是否改变病理判断？",
    )
    answer = SpecialtyTaskAnswer(
        answer_id=f"{task.task_id}-A",
        task_id=task.task_id,
        issue_type=task.issue_type,
        issue_id=task.issue_id,
        answerability="answered",
        answer="需要收窄病理模式结论。",
        confidence="moderate",
        medical_basis="现有切片不充分。",
        changed_from_previous=True,
    )
    agent = SpecialtyDiscussionAgent(
        llm,
        specialty="pathology",
        config={"max_attempts": 2, "guideline_retrieval": {"enabled": False}},
    )

    update, trace = agent.propose_judgment_update(
        round_number=1,
        tasks=[task],
        answers=[answer],
        active_judgments=[current],
    )

    assert llm.calls == 2
    assert len(trace["attempts"]) == 2
    assert "no validated considered source refs" in trace["attempts"][0]["validation_error"]
    assert update.proposals[0].change_type == "revise"
    assert update.proposals[0].proposed_content.statement == content.statement


def test_judgment_updates_include_both_answerer_and_question_requester():
    propositions, graphs = documents()
    task = build_discussion_tasks(
        chair_result=current_chair_question(),
        clinical_propositions=propositions,
        local_graphs=graphs,
        round_number=1,
        previous_rounds=[],
    )[0]
    answer = SpecialtyTaskAnswer(
        answer_id=f"{task.task_id}-A",
        task_id=task.task_id,
        issue_type=task.issue_type,
        issue_id=task.issue_id,
        answerability="partially_answered",
        answer="现有资料只能形成边界性回答。",
        confidence="moderate",
        medical_basis="缺少可区分病因的资料。",
        changed_from_previous=False,
    )
    response = SpecialtyRoundResponse(
        case_id="case-1",
        round_number=1,
        specialty=task.specialty,
        answers=[answer],
    )
    review = SpecialtyAnswerReview(
        review_id=f"{answer.answer_id}-RV-thoracic_radiology",
        issue_id=answer.issue_id,
        answer_id=answer.answer_id,
        reviewer_specialty="thoracic_radiology",
        outcome="accept_boundary",
        rationale="接受当前判断边界。",
    )

    inputs = build_judgment_update_inputs([task], [response], [review])

    assert set(inputs) == {task.specialty, "thoracic_radiology"}
    assert inputs[task.specialty][0][1].answer_id == answer.answer_id
    assert inputs["thoracic_radiology"][0][1].answer_id == answer.answer_id


def test_round_continuation_counts_requester_owned_judgment_changes():
    previous = chair_question_model()
    current = previous.model_copy(deep=True)
    requester_update = SpecialtyJudgmentUpdate(
        specialty="thoracic_radiology",
        transaction_id="R01-thoracic_radiology-judgment-update",
        proposals=[JudgmentChangeProposal(
            change_type="withdraw",
            target_judgment_id="thoracic_radiology_001",
            base_version_id="thoracic_radiology_001@v001",
            rationale="目标专科回答表明原判断不再成立。",
            trigger_issue_ids=["Q001"],
            considered_source_refs=["R01-Q001-pulmonology-A"],
        )],
    )

    decision = decide_discussion_continuation(
        previous=previous,
        current=current,
        round_number=1,
        max_rounds=3,
        responses=[],
        reviews=[],
        judgment_updates=[requester_update],
    )

    assert decision["continue_discussion"] is True
    assert decision["changed_judgments"] == 1


def test_discussion_schema_closes_evidence_uses_to_task_candidates():
    propositions, graphs = documents()
    task = build_discussion_tasks(
        chair_result=current_chair_question(),
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
        chair_result=current_chair_question(),
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
    from src.agents.mdt_discussion.routing import _validate_evidence_candidates
    from src.agents.mdt_discussion.models import DiscussionEvidenceCandidate
    with pytest.raises(ValueError, match="missing source text"):
        _validate_evidence_candidates([DiscussionEvidenceCandidate(evidence_ref='E1', graph_unit_id='gu-1', evidence_ids=['ev-1'])])


def test_task_without_selected_evidence_still_receives_the_patient_catalog():
    propositions, graphs = documents()
    question = current_chair_question()
    assert not question['team_synthesis']['issues'][0]['evidence_refs']
    tasks = build_discussion_tasks(chair_result=question, clinical_propositions=propositions,
                                  local_graphs=graphs, round_number=1, previous_rounds=[])
    assert len(tasks) == 1
    assert tasks[0].evidence_candidates[0].quote == '静息低氧'


def test_judgment_update_evidence_deduplication_is_lossless():
    from copy import deepcopy
    from src.llm.prompting import build_shared_prompt_view
    evidence = dict(evidence_ref='E1', segment_id='S1', graph_unit_id='U1',
        evidence_ids=['E1'], quote='完整病例原文' * 1000,
        evidence_fragments=[dict(text='原文片段')], propositions=[dict(statement='原始命题')],
        graph_nodes=[dict(node_id='N1')], graph_edges=[])
    use = dict(evidence, effect='supporting', interpretation='支持当前判断', proposition_ids=['P1'])
    # Distinct per-use content must override the shared source rather than disappear.
    alternate = dict(use, quote='不同引用范围', graph_nodes=[dict(node_id='N2')])
    original = [dict(task=dict(evidence_candidates=[evidence]),
        answer=dict(evidence_uses=[use, alternate], answer_claims=[dict(evidence_uses=[use])]))] * 2
    saved = deepcopy(original)
    view = build_shared_prompt_view(original)

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) == {'shared_value_ref'}:
            return expand(view['shared_values'][value['shared_value_ref']])
        return {key: expand(item) for key, item in value.items()}

    assert expand(view['input']) == original == saved
    assert len(json.dumps(view)) < len(json.dumps(original)) / 2


def test_incomplete_judgment_update_feedback_names_missing_targets_and_append_operation():
    from src.llm.structured import StructuredGenerationError
    assessment = SpecialtyAssessment.model_validate(dict(
        assessment_id='a1', role='primary', assessment_type='working_diagnosis',
        statement='有限工作判断', assessability='partially_assessable', direction='supports',
        confidence='low', clinical_role='primary', status='possible', medical_basis='原文',
        decision_impact='病因定位', evidence={'evidence_relations': []}))
    judgments = [SpecialtyJudgmentVersion(judgment_id=ref, version_id=ref+'@v001',
        version_number=1, specialty='pulmonology', change_type='initial',
        assessment=assessment, content_hash='hash') for ref in ['current1', 'current2']]
    proposals = [dict(change_type='maintain', target_judgment_id=j.judgment_id,
        base_version_id=j.version_id, proposed_content=None, rationale='本轮判断不变',
        trigger_issue_ids=[], considered_source_refs=[]) for j in judgments]
    class FakeLLM:
        supports_json_schema = False
        items = proposals[:1]
        def complete(self, messages, **kwargs):
            return LLMResponse(content=json.dumps({'proposals': self.items}), raw={})
    llm = FakeLLM()
    agent = SpecialtyDiscussionAgent(llm, specialty='pulmonology', config={
        'protocol_version': 'expert.v1', 'max_attempts': 1, 'guideline_retrieval': {'enabled': False}})
    with pytest.raises(StructuredGenerationError) as failure:
        agent.propose_judgment_update(round_number=1, tasks=[], answers=[], active_judgments=judgments)
    error = failure.value.attempts[0]['validation_error']
    assert "missing=['current2']" in error
    assert 'current2@v001' in error
    assert 'append' in error and '/proposals' in error
    llm.items = proposals
    update, _ = agent.propose_judgment_update(round_number=1, tasks=[], answers=[], active_judgments=judgments)
    assert len(update.proposals) == 2
