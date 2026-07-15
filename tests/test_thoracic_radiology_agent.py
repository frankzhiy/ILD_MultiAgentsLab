import json

import pytest
from pydantic import ValidationError

from scripts.agent_input.prepare_specialty_input import build_specialty_case_input
from src.agents.thoracic_radiology.agent import (
    ThoracicRadiologyAgent,
    validate_discussion_response,
    validate_initial_assessment,
)
from src.agents.thoracic_radiology.models import (
    ChairAnswer,
    ConditionalImagingClassification,
    DescriptionDerivedObservationState,
    DiscussionConsultOutput,
    DiscussionEvidenceMap,
    DiscussionStateUpdate,
    DomainReview,
    EvidencePointer,
    ImagingExamination,
    ImagingInterpretationState,
    ImagingObservation,
    ImagingSourceState,
    InitialImagingFormulation,
    InitialMorphologicAssessment,
    InitialSourceReconstruction,
    LongitudinalImagingAssessment,
    MappedSpecialistFinding,
    MorphologicPatternAssessment,
    RadiologyDomainChange,
    SpecialistClaim,
    SpecialistOpinion,
    ThoracicRadiologyClinicalState,
    ThoracicRadiologyDiscussionInput,
    ThoracicRadiologyDiscussionResponse,
    ThoracicRadiologyDomain,
    ThoracicRadiologyInitialAssessment,
    DiseaseAssociation,
)
from src.llm.base import LLMResponse
from src.reporting.thoracic_radiology_report import render_thoracic_radiology_report
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import EvidenceRole
from src.utils.config import load_yaml


RUN_DIR = "outputs/runs/20260714_163246_76-IPF_step2_step3"
CONFIG = "configs/agents/thoracic_radiology/agent.yaml"
MORPHOLOGY_SOURCE = "ERS/ATS Statement 2025 (Eur Respir J 2025;66:2500158)"
IPF_SOURCE = "ATS/ERS/JRS/ALAT Clinical Practice Guideline 2022"


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


def case_input():
    return build_specialty_case_input(RUN_DIR, MdtSpecialty.THORACIC_RADIOLOGY)


def unit_with_role(case, role):
    return next(
        unit for segment in case.segments for unit in segment.units if unit.evidence_role == role
    )


def unit_for_specialty(case, specialty):
    return next(
        unit
        for segment in case.segments
        for unit in segment.units
        if specialty in unit.graph_unit.mdt_specialty
    )


def pointer_for(unit, block_index=0):
    return EvidencePointer(
        evidence_ids=[unit.clinical_propositions.evidence_blocks[block_index].evidence_id]
    )


def llm_payload(model):
    program_fields = {"case_id", "segment_id", "graph_unit_id", "node_ids", "quote"}

    def strip(value):
        if isinstance(value, dict):
            return {key: strip(item) for key, item in value.items() if key not in program_fields}
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value

    return strip(model.model_dump(mode="json"))


def review(domain, status="assessed"):
    return DomainReview(domain=domain, status=status, rationale="已按影像科规则审阅。")


def source_state(pointer):
    return ImagingSourceState(
        overall_evaluability="partially_sufficient",
        examinations=[
            ImagingExamination(
                exam_id="exam_001",
                temporal_anchor="当前检查",
                modality="ct",
                source_authority="formal_imaging_report",
                description_sufficiency="partial",
                technical_quality_status="not_assessable_from_text",
                comparison_status="unknown",
                assessment="现有正式 CT 文字报告可支持有限所见提取。",
                supporting_evidence=[pointer],
            )
        ],
        reasoning_summary="未读取原始图像，扫描技术质量不能由文字替代评价。",
    )


def observation_state(pointer):
    return DescriptionDerivedObservationState(
        observations=[
            ImagingObservation(
                finding="双肺间质性增粗",
                category="parenchymal",
                status="reported_present",
                craniocaudal_distribution="描述未提供",
                axial_distribution="描述未提供",
                anatomic_distribution="双肺",
                confidence="moderate",
                supporting_evidence=[pointer],
            )
        ],
        reasoning_summary="仅保留报告文字明确陈述的影像事实。",
    )


