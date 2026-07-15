import json

import pytest
from pydantic import ValidationError

from scripts.agent_input.prepare_specialty_input import build_specialty_case_input
from src.agents.thoracic_radiology.agent import (
    ThoracicRadiologyAgent,
    validate_initial_assessment,
)
from src.agents.thoracic_radiology.evidence_projection import (
    build_radiology_working_input,
)
from src.agents.thoracic_radiology.models import (
    CaseOrientation,
    ChairAnswer,
    CoreConsultAnswer,
    DiscussionEvidenceMap,
    DiscussionUpdateAndConsult,
    EvidencePointer,
    ReviewDomainCoverage,
    ImagingExamination,
    InitialCaseReconstruction,
    InitialConsultFormulation,
    MappedSpecialistFinding,
    RadiologyActionItem,
    RadiologyTask,
    RadiologyTaskAssessment,
    ReportedImagingStatement,
    SpecialistClaim,
    SpecialistOpinion,
    TaskPlanItem,
    TaskUpdate,
    ThoracicRadiologyDiscussionInput,
    ThoracicRadiologyDomain,
    ThoracicRadiologyInitialAssessment,
)
from src.agents.thoracic_radiology.validation import (
    resolve_proposition_pointers,
    validate_case_reconstruction,
    validate_update_and_consult,
)
from src.llm.base import LLMResponse
from src.reporting.thoracic_radiology_report import render_thoracic_radiology_report
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty


RUN_0714 = "outputs/runs/20260714_163246_76-IPF_step2_step3"
RUN_0715 = "outputs/runs/20260715_121324_77-IPF_step2_step3"
CONFIG = "configs/agents/thoracic_radiology/agent.yaml"


