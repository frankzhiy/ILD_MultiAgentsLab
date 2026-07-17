import json
from pathlib import Path

import pytest

from scripts.agent_input.prepare_specialty_input import build_specialty_case_input
from src.agents.pulmonology.agent import (
    PulmonologyAgent,
    validate_discussion_response,
    validate_initial_assessment,
)
from src.agents.pulmonology.models import (
    ChairAnswer,
    ClinicalAssessmentItem,
    DataGap,
    DiagnosticFormulation,
    DifferentialDiagnosis,
    DiscussionConsultOutput,
    DiscussionEvidenceMap,
    DiscussionStateUpdate,
    DomainChange,
    EvidencePointer,
    InitialDiagnosticFormulation,
    InitialFoundation,
    InitialPulmonaryAssessment,
    PulmonologyDiscussionState,
    MappedSpecialistFinding,
    ProgressionAssessment,
    ProgressionComponent,
    PulmonologyDiscussionInput,
    PulmonologyDiscussionResponse,
    PulmonologyDomain,
    PulmonologyInitialAssessment,
    ReferenceObservation,
    SpecialistClaim,
    SpecialistOpinion,
    SpecialistQuestion,
)
from src.llm.apiyi_client import APIYIClient
from src.llm.base import LLMResponse
from src.llm.factory import build_llm_client
from src.reporting.pulmonology_report import render_pulmonology_report
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import EvidenceRole
from src.utils.config import load_yaml


RUN_DIR = "outputs/runs/20260714_163246_76-IPF_step2_step3"
CONFIG = "configs/agents/pulmonology/agent.yaml"