def longitudinal(pointer=None):
    return LongitudinalImagingAssessment(
        rule_source=IPF_SOURCE,
        status="requires_comparator",
        comparison_window="未提供可比时间窗",
        progression_features=[],
        acute_overlay_status="not_assessable",
        confidence="unknown",
        reasoning_summary="缺少明确比较片，不能判断稳定或进展。",
        related_evidence=[pointer] if pointer else [],
    )


def pattern(pointer):
    return MorphologicPatternAssessment(
        framework=MORPHOLOGY_SOURCE,
        classification_status="unclassifiable_pattern",
        primary_pattern="unclassifiable_pattern",
        confidence="low",
        reasoning_summary="描述缺少关键分布和征象，不能可靠归入特定形态模式。",
        supporting_evidence=[pointer],
    )


def initial_stages(case):
    pointer = pointer_for(unit_with_role(case, EvidenceRole.OWNED))
    source = InitialSourceReconstruction(
        domain_reviews=[review(ThoracicRadiologyDomain.SOURCE_AND_EVALUABILITY)],
        source_state=source_state(pointer),
    )
    morphology = InitialMorphologicAssessment(
        domain_reviews=[
            review(ThoracicRadiologyDomain.IMAGING_PHENOTYPE),
            review(ThoracicRadiologyDomain.NATURE_AND_BURDEN, "partially_assessable"),
            review(ThoracicRadiologyDomain.LONGITUDINAL_CHANGE, "requires_comparator"),
        ],
        observation_state=observation_state(pointer),
        longitudinal_assessment=longitudinal(pointer),
    )
    formulation = InitialImagingFormulation(
        domain_reviews=[
            review(ThoracicRadiologyDomain.MORPHOLOGIC_PATTERN, "partially_assessable"),
            review(ThoracicRadiologyDomain.DISEASE_ASSOCIATION, "not_assessable"),
            review(ThoracicRadiologyDomain.MDT_DECISION_GAPS, "partially_assessable"),
        ],
        morphologic_pattern=pattern(pointer),
        conditional_classifications=[
            ConditionalImagingClassification(
                protocol="ipf_hrct_2022",
                rule_source=IPF_SOURCE,
                applicability="not_applicable",
                applicability_basis="现有资料未明确给出临床疑似 IPF 前提。",
                category=None,
                confidence="unknown",
                reasoning_summary="不机械套用 IPF 专用四分类。",
            )
        ],
    )
    return source, morphology, formulation


def assessment_for(case):
    source, morphology, formulation = initial_stages(case)
    return ThoracicRadiologyInitialAssessment(
        case_id=case.case_id,
        domain_reviews=[review(domain) for domain in ThoracicRadiologyDomain],
        source_state=source.source_state,
        observation_state=morphology.observation_state,
        interpretation_state=ImagingInterpretationState(
            morphologic_pattern=formulation.morphologic_pattern,
            conditional_classifications=formulation.conditional_classifications,
            longitudinal_assessment=morphology.longitudinal_assessment,
        ),
    )


def unchanged_changes():
    return [
        RadiologyDomainChange(
            domain=domain,
            observation_delta="unchanged",
            interpretation_delta="unchanged",
            assessability_delta="unchanged",
            initial_view="首轮状态",
            updated_view="复核后不变",
            reason="没有足以改变该域的新信息。",
        )
        for domain in ThoracicRadiologyDomain
    ]


def discussion_state(initial):
    return ThoracicRadiologyClinicalState(
        case_id=initial.case_id,
        phase="discussion_update",
        domain_reviews=initial.domain_reviews,
        source_state=initial.source_state,
        observation_state=initial.observation_state,
        interpretation_state=initial.interpretation_state,
        specialist_dependencies=initial.specialist_dependencies,
        direct_review_requests=initial.direct_review_requests,
        missing_data=initial.missing_data,
        limitations=initial.limitations,
    )


def test_initial_assessment_runs_three_ordered_stages():
    case = case_input()
    stages = initial_stages(case)
    llm = FakeLLM([llm_payload(item) for item in stages])
    agent = ThoracicRadiologyAgent.from_config(CONFIG, llm)

    result, trace = agent.initial_assessment(case)

    assert result.schema_version == "thoracic_radiology.v1"
    assert result.source_state.direct_images_reviewed is False
    assert [item.domain for item in result.domain_reviews] == list(ThoracicRadiologyDomain)
    assert [item["stage"] for item in trace["stages"]] == [
        "initial_source_reconstruction",
        "initial_morphologic_assessment",
        "initial_imaging_formulation",
    ]
    assert "第 1 阶段来源与可评价性" in llm.prompts[1][1].content
    assert "描述派生形态评估" in llm.prompts[2][1].content