class FakeLLM:
    supports_json_schema = False

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def complete(self, messages, *, temperature, max_tokens, response_format=None):
        self.prompts.append(messages)
        content = json.dumps(self.responses.pop(0), ensure_ascii=False)
        return LLMResponse(
            content=content,
            raw={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
        )


def case_0714():
    return build_specialty_case_input(RUN_0714, MdtSpecialty.THORACIC_RADIOLOGY)


def case_0715():
    return build_specialty_case_input(RUN_0715, MdtSpecialty.THORACIC_RADIOLOGY)


def pointer(unit_id, *proposition_ids):
    return EvidencePointer(graph_unit_id=unit_id, proposition_ids=list(proposition_ids))


def llm_payload(model):
    program_fields = {
        "evidence_ids",
        "segment_id",
        "node_ids",
        "resolved_quotes",
        "quote",
    }

    def strip(value):
        if isinstance(value, dict):
            return {key: strip(item) for key, item in value.items() if key not in program_fields}
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value

    return strip(model.model_dump(mode="json"))


def coverage():
    return [
        ReviewDomainCoverage(
            domain=domain,
            status="addressed_by_active_task",
            rationale="已在问题驱动任务中完成审阅。",
        )
        for domain in ThoracicRadiologyDomain
    ]


def reconstruction_0714():
    return InitialCaseReconstruction(
        orientation=CaseOrientation(
            clinical_trigger="慢性咳嗽气短近期加重，CT同时描述间质异常和局灶感染性病灶。",
            primary_imaging_question="现有文字能否区分慢性间质异常与急性局灶病变？",
            secondary_imaging_questions=["两段CT描述是否属于同一次检查？"],
            urgency="expedited",
        ),
        examinations=[
            ImagingExamination(
                exam_id="exam_001",
                temporal_anchor="2月前",
                modality="ct",
                purpose="acute_deterioration",
                source_authority="clinician_paraphrase",
                evidence_level="feature_level",
                description="当地胸部CT在病程叙事中的转述。",
                possible_same_exam_as=["exam_002"],
                relationship_note="与后续独立CT结论可能相关，但原文未明确是否同次。",
                source_evidence=[pointer("seg_003_gu_003", "prop_006")],
            ),
            ImagingExamination(
                exam_id="exam_002",
                temporal_anchor="时间未提供",
                modality="ct",
                purpose="ild_characterization",
                source_authority="formal_report",
                evidence_level="impression_level",
                description="独立成段的胸部CT结论。",
                possible_same_exam_as=["exam_001"],
                relationship_note="可能与病程中的当地CT重复，不能作为明确比较片。",
                source_evidence=[pointer("seg_004_gu_001", "prop_001")],
            ),
        ],
        reported_statements=[
            ReportedImagingStatement(
                statement_id="stmt_001",
                exam_id="exam_001",
                statement_type="finding",
                origin="clinician_paraphrase",
                text="双肺间质增粗、纹理走形杂乱。",
                assertion_status="reported_present",
                certainty="high",
                evidence=[pointer("seg_003_gu_003", "prop_006")],
            ),
            ReportedImagingStatement(
                statement_id="stmt_002",
                exam_id="exam_001",
                statement_type="finding",
                origin="clinician_paraphrase",
                text="右中下叶及左舌段条片状实性高密度影。",
                assertion_status="reported_present",
                certainty="high",
                evidence=[pointer("seg_003_gu_003", "prop_008", "prop_009")],
            ),
            ReportedImagingStatement(
                statement_id="stmt_003",
                exam_id="exam_001",
                statement_type="impression",
                origin="clinician_paraphrase",
                text="原文考虑肺部感染。",
                assertion_status="reported_possible",
                certainty="moderate",
                evidence=[pointer("seg_003_gu_003", "prop_010")],
            ),
            ReportedImagingStatement(
                statement_id="stmt_004",
                exam_id="exam_002",
                statement_type="finding",
                origin="formal_report",
                text="右中下叶感染性病灶、左舌段局限性不张。",
                assertion_status="reported_present",
                certainty="high",
                evidence=[pointer("seg_004_gu_001", "prop_002", "prop_003")],
            ),
        ],
        task_plan=[
            TaskPlanItem(
                task=RadiologyTask.ACUTE_PARENCHYMAL_OVERLAY,
                priority="primary",
                activation="active",
                rationale="近期加重且CT描述局灶实性病变。",
            ),
            TaskPlanItem(
                task=RadiologyTask.SOURCE_RECONCILIATION,
                priority="secondary",
                activation="active",
                rationale="两段CT描述关系不明确。",
            ),
            TaskPlanItem(
                task=RadiologyTask.ILD_MORPHOLOGIC_PATTERN,
                priority="conditional",
                activation="conditional",
                rationale="文字缺少完成UIP/NSIP分类所需的关键分布和征象。",
            ),
        ],
        excluded_candidate_notes=[],
        limitations=["没有直接阅片，两个CT来源的关系不明确。"],
    )


def formulation_0714():
    return InitialConsultFormulation(
        task_assessments=[
            RadiologyTaskAssessment(
                task=RadiologyTask.ACUTE_PARENCHYMAL_OVERLAY,
                priority="primary",
                answerability="partially_answered",
                conclusion="文字支持慢性间质异常背景上存在右中下叶局灶感染性病灶及左舌段不张。",
                confidence="moderate",
                reasoning_summary="局灶实性影、边缘模糊及报告感染印象相互一致，但不能由文字独立确定病因。",
                reported_statement_ids=["stmt_002", "stmt_003", "stmt_004"],
                supporting_evidence=[
                    pointer("seg_003_gu_003", "prop_008", "prop_009"),
                    pointer("seg_004_gu_001", "prop_002", "prop_003"),
                ],
                limitations=["抗感染后症状改善不明显不能反向否定影像感染印象。"],
                decision_impact="MDT需要把慢性间质异常与局灶急性病变分开处理。",
            ),
            RadiologyTaskAssessment(
                task=RadiologyTask.SOURCE_RECONCILIATION,
                priority="secondary",
                answerability="not_answerable",
                conclusion="两段CT文字可能相关，但不能确认是否同一次检查。",
                confidence="low",
                reasoning_summary="独立报告没有时间锚点，也没有明确比较语句。",
                reported_statement_ids=["stmt_001", "stmt_004"],
                supporting_evidence=[
                    pointer("seg_003_gu_003", "prop_006"),
                    pointer("seg_004_gu_001", "prop_001"),
                ],
                decision_impact="不能把两段文字用于稳定、进展或改善判断。",
            ),
            RadiologyTaskAssessment(
                task=RadiologyTask.ILD_MORPHOLOGIC_PATTERN,
                priority="conditional",
                answerability="not_answerable",
                conclusion="现有文字不能完成UIP、NSIP或其他ILD形态模式分类。",
                confidence="high",
                reasoning_summary="仅有间质增粗标签，缺少关键纤维化征象和完整分布。",
                reported_statement_ids=["stmt_001"],
                supporting_evidence=[pointer("seg_003_gu_003", "prop_006")],
                decision_impact="不能用本次文字直接确认IPF或其他病因。",
            ),
        ],
        core_answer=CoreConsultAnswer(
            primary_question="现有文字能否区分慢性间质异常与急性局灶病变？",
            answer="可以确认两类描述并存，但不能由文字独立确定局灶病变病因，也不能完成ILD模式分型。",
            confidence="moderate",
            decision_impact="急性局灶病变与慢性间质背景应分层讨论。",
            decisive_next_step="核对两段CT是否同次并直接比较原始图像。",
        ),
        review_coverage=coverage(),
        action_items=[
            RadiologyActionItem(
                action="核对两段CT的检查日期并直接比较原始图像。",
                reason="现有文字无法确认检查关系和局灶病变变化。",
                decision_unlocked="区分同次重复描述与真实纵向变化。",
                priority="high",
            )
        ],
        limitations=["本意见仅基于影像文字描述。"],
    )


def assessment_0714():
    reconstruction = reconstruction_0714()
    formulation = formulation_0714()
    return ThoracicRadiologyInitialAssessment(
        case_id="76-IPF",
        reconstruction=reconstruction,
        task_assessments=formulation.task_assessments,
        core_answer=formulation.core_answer,
        review_coverage=formulation.review_coverage,
        action_items=formulation.action_items,
        limitations=[*reconstruction.limitations, *formulation.limitations],
    )


def assessment_0715():
    reconstruction = InitialCaseReconstruction(
        orientation=CaseOrientation(
            clinical_trigger="膝关节术后严重低氧，急诊行CTPA。",
            primary_imaging_question="CTPA对肺栓塞及低氧相关胸部异常回答到什么程度？",
            urgency="urgent",
        ),
        examinations=[
            ImagingExamination(
                exam_id="exam_001",
                temporal_anchor="2025-10-19",
                modality="ctpa",
                purpose="pulmonary_embolism",
                source_authority="report_excerpt",
                evidence_level="impression_level",
                description="急诊肺动脉CT报告摘录。",
                source_evidence=[pointer("seg_004_gu_003", "prop_001")],
            )
        ],
        reported_statements=[
            ReportedImagingStatement(
                statement_id="stmt_pe",
                exam_id="exam_001",
                statement_type="finding",
                origin="report_excerpt",
                text="未见明确中央型肺栓塞直接征象。",
                assertion_status="reported_absent",
                certainty="high",
                evidence=[pointer("seg_004_gu_003", "prop_001")],
            ),
            ReportedImagingStatement(
                statement_id="stmt_ild",
                exam_id="exam_001",
                statement_type="finding",
                origin="report_excerpt",
                text="双肺间质纤维化并肺气肿。",
                assertion_status="reported_present",
                certainty="high",
                evidence=[pointer("seg_004_gu_003", "prop_003", "prop_004")],
            ),
        ],
        task_plan=[
            TaskPlanItem(
                task=RadiologyTask.TARGETED_PULMONARY_VASCULAR,
                priority="primary",
                activation="active",
                rationale="术后严重低氧且临床待排肺栓塞。",
            ),
            TaskPlanItem(
                task=RadiologyTask.ILD_PHENOTYPE,
                priority="secondary",
                activation="active",
                rationale="CTPA同时报告纤维化和肺气肿。",
            ),
        ],
        excluded_candidate_notes=["术前肺功能、心超和下肢超声不属于胸部影像观察。"],
    )
    pe = RadiologyTaskAssessment(
        task=RadiologyTask.TARGETED_PULMONARY_VASCULAR,
        priority="primary",
        answerability="partially_answered",
        conclusion="报告仅说明未见明确中央型肺栓塞直接征象，不能扩大为排除全部肺栓塞。",
        confidence="high",
        reasoning_summary="文字限定于中央型直接征象，未提供远端血管和技术质量信息。",
        reported_statement_ids=["stmt_pe"],
        supporting_evidence=[pointer("seg_004_gu_003", "prop_001")],
        decision_impact="若临床怀疑仍高，需要核实CTPA质量和远端肺动脉评价。",
    )
    ild = RadiologyTaskAssessment(
        task=RadiologyTask.ILD_PHENOTYPE,
        priority="secondary",
        answerability="partially_answered",
        conclusion="报告支持纤维化性ILD合并肺气肿背景，但不足以完成形态分型。",
        confidence="moderate",
        reasoning_summary="纤维化为标签性描述。",
        reported_statement_ids=["stmt_ild"],
        supporting_evidence=[pointer("seg_004_gu_003", "prop_003", "prop_004")],
        decision_impact="慢性背景可影响低氧解释，但不能由本摘录确认IPF。",
    )
    return ThoracicRadiologyInitialAssessment(
        case_id="77-IPF",
        reconstruction=reconstruction,
        task_assessments=[pe, ild],
        core_answer=CoreConsultAnswer(
            primary_question="CTPA对肺栓塞及低氧相关胸部异常回答到什么程度？",
            answer="未见明确中央型肺栓塞直接征象，但不能据此排除全部肺栓塞。",
            confidence="high",
            decision_impact="仍需结合CTPA质量、远端血管和临床概率判断。",
            decisive_next_step="若临床怀疑仍高，复核CTPA技术质量和远端肺动脉。",
        ),
        review_coverage=coverage(),
    )


def test_initial_assessment_runs_two_problem_oriented_stages():
    case = case_0714()
    reconstruction = reconstruction_0714()
    formulation = formulation_0714()
    llm = FakeLLM([llm_payload(reconstruction), llm_payload(formulation)])
    agent = ThoracicRadiologyAgent.from_config(CONFIG, llm)

    result, trace = agent.initial_assessment(case)

    assert result.schema_version == "thoracic_radiology.v2"
    assert result.core_answer.primary_question.startswith("现有文字")
    assert [item["stage"] for item in trace["stages"]] == [
        "initial_case_reconstruction",
        "initial_consult_formulation",
    ]
    assert len(llm.prompts) == 2
    assert "local_graph" not in llm.prompts[0][1].content
    resolved = result.reconstruction.reported_statements[0].evidence[0]
    assert resolved.quote == "双肺间质增粗纹理走形杂乱"
    assert resolved.node_ids == ["seg_003_gu_003::prop_006"]


def test_legacy_initial_can_enter_v2_discussion_without_old_route_errors():
    legacy = ThoracicRadiologyInitialAssessment.model_validate(
        {
            "schema_version": "thoracic_radiology.v1",
            "case_id": "77-IPF",
            "source_state": {
                "reasoning_summary": "旧版来源重建。",
                "examinations": [
                    {
                        "exam_id": "exam_echo",
                        "temporal_anchor": "2025-10-10",
                        "modality": "other",
                        "source_authority": "clinician_paraphrase",
                        "description_sufficiency": "partial",
                        "assessment": "超声心动图结果。",
                        "supporting_evidence": [
                            {
                                "graph_unit_id": "seg_003_gu_001",
                                "evidence_ids": ["seg_003_gu_001_ev_002"],
                            }
                        ],
                    },
                    {
                        "exam_id": "exam_ctpa",
                        "temporal_anchor": "2025-10-19",
                        "modality": "ct",
                        "source_authority": "report_excerpt",
                        "description_sufficiency": "partial",
                        "assessment": "肺动脉CT报告摘录。",
                        "supporting_evidence": [
                            {
                                "graph_unit_id": "seg_004_gu_003",
                                "evidence_ids": [
                                    "seg_004_gu_003_ev_001",
                                    "seg_004_gu_003_ev_003",
                                ],
                            }
                        ],
                    },
                ],
            },
            "observation_state": {
                "observations": [
                    {
                        "finding": "CTPA未见明确中央型肺栓塞直接征象",
                        "status": "reported_absent",
                        "confidence": "high",
                        "supporting_evidence": [
                            {
                                "graph_unit_id": "seg_004_gu_003",
                                "evidence_ids": ["seg_004_gu_003_ev_001"],
                            }
                        ],
                    },
                    {
                        "finding": "双肺间质纤维化",
                        "status": "reported_present",
                        "confidence": "high",
                        "supporting_evidence": [
                            {
                                "graph_unit_id": "seg_004_gu_003",
                                "evidence_ids": ["seg_004_gu_003_ev_003"],
                            }
                        ],
                    },
                ]
            },
            "interpretation_state": {
                "morphologic_pattern": {
                    "classification_status": "not_assessable",
                    "confidence": "moderate",
                    "reasoning_summary": "文字不足以完成形态分型。",
                    "supporting_evidence": [
                        {
                            "graph_unit_id": "seg_004_gu_003",
                            "evidence_ids": ["seg_004_gu_003_ev_003"],
                        }
                    ],
                }
            },
        }
    )

    migrated = validate_initial_assessment(legacy, case_0715())

    assert migrated.legacy_import is True
    assert [item.exam_id for item in migrated.reconstruction.examinations] == [
        "exam_ctpa"
    ]
    assert len(migrated.reconstruction.reported_statements) == 2
    assert all(
        pointer.proposition_ids
        for item in migrated.reconstruction.reported_statements
        for pointer in item.evidence
    )
    assert "legacy_import" not in migrated.model_dump(mode="json")


def test_direct_image_review_can_never_be_claimed():
    with pytest.raises(ValidationError, match="direct_images_reviewed"):
        ImagingExamination(
            exam_id="exam",
            temporal_anchor="当前",
            modality="ct",
            source_authority="report_excerpt",
            evidence_level="impression_level",
            description="错误声称阅片。",
            source_evidence=[pointer("unit", "prop_001")],
            direct_images_reviewed=True,
        )


def test_misrouted_echo_proposition_cannot_enter_thoracic_exam():
    case = case_0715()
    reconstruction = assessment_0715().reconstruction.model_copy(deep=True)
    reconstruction.examinations[0].source_evidence = [
        pointer("seg_003_gu_001", "prop_007")
    ]

    with pytest.raises(ValueError, match="non-thoracic or ineligible"):
        validate_case_reconstruction(
            reconstruction, case, build_radiology_working_input(case)
        )


def test_central_pe_negative_cannot_be_expanded_to_complete_exclusion():
    case = case_0715()
    result = assessment_0715().model_copy(deep=True)
    result.core_answer.answer = "CTPA已经排除肺栓塞。"

    with pytest.raises(ValueError, match="central-PE-only"):
        validate_initial_assessment(result, case)


def test_ipf_hrct_task_cannot_activate_without_explicit_ipf_context():
    case = case_0714()
    reconstruction = reconstruction_0714().model_copy(deep=True)
    reconstruction.task_plan.append(
        TaskPlanItem(
            task=RadiologyTask.CONDITIONAL_IPF_HRCT,
            priority="conditional",
            activation="active",
            rationale="错误地机械启动IPF分类。",
        )
    )

    with pytest.raises(ValueError, match="conditional_ipf_hrct"):
        validate_case_reconstruction(
            reconstruction, case, build_radiology_working_input(case)
        )


def test_unknown_proposition_id_is_rejected():
    case = case_0714()
    evidence = pointer("seg_003_gu_003", "prop_999")

    with pytest.raises(ValueError, match="unknown proposition_ids"):
        resolve_proposition_pointers(evidence, case)


def test_non_radiology_opinion_cannot_add_reported_content():
    case = case_0714()
    initial = validate_initial_assessment(assessment_0714(), case)
    rheum_unit = next(
        unit
        for segment in case.segments
        for unit in segment.units
        if MdtSpecialty.RHEUMATOLOGY in unit.graph_unit.mdt_specialty
    )
    prop_id = rheum_unit.clinical_propositions.propositions[0].proposition_id
    opinion = SpecialistOpinion(
        specialty=MdtSpecialty.RHEUMATOLOGY,
        opinion_id="rheum-001",
        summary="正式风湿意见",
        claims=[
            SpecialistClaim(
                claim="风湿背景补充",
                evidence=[pointer(rheum_unit.graph_unit.graph_unit_id, prop_id)],
            )
        ],
        confidence="moderate",
    )
    discussion_input = ThoracicRadiologyDiscussionInput(
        case_input=case,
        initial_assessment=initial,
        specialist_opinions=[opinion],
    )
    update = DiscussionUpdateAndConsult(
        added_examinations=[initial.reconstruction.examinations[0]],
        reported_content_opinion_ids=[opinion.opinion_id],
        updated_core_answer=initial.core_answer,
    )

    with pytest.raises(ValueError, match="formal thoracic radiology opinion"):
        validate_update_and_consult(update, discussion_input)


def test_discussion_updates_only_affected_interpretation_task():
    case = case_0714()
    initial = validate_initial_assessment(assessment_0714(), case)
    rheum_unit = next(
        unit
        for segment in case.segments
        for unit in segment.units
        if MdtSpecialty.RHEUMATOLOGY in unit.graph_unit.mdt_specialty
    )
    prop_id = rheum_unit.clinical_propositions.propositions[0].proposition_id
    evidence = pointer(rheum_unit.graph_unit.graph_unit_id, prop_id)
    opinion = SpecialistOpinion(
        specialty=MdtSpecialty.RHEUMATOLOGY,
        opinion_id="rheum-001",
        summary="正式风湿意见",
        claims=[SpecialistClaim(claim="目前无明确CTD证据", evidence=[evidence])],
        confidence="moderate",
    )
    discussion_input = ThoracicRadiologyDiscussionInput(
        case_input=case,
        initial_assessment=initial,
        specialist_opinions=[opinion],
        chair_questions=["风湿意见是否改变影像疾病关联？"],
    )
    evidence_map = DiscussionEvidenceMap(
        specialist_opinions_used=[opinion.opinion_id],
        mapped_findings=[
            MappedSpecialistFinding(
                opinion_id=opinion.opinion_id,
                relationship="supplementary",
                target_layer="interpretation",
                affected_tasks=[RadiologyTask.ILD_MORPHOLOGIC_PATTERN],
                imaging_effect="只影响疾病关联解释，不改变原报告内容。",
                evidence=[evidence],
            )
        ],
    )
    updated_task = RadiologyTaskAssessment(
        task=RadiologyTask.ILD_MORPHOLOGIC_PATTERN,
        priority="conditional",
        answerability="not_answerable",
        conclusion="风湿意见未改变文字不足以分型的结论。",
        confidence="high",
        reasoning_summary="其他专科意见不能补写影像征象。",
        supporting_evidence=[evidence],
        specialist_opinion_ids=[opinion.opinion_id],
        decision_impact="保留形态模式不可评价，疾病关联由MDT综合。",
    )
    update = DiscussionUpdateAndConsult(
        task_updates=[
            TaskUpdate(
                task=RadiologyTask.ILD_MORPHOLOGIC_PATTERN,
                change="updated",
                previous_summary="文字不足以分型。",
                updated_assessment=updated_task,
                reason="整合正式风湿意见，但不改变影像事实。",
                supporting_evidence=[evidence],
                specialist_opinion_ids=[opinion.opinion_id],
            )
        ],
        updated_core_answer=initial.core_answer,
        chair_answers=[
            ChairAnswer(
                question_id="chair_q_001",
                answer="不改变影像所见或形态不可评价结论，只影响疾病关联解释。",
                confidence="moderate",
                supporting_evidence=[evidence],
                specialist_opinion_ids=[opinion.opinion_id],
            )
        ],
    )
    llm = FakeLLM([llm_payload(evidence_map), llm_payload(update)])
    agent = ThoracicRadiologyAgent.from_config(CONFIG, llm)

    result, trace = agent.discussion_response(discussion_input)

    assert result.updated_assessment.reconstruction == initial.reconstruction
    assert len(result.task_changes) == 1
    assert result.chair_answers[0].question_id == "chair_q_001"
    assert [item["stage"] for item in trace["stages"]] == [
        "discussion_evidence_mapping",
        "discussion_update_and_response",
    ]


def test_initial_report_leads_with_core_answer_and_keeps_guide_as_audit(tmp_path):
    case = case_0714()
    result = validate_initial_assessment(assessment_0714(), case)
    output = tmp_path / "thoracic_radiology.html"

    render_thoracic_radiology_report(result, case, output)

    html = output.read_text(encoding="utf-8")
    assert "胸部影像科 首轮评估" in html
    assert "仅分析病例中的影像文字描述" in html
    assert "当前影像问题" in html
    assert "核心回答" in html
    assert "影像审阅覆盖（内部审计）" in html
    assert "七问处理状态" not in html
    assert "双肺间质增粗纹理走形杂乱" in html
    assert html.index("核心回答") < html.index("影像审阅覆盖")