class FakeLLM:
    supports_json_schema = False

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []
        self.response_formats = []

    def complete(self, messages, *, temperature, max_tokens, response_format=None):
        self.prompts.append(messages)
        self.response_formats.append(response_format)
        content = json.dumps(self.responses.pop(0), ensure_ascii=False)
        return LLMResponse(
            content=content,
            raw={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
        )


def case_input():
    return build_specialty_case_input(RUN_DIR, MdtSpecialty.PULMONOLOGY)


def unit_with_role(case, role):
    return next(
        unit for segment in case.segments for unit in segment.units if unit.evidence_role == role
    )


def pointer_for(unit, block_index=0) -> EvidencePointer:
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
    return {"domain": domain, "status": status, "rationale": "已按本域规则审阅。"}


def initial_reviews():
    statuses = {
        PulmonologyDomain.SECONDARY_CAUSES: "partially_assessable",
        PulmonologyDomain.RESPIRATORY_TESTS: "not_assessable",
        PulmonologyDomain.SPECIALIST_INTEGRATION: "deferred_to_specialist",
        PulmonologyDomain.PROGRESSION: "not_assessable",
        PulmonologyDomain.DECISION_RELEVANT_GAPS: "partially_assessable",
    }
    return [review(domain, statuses.get(domain, "assessed")) for domain in PulmonologyDomain]


def discussion_reviews():
    return [review(domain, "reviewed_unchanged") for domain in PulmonologyDomain]


def clinical_item(pointer, text="慢性呼吸系统症状，近期加重"):
    return ClinicalAssessmentItem(
        assessment=text,
        confidence="moderate",
        reasoning_summary="原文记录支持当前有限判断。",
        supporting_evidence=[pointer],
    )


def formulation_for(pointer, *, opinion_id=None):
    opinion_ids = [opinion_id] if opinion_id else []
    return DiagnosticFormulation(
        classification_status="provisional_diagnosis",
        leading_diagnosis="待分类间质性肺病",
        confidence="low",
        reasoning_summary="当前仅形成呼吸科工作假设，不是最终 MDT 诊断。",
        differential_diagnoses=[
            DifferentialDiagnosis(
                rank=1,
                diagnosis="待分类间质性肺病",
                confidence="low",
                reasoning_summary="病例资料尚不足以唯一分类。",
                supporting_evidence=[pointer],
                specialist_opinion_ids=opinion_ids,
            )
        ],
        supporting_evidence=[pointer],
        specialist_opinion_ids=opinion_ids,
    )


def assessment_for(case, pointer=None) -> PulmonologyInitialAssessment:
    pointer = pointer or pointer_for(unit_with_role(case, EvidenceRole.OWNED))
    item = clinical_item(pointer)
    return PulmonologyInitialAssessment(
        case_id=case.case_id,
        domain_reviews=initial_reviews(),
        clinical_phenotype=item,
        pulmonary_severity=item,
        diagnostic_formulation=formulation_for(pointer),
    )


def initial_stages(case):
    pointer = pointer_for(unit_with_role(case, EvidenceRole.OWNED))
    item = clinical_item(pointer)
    foundation = InitialFoundation(
        domain_reviews=[
            review(PulmonologyDomain.CLINICAL_PHENOTYPE),
            review(PulmonologyDomain.SECONDARY_CAUSES, "partially_assessable"),
        ],
        clinical_phenotype=item,
    )
    pulmonary = InitialPulmonaryAssessment(
        domain_reviews=[
            review(PulmonologyDomain.PULMONARY_SEVERITY),
            review(PulmonologyDomain.RESPIRATORY_TESTS, "not_assessable"),
            review(PulmonologyDomain.PROGRESSION, "not_assessable"),
        ],
        pulmonary_severity=item,
    )
    formulation = InitialDiagnosticFormulation(
        domain_reviews=[
            review(PulmonologyDomain.SPECIALIST_INTEGRATION, "deferred_to_specialist"),
            review(PulmonologyDomain.DIAGNOSTIC_FORMULATION),
            review(PulmonologyDomain.DECISION_RELEVANT_GAPS, "partially_assessable"),
        ],
        diagnostic_formulation=formulation_for(pointer),
    )
    return foundation, pulmonary, formulation


def discussion_state(initial, *, diagnostic_formulation=None):
    return PulmonologyDiscussionState(
        case_id=initial.case_id,
        phase="discussion_update",
        domain_reviews=discussion_reviews(),
        clinical_phenotype=initial.clinical_phenotype,
        secondary_cause_assessment=initial.secondary_cause_assessment,
        pulmonary_severity=initial.pulmonary_severity,
        respiratory_test_interpretation=initial.respiratory_test_interpretation,
        bronchoscopy_assessment=initial.bronchoscopy_assessment,
        specialist_dependencies=initial.specialist_dependencies,
        reference_observations=initial.reference_observations,
        progression_assessment=initial.progression_assessment,
        diagnostic_formulation=diagnostic_formulation or initial.diagnostic_formulation,
        missing_data=initial.missing_data,
        limitations=initial.limitations,
    )


def domain_changes():
    return [
        DomainChange(
            domain=domain,
            change_status="reviewed_unchanged",
            initial_view="首轮状态",
            updated_view="复核后不变",
            reason="本轮无足以改变该域判断的新信息。",
        )
        for domain in PulmonologyDomain
    ]


def test_pulmonology_yaml_builds_apiyi_client(monkeypatch):
    monkeypatch.setenv("APIYI_API_KEY", "secret")
    client = build_llm_client(load_yaml(CONFIG))

    assert isinstance(client, APIYIClient)
    assert client.model == "gpt-5.6-luna"
    assert client.base_url == "https://api.apiyi.com/v1"
    assert client.supports_json_schema is True


def test_strict_schema_mode_keeps_schema_out_of_prompt_and_appends_contract():
    stage = InitialFoundation(
        domain_reviews=[
            review(PulmonologyDomain.CLINICAL_PHENOTYPE),
            review(PulmonologyDomain.SECONDARY_CAUSES),
        ]
    )
    llm = FakeLLM([llm_payload(stage)])
    llm.supports_json_schema = True
    agent = PulmonologyAgent.from_config(CONFIG, llm, enable_guidelines=False)

    agent._generate(
        stage="initial_foundation",
        schema_model=InitialFoundation,
        variables={"case_input": "{}", "clinical_rules": "{}"},
        validation=lambda result: result,
    )

    prompt = llm.prompts[0][1].content
    assert llm.response_formats[0]["type"] == "json_schema"
    assert "由 API 的严格 JSON Schema response_format 提供" in prompt
    assert '"$defs"' not in prompt
    assert prompt.rstrip().endswith(
        "本轮没有正式专科意见，所有 specialist_opinion_ids 必须为空列表。"
    )


def test_initial_assessment_runs_three_ordered_stages():
    case = case_input()
    stages = initial_stages(case)
    llm = FakeLLM([llm_payload(item) for item in stages])
    events = []
    agent = PulmonologyAgent.from_config(
        CONFIG,
        llm,
        event_callback=lambda event, payload: events.append((event, payload)),
        enable_guidelines=False,
    )

    result, trace = agent.initial_assessment(case)

    assert result.schema_version == "pulmonology.v2"
    assert [item.domain for item in result.domain_reviews] == list(PulmonologyDomain)
    assert [item["stage"] for item in trace["stages"]] == [
        "initial_foundation",
        "initial_pulmonary_assessment",
        "initial_diagnostic_formulation",
    ]
    assert len(llm.prompts) == 3
    assert "所有面向人的文本字段必须使用简体中文" in llm.prompts[0][0].content
    assert "简明、可审计的临床理由" in llm.prompts[0][1].content
    assert "第 1 阶段临床基础" in llm.prompts[1][1].content
    assert "第 2 阶段肺部评估" in llm.prompts[2][1].content
    assert [event for event, _ in events].count("stage_completed") == 3
    assert [event for event, _ in events].count("llm_attempt_completed") == 3
    assert [event for event, _ in events].count("validation_completed") == 3
    assert trace["stages"][0]["timing"]["llm_duration_seconds"] >= 0
    assert trace["stages"][0]["timing"]["validation_duration_seconds"] >= 0


def test_eight_domains_are_required_once_but_results_may_be_empty():
    reviews = initial_reviews()
    state = PulmonologyInitialAssessment(domain_reviews=reviews)

    assert state.clinical_phenotype is None
    assert state.domain_reviews[0].status == "assessed"
    with pytest.raises(ValueError, match="eight domains exactly once"):
        PulmonologyInitialAssessment(domain_reviews=reviews[:-1] + [reviews[0]])


def test_stage_schema_exposes_only_evidence_ids_for_pointer():
    schema = InitialFoundation.model_json_schema()

    assert set(schema["$defs"]["EvidencePointer"]["properties"]) == {"evidence_ids"}
    evidence_ids = schema["$defs"]["EvidencePointer"]["properties"]["evidence_ids"]
    assert evidence_ids["maxItems"] == 1
    assert "只填写一个" in evidence_ids["description"]
    review_schema = schema["$defs"]["InitialFoundationReview"]
    assert {"domain", "status", "rationale"}.issubset(review_schema["required"])
    assert set(review_schema["properties"]["status"]["enum"]) == {
        "assessed", "partially_assessable", "not_assessable", "deferred_to_specialist", "not_applicable"
    }
    assert set(review_schema["properties"]["domain"]["enum"]) == {
        "clinical_phenotype", "secondary_causes"
    }


def test_initial_stage_schema_excludes_other_stage_domains_and_discussion_statuses():
    schema = InitialDiagnosticFormulation.model_json_schema()
    review_schema = schema["$defs"]["InitialDiagnosticReview"]

    assert set(review_schema["properties"]["domain"]["enum"]) == {
        "specialist_integration", "diagnostic_formulation", "decision_relevant_gaps"
    }
    assert "updated" not in review_schema["properties"]["status"]["enum"]
    with pytest.raises(ValueError):
        InitialDiagnosticFormulation(
            domain_reviews=[
                review(PulmonologyDomain.SPECIALIST_INTEGRATION, "deferred_to_specialist"),
                review(PulmonologyDomain.DIAGNOSTIC_FORMULATION, "updated"),
                review(PulmonologyDomain.SECONDARY_CAUSES, "assessed"),
            ]
        )


def test_program_resolves_and_overwrites_evidence_locator():
    case = case_input()
    owned = unit_with_role(case, EvidenceRole.OWNED)
    pointer = pointer_for(owned)
    pointer.segment_id = "wrong-segment"
    pointer.graph_unit_id = "wrong-unit"
    pointer.node_ids = ["wrong-node"]
    pointer.quote = "不是连续原文"

    result = validate_initial_assessment(assessment_for(case, pointer), case)
    resolved = result.clinical_phenotype.supporting_evidence[0]

    assert resolved.segment_id == owned.graph_unit.segment_id
    assert resolved.graph_unit_id == owned.graph_unit.graph_unit_id
    assert resolved.quote == owned.clinical_propositions.evidence_blocks[0].text
    assert resolved.node_ids and "wrong-node" not in resolved.node_ids


def test_initial_assessment_rejects_missing_unknown_and_reference_only_evidence():
    case = case_input()
    missing = assessment_for(case)
    missing.clinical_phenotype.supporting_evidence[0].evidence_ids = []
    with pytest.raises(ValueError, match="at least one evidence_id"):
        validate_initial_assessment(missing, case)

    unknown = assessment_for(case)
    unknown.clinical_phenotype.supporting_evidence[0].evidence_ids = ["missing"]
    with pytest.raises(ValueError, match="unknown evidence_ids"):
        validate_initial_assessment(unknown, case)

    reference = assessment_for(case, pointer_for(unit_with_role(case, EvidenceRole.REFERENCE_ONLY)))
    with pytest.raises(ValueError, match="reference_only"):
        validate_initial_assessment(reference, case)


def test_mixed_specialty_context_has_owned_evidence_authorization():
    case = case_input()
    multi_specialty = next(
        unit
        for segment in case.segments
        for unit in segment.units
        if unit.evidence_role == EvidenceRole.OWNED
        and len(unit.graph_unit.mdt_specialty) > 1
    )
    assessment = assessment_for(case, pointer_for(multi_specialty))

    validated = validate_initial_assessment(assessment, case)

    assert validated.clinical_phenotype.supporting_evidence[0].graph_unit_id == (
        multi_specialty.graph_unit.graph_unit_id
    )


def test_program_splits_one_evidence_pointer_across_graph_units():
    case = case_input()
    owned = [
        unit
        for segment in case.segments
        for unit in segment.units
        if unit.evidence_role == EvidenceRole.OWNED
    ]
    invalid = assessment_for(case)
    invalid.clinical_phenotype.supporting_evidence[0].evidence_ids = [
        owned[0].clinical_propositions.evidence_blocks[0].evidence_id,
        owned[1].clinical_propositions.evidence_blocks[0].evidence_id,
    ]

    validated = validate_initial_assessment(invalid, case)

    pointers = validated.clinical_phenotype.supporting_evidence
    assert len(pointers) == 2
    assert {pointer.graph_unit_id for pointer in pointers} == {
        owned[0].graph_unit.graph_unit_id,
        owned[1].graph_unit.graph_unit_id,
    }


def test_stage2_can_route_non_authoritative_imaging_to_related_evidence():
    case = case_input()
    foundation, pulmonary, formulation = initial_stages(case)
    reference_pointer = pointer_for(unit_with_role(case, EvidenceRole.REFERENCE_ONLY))
    multi_specialty_pointer = pointer_for(next(
        unit
        for segment in case.segments
        for unit in segment.units
        if unit.evidence_role == EvidenceRole.OWNED
        and len(unit.graph_unit.mdt_specialty) > 1
    ))
    pulmonary.progression_assessment = ProgressionAssessment(
        recent_worsening="not_assessable",
        acute_exacerbation_status="not_assessable",
        ppf_status="not_assessable",
        assessment_window="past year",
        components=[
            ProgressionComponent(
                component="radiology",
                status="not_assessable",
                assessment="需要影像科完成纵向比较。",
                related_evidence=[reference_pointer, multi_specialty_pointer],
            )
        ],
        reasoning_summary="现有影像上下文不能替代正式纵向影像判断。",
    )
    pulmonary.specialist_dependencies = [
        SpecialistQuestion(
            specialty=MdtSpecialty.THORACIC_RADIOLOGY,
            question="影像是否存在明确纵向进展？",
            why_it_matters="决定影像进展及 PPF 是否可评价。",
            related_evidence=[reference_pointer],
        )
    ]
    pulmonary.reference_observations = [
        ReferenceObservation(
            observation="原报告提及双肺间质性增粗。",
            why_confirmation_is_needed="该观察不能替代影像科模式与进展判断。",
            related_evidence=[reference_pointer],
        )
    ]
    llm = FakeLLM([llm_payload(foundation), llm_payload(pulmonary), llm_payload(formulation)])
    agent = PulmonologyAgent.from_config(CONFIG, llm, enable_guidelines=False)

    result, _ = agent.initial_assessment(case)

    component = result.progression_assessment.components[0]
    assert component.status == "not_assessable"
    assert {item.graph_unit_id for item in component.related_evidence} == {
        "seg_003_gu_003",
        "seg_004_gu_001",
    }
    assert result.specialist_dependencies[0].specialty == MdtSpecialty.THORACIC_RADIOLOGY
    assert result.reference_observations[0].related_evidence[0].quote
    assert "rule_source" not in result.progression_assessment.model_dump(mode="json")


def test_initial_report_shows_clinical_results_and_coverage_audit(tmp_path):
    case = case_input()
    assessment = assessment_for(case)
    assessment.missing_data = [
        DataGap(
            gap_type="no_longitudinal_comparator",
            available_information="已有本次肺功能检查。",
            missing_information="缺少带日期且指标可比的历史肺功能结果。",
            why_it_matters="无法判断肺功能下降速度。",
            decision_unlocked="能否评价生理进展与 PPF。",
            related_evidence=[assessment.clinical_phenotype.supporting_evidence[0]],
        )
    ]
    result = validate_initial_assessment(assessment, case)
    output = tmp_path / "pulmonology.html"

    render_pulmonology_report(result, case, output)

    html = output.read_text(encoding="utf-8")
    pointer = result.clinical_phenotype.supporting_evidence[0]
    assert "呼吸科 首轮评估" in html
    assert result.clinical_phenotype.assessment in html
    assert pointer.evidence_ids[0] in html and pointer.quote in html
    assert "八问处理状态" in html
    assert "可解锁的决策" in html
    assert "ILD 多学科团队 · 呼吸科" in html
    assert "片段 ·" in html and "segment ·" not in html


def test_legacy_saved_assessment_migrates_to_v2_state():
    path = Path(RUN_DIR) / "76-IPF_pulmonology_initial.json"
    result = PulmonologyInitialAssessment.model_validate_json(path.read_text(encoding="utf-8"))

    assert result.schema_version == "pulmonology.v2"
    assert len(result.domain_reviews) == 8
    assert result.missing_data[0].decision_unlocked


def test_agent_rejects_wrong_specialty_and_empty_input():
    case = case_input()
    agent = PulmonologyAgent.from_config(CONFIG, FakeLLM([]), enable_guidelines=False)
    case.target_specialty = MdtSpecialty.THORACIC_RADIOLOGY
    with pytest.raises(ValueError, match="target_specialty=pulmonology"):
        agent.initial_assessment(case)

    case.target_specialty = MdtSpecialty.PULMONOLOGY
    case.summary.unit_count = 0
    with pytest.raises(ValueError, match="at least one graph unit"):
        agent.initial_assessment(case)


def discussion_fixture():
    case = case_input()
    initial = assessment_for(case)
    reference_unit = unit_with_role(case, EvidenceRole.REFERENCE_ONLY)
    pointer = pointer_for(reference_unit)
    opinion = SpecialistOpinion(
        specialty=MdtSpecialty.THORACIC_RADIOLOGY,
        opinion_id="radiology-001",
        summary="影像科正式意见",
        claims=[SpecialistClaim(claim="存在间质性影像异常", evidence=[pointer])],
        confidence="moderate",
    )
    discussion_input = PulmonologyDiscussionInput(
        case_input=case,
        initial_assessment=initial,
        specialist_opinions=[opinion],
        chair_questions=["影像意见是否改变呼吸科工作诊断？"],
    )
    return case, initial, pointer, opinion, discussion_input


def test_discussion_runs_mapping_update_and_consult_stages():
    case, initial, pointer, opinion, discussion_input = discussion_fixture()
    evidence_map = DiscussionEvidenceMap(
        specialist_opinions_used=[opinion.opinion_id],
        mapped_findings=[
            MappedSpecialistFinding(
                opinion_id=opinion.opinion_id,
                relationship="supplementary",
                affected_domains=[PulmonologyDomain.SPECIALIST_INTEGRATION],
                clinical_effect="正式影像意见可进入呼吸科疾病层面判断。",
                evidence=[pointer],
            )
        ],
    )
    updated = discussion_state(
        initial,
        diagnostic_formulation=formulation_for(pointer, opinion_id=opinion.opinion_id),
    )
    update = DiscussionStateUpdate(updated_state=updated, domain_changes=domain_changes())
    consult = DiscussionConsultOutput(
        chair_answers=[
            ChairAnswer(
                question_id="chair_q_001",
                answer="影像意见已被整合，但当前仍是呼吸科工作诊断。",
                confidence="moderate",
                supporting_evidence=[pointer],
                specialist_opinion_ids=[opinion.opinion_id],
            )
        ],
        diagnostic_recommendations=["如关键冲突仍存在，建议主席安排再次 MDD。"],
    )
    llm = FakeLLM([llm_payload(evidence_map), llm_payload(update), llm_payload(consult)])
    agent = PulmonologyAgent.from_config(CONFIG, llm, enable_guidelines=False)

    result, trace = agent.discussion_response(discussion_input)

    assert result.updated_state.phase == "discussion_update"
    assert result.chair_answers[0].question_id == "chair_q_001"
    assert [item["stage"] for item in trace["stages"]] == [
        "discussion_evidence_mapping",
        "discussion_state_update",
        "discussion_consult_response",
    ]
    assert "第 1 步专科证据映射" in llm.prompts[1][1].content
    assert "state_delta" not in llm.prompts[2][1].content


def test_discussion_authorizes_reference_evidence_by_exact_block_id():
    case, initial, authorized, opinion, discussion_input = discussion_fixture()
    other_block = pointer_for(unit_with_role(case, EvidenceRole.REFERENCE_ONLY), 1)
    state = discussion_state(
        initial,
        diagnostic_formulation=formulation_for(other_block, opinion_id=opinion.opinion_id),
    )
    response = PulmonologyDiscussionResponse(
        case_id=case.case_id,
        updated_state=state,
        domain_changes=domain_changes(),
        specialist_opinions_used=[opinion.opinion_id],
    )

    with pytest.raises(ValueError, match="精确引用相同 evidence ID"):
        validate_discussion_response(response, discussion_input)


def test_discussion_rejects_specialist_evidence_outside_scope():
    case = case_input()
    reference = unit_with_role(case, EvidenceRole.REFERENCE_ONLY)
    opinion = SpecialistOpinion(
        specialty=MdtSpecialty.PATHOLOGY,
        opinion_id="pathology-001",
        summary="越界引用影像证据",
        claims=[SpecialistClaim(claim="病理意见", evidence=[pointer_for(reference)])],
        confidence="low",
    )
    discussion_input = PulmonologyDiscussionInput(
        case_input=case,
        initial_assessment=assessment_for(case),
        specialist_opinions=[opinion],
    )
    agent = PulmonologyAgent.from_config(CONFIG, FakeLLM([]), enable_guidelines=False)

    with pytest.raises(ValueError, match="outside pathology's evidence scope"):
        agent.discussion_response(discussion_input)