def test_direct_image_review_can_never_be_claimed():
    with pytest.raises(ValidationError, match="direct_images_reviewed"):
        ImagingSourceState(
            direct_images_reviewed=True,
            overall_evaluability="partially_sufficient",
            reasoning_summary="错误地声称读取原始图像。",
        )


def test_observation_rejects_non_radiology_unit_even_when_shared_context():
    case = case_input()
    initial = assessment_for(case)
    shared = unit_with_role(case, EvidenceRole.SHARED_CONTEXT)
    initial.observation_state.observations[0].supporting_evidence = [pointer_for(shared)]

    with pytest.raises(ValueError, match="not radiology-scoped"):
        validate_initial_assessment(initial, case, load_yaml(CONFIG)["clinical_rules"])


def test_wrong_pattern_framework_is_rejected():
    case = case_input()
    initial = assessment_for(case)
    initial.interpretation_state.morphologic_pattern.framework = "mixed guideline"

    with pytest.raises(ValueError, match="Pattern framework"):
        validate_initial_assessment(initial, case, load_yaml(CONFIG)["clinical_rules"])


def test_agent_rejects_wrong_specialty_and_empty_input():
    case = case_input()
    agent = ThoracicRadiologyAgent.from_config(CONFIG, FakeLLM([]))
    case.target_specialty = MdtSpecialty.PULMONOLOGY
    with pytest.raises(ValueError, match="target_specialty=thoracic_radiology"):
        agent.initial_assessment(case)

    case.target_specialty = MdtSpecialty.THORACIC_RADIOLOGY
    case.summary.unit_count = 0
    with pytest.raises(ValueError, match="at least one graph unit"):
        agent.initial_assessment(case)


def test_non_radiology_opinion_cannot_change_observation_layer():
    case = case_input()
    initial = assessment_for(case)
    rheumatology_unit = unit_for_specialty(case, MdtSpecialty.RHEUMATOLOGY)
    evidence = pointer_for(rheumatology_unit)
    opinion = SpecialistOpinion(
        specialty=MdtSpecialty.RHEUMATOLOGY,
        opinion_id="rheum-001",
        summary="正式风湿免疫意见",
        claims=[SpecialistClaim(claim="目前无明确 CTD 证据", evidence=[evidence])],
        confidence="moderate",
    )
    updated = discussion_state(initial)
    updated.observation_state.observations[0].finding = "被风湿意见改写的影像观察"
    changes = unchanged_changes()
    changes[1].observation_delta = "updated"
    changes[1].supporting_evidence = [evidence]
    changes[1].specialist_opinion_ids = [opinion.opinion_id]
    discussion_input = ThoracicRadiologyDiscussionInput(
        case_input=case,
        initial_assessment=initial,
        specialist_opinions=[opinion],
    )
    response = ThoracicRadiologyDiscussionResponse(
        updated_state=updated,
        domain_changes=changes,
    )

    with pytest.raises(ValueError, match="formal thoracic radiology claim"):
        validate_discussion_response(
            response,
            discussion_input,
            load_yaml(CONFIG)["clinical_rules"],
        )


def test_formal_radiology_claim_can_update_observation_layer():
    case = case_input()
    initial = assessment_for(case)
    evidence = pointer_for(unit_with_role(case, EvidenceRole.OWNED))
    opinion = SpecialistOpinion(
        specialty=MdtSpecialty.THORACIC_RADIOLOGY,
        opinion_id="radiology-001",
        summary="正式影像科复核意见",
        claims=[SpecialistClaim(claim="确认双肺间质性增粗并补充范围描述", evidence=[evidence])],
        confidence="moderate",
    )
    updated = discussion_state(initial)
    updated.observation_state = updated.observation_state.model_copy(deep=True)
    updated.observation_state.observations[0].finding = "双肺间质性增粗，以右中下肺为著"
    changes = unchanged_changes()
    phenotype_change = next(
        item for item in changes if item.domain == ThoracicRadiologyDomain.IMAGING_PHENOTYPE
    )
    phenotype_change.observation_delta = "updated"
    phenotype_change.updated_view = "正式影像复核补充了分布范围。"
    phenotype_change.reason = "整合正式影像科 claim。"
    phenotype_change.supporting_evidence = [evidence]
    phenotype_change.specialist_opinion_ids = [opinion.opinion_id]
    discussion_input = ThoracicRadiologyDiscussionInput(
        case_input=case,
        initial_assessment=initial,
        specialist_opinions=[opinion],
    )
    response = ThoracicRadiologyDiscussionResponse(
        updated_state=updated,
        domain_changes=changes,
        specialist_opinions_used=[opinion.opinion_id],
    )

    validated = validate_discussion_response(
        response,
        discussion_input,
        load_yaml(CONFIG)["clinical_rules"],
    )

    assert "右中下肺" in validated.updated_state.observation_state.observations[0].finding


def test_discussion_allows_rheumatology_claim_to_update_disease_association():
    case = case_input()
    initial = assessment_for(case)
    rheumatology_unit = unit_for_specialty(case, MdtSpecialty.RHEUMATOLOGY)
    evidence = pointer_for(rheumatology_unit)
    opinion = SpecialistOpinion(
        specialty=MdtSpecialty.RHEUMATOLOGY,
        opinion_id="rheum-001",
        summary="正式风湿免疫意见",
        claims=[SpecialistClaim(claim="目前无明确 CTD 证据", evidence=[evidence])],
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
                affected_domains=[ThoracicRadiologyDomain.DISEASE_ASSOCIATION],
                imaging_effect="正式风湿意见可更新疾病关联，但不改变影像观察。",
                evidence=[evidence],
            )
        ],
    )
    updated = discussion_state(initial)
    updated.interpretation_state = updated.interpretation_state.model_copy(deep=True)
    updated.interpretation_state.disease_associations = [
        DiseaseAssociation(
            rank=1,
            disease_or_context="CTD-ILD",
            relationship="less_likely",
            confidence="moderate",
            reasoning_summary="正式风湿意见降低当前 CTD 关联可能性。",
            supporting_evidence=[evidence],
            specialist_opinion_ids=[opinion.opinion_id],
        )
    ]
    changes = unchanged_changes()
    disease_change = next(
        item for item in changes if item.domain == ThoracicRadiologyDomain.DISEASE_ASSOCIATION
    )
    disease_change.interpretation_delta = "updated"
    disease_change.updated_view = "CTD 关联可能性降低"
    disease_change.reason = "整合正式风湿免疫意见。"
    disease_change.supporting_evidence = [evidence]
    disease_change.specialist_opinion_ids = [opinion.opinion_id]
    update = DiscussionStateUpdate(updated_state=updated, domain_changes=changes)
    consult = DiscussionConsultOutput(
        chair_answers=[
            ChairAnswer(
                question_id="chair_q_001",
                answer="风湿意见降低 CTD 关联，但未改变既有影像观察。",
                confidence="moderate",
                supporting_evidence=[evidence],
                specialist_opinion_ids=[opinion.opinion_id],
            )
        ]
    )
    llm = FakeLLM([llm_payload(evidence_map), llm_payload(update), llm_payload(consult)])
    agent = ThoracicRadiologyAgent.from_config(CONFIG, llm)

    result, trace = agent.discussion_response(discussion_input)

    assert result.updated_state.observation_state == initial.observation_state
    assert result.updated_state.interpretation_state.disease_associations[0].relationship == (
        "less_likely"
    )
    assert result.chair_answers[0].question_id == "chair_q_001"
    assert [item["stage"] for item in trace["stages"]] == [
        "discussion_evidence_mapping",
        "discussion_imaging_update",
        "discussion_consult_response",
    ]


def test_initial_report_exposes_text_only_boundary_and_seven_domains(tmp_path):
    case = case_input()
    result = validate_initial_assessment(
        assessment_for(case), case, load_yaml(CONFIG)["clinical_rules"]
    )
    output = tmp_path / "thoracic_radiology.html"

    render_thoracic_radiology_report(result, case, output)

    html = output.read_text(encoding="utf-8")
    pointer = result.observation_state.observations[0].supporting_evidence[0]
    assert "胸部影像科 首轮评估" in html
    assert "仅分析病例中的影像文字描述" in html
    assert "七问处理状态" in html
    assert "双肺间质性增粗" in html
    assert pointer.evidence_ids[0] in html and pointer.quote in html
