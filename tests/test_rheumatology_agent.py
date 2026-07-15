import json
from pathlib import Path

import pytest

from scripts.agent_input.prepare_specialty_input import build_specialty_case_input
from src.agents.rheumatology.agent import RheumatologyAgent
from src.agents.rheumatology.validation import validate_initial_stage
from src.agents.rheumatology.models import (
    ActivityAndRiskAssessment,
    AutoimmuneManifestation,
    ClinicalAssessmentItem,
    DiscussionConsultOutput,
    DiscussionEvidenceMap,
    DiscussionStateUpdate,
    DomainChange,
    EvidencePointer,
    IldAttributionAssessment,
    InitialAutoimmuneAssessment,
    InitialCaseReconstruction,
    InitialConsultFormulation,
    RheumaticDiseaseFormulation,
    RheumatologyClinicalState,
    RheumatologyDiscussionState,
    RheumatologyDiscussionInput,
    RheumatologyDomain,
    SerologicFinding,
)
from src.llm.base import LLMResponse
from src.reporting.rheumatology_report import render_rheumatology_report
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty


RUN_DIR = "outputs/runs/20260714_163246_76-IPF_step2_step3"


class FakeLLM:
    supports_json_schema = False

    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, *, temperature, max_tokens, response_format=None):
        content = json.dumps(self.responses.pop(0), ensure_ascii=False)
        return LLMResponse(content=content, raw={})


def case_input():
    return build_specialty_case_input(RUN_DIR, MdtSpecialty.RHEUMATOLOGY)


def pointer(case):
    unit = next(unit for segment in case.segments for unit in segment.units if unit.may_support_diagnostic_claim)
    return EvidencePointer(evidence_ids=[unit.clinical_propositions.evidence_blocks[0].evidence_id])


def review(domain, status="assessed"):
    return {"domain": domain, "status": status, "rationale": "已按风湿科规则复核。"}


def item(pointer, text="病例资料支持有限风湿科判断"):
    return ClinicalAssessmentItem(
        assessment=text,
        confidence="moderate",
        reasoning_summary="原始病例资料支持该有限判断。",
        supporting_evidence=[pointer],
    )


def stages(case):
    evidence = pointer(case)
    reconstruction = InitialCaseReconstruction(
        domain_reviews=[review(RheumatologyDomain.SOURCE_AND_EVALUABILITY), review(RheumatologyDomain.AUTOIMMUNE_PHENOTYPE)],
        case_orientation=item(evidence),
        autoimmune_manifestations=[
            AutoimmuneManifestation(
                **item(evidence, "存在待进一步核对的自身免疫线索").model_dump(),
                domain="joint",
                status="possible",
                temporal_relationship="与肺部病程的先后关系目前不清楚。",
            )
        ],
    )
    autoimmune = InitialAutoimmuneAssessment(
        domain_reviews=[
            review(RheumatologyDomain.SEROLOGIC_ASSESSMENT),
            review(RheumatologyDomain.RHEUMATIC_DISEASE_FORMULATION, "partially_assessable"),
            review(RheumatologyDomain.ACTIVITY_AND_RISK, "partially_assessable"),
        ],
        serologic_findings=[
            SerologicFinding(
                **item(evidence, "自身抗体结果需要结合临床解释").model_dump(),
                test_name="ANA",
                reported_result="病例记录存在相关检测信息。",
                interpretation="nonspecific",
            )
        ],
        rheumatic_disease_formulation=RheumaticDiseaseFormulation(
            **item(evidence, "当前不支持确定 CTD").model_dump(),
            classification_status="autoimmune_features_insufficient",
            leading_diagnosis=None,
        ),
        activity_and_risk=ActivityAndRiskAssessment(
            **item(evidence, "风险目前只能部分评价").model_dump(),
            disease_activity="uncertain",
            ild_risk="not_assessable",
        ),
    )
    formulation = InitialConsultFormulation(
        domain_reviews=[
            review(RheumatologyDomain.ILD_ATTRIBUTION, "not_assessable"),
            review(RheumatologyDomain.SPECIALIST_INTEGRATION_AND_GAPS, "partially_assessable"),
        ],
        ild_attribution=IldAttributionAssessment(
            **item(evidence, "当前不能判断 ILD 的风湿归因").model_dump(),
            attribution_strength="not_assessable",
        ),
    )
    return reconstruction, autoimmune, formulation


def payload(model):
    return model.model_dump(mode="json")


def test_initial_assessment_aggregates_three_stages_and_renders_report(tmp_path):
    case = case_input()
    reconstruction, autoimmune, formulation = stages(case)
    agent = RheumatologyAgent(
        FakeLLM([payload(reconstruction), payload(autoimmune), payload(formulation)]),
        initial_case_reconstruction_prompt_path="src/prompts/rheumatology/initial_case_reconstruction.md",
        initial_autoimmune_assessment_prompt_path="src/prompts/rheumatology/initial_autoimmune_assessment.md",
        initial_consult_formulation_prompt_path="src/prompts/rheumatology/initial_consult_formulation.md",
        discussion_evidence_mapping_prompt_path="src/prompts/rheumatology/discussion_evidence_mapping.md",
        discussion_state_update_prompt_path="src/prompts/rheumatology/discussion_state_update.md",
        discussion_consult_response_prompt_path="src/prompts/rheumatology/discussion_consult_response.md",
        clinical_rules={}, temperature=0, max_tokens=2000,
    )
    result, trace = agent.initial_assessment(case)

    assert result.rheumatic_disease_formulation.classification_status == "autoimmune_features_insufficient"
    assert result.ild_attribution.attribution_strength == "not_assessable"
    assert len(result.domain_reviews) == len(RheumatologyDomain)
    assert [stage["stage"] for stage in trace["stages"]] == [
        "initial_case_reconstruction", "initial_autoimmune_assessment", "initial_consult_formulation"
    ]
    report = render_rheumatology_report(result, case, tmp_path / "report.html")
    assert "风湿免疫科 首轮评估" in report.read_text(encoding="utf-8")


def test_initial_stage_schema_excludes_discussion_statuses_and_other_domains():
    schema = InitialConsultFormulation.model_json_schema()
    review_schema = schema["$defs"]["InitialConsultDomainReview"]

    assert set(review_schema["properties"]["domain"]["enum"]) == {
        "ild_attribution", "specialist_integration_and_gaps"
    }
    assert "updated" not in review_schema["properties"]["status"]["enum"]
    with pytest.raises(ValueError):
        InitialConsultFormulation(
            domain_reviews=[
                review(RheumatologyDomain.ILD_ATTRIBUTION, "updated"),
                review(RheumatologyDomain.SEROLOGIC_ASSESSMENT, "assessed"),
            ]
        )


def test_discussion_updates_same_state_without_chair_questions():
    case = case_input()
    reconstruction, autoimmune, formulation = stages(case)
    initial = RheumatologyClinicalState(
        case_id=case.case_id,
        phase="initial_assessment",
        domain_reviews=[
            review(domain, "partially_assessable" if domain != RheumatologyDomain.ILD_ATTRIBUTION else "not_assessable")
            for domain in RheumatologyDomain
        ],
        case_orientation=reconstruction.case_orientation,
        autoimmune_manifestations=reconstruction.autoimmune_manifestations,
        serologic_findings=autoimmune.serologic_findings,
        rheumatic_disease_formulation=autoimmune.rheumatic_disease_formulation,
        activity_and_risk=autoimmune.activity_and_risk,
        ild_attribution=formulation.ild_attribution,
    )
    initial = initial.model_copy(update={"phase": "initial_assessment"})
    from src.agents.rheumatology.models import RheumatologyInitialAssessment
    initial = RheumatologyInitialAssessment.model_validate(initial.model_dump())
    updated_data = initial.model_dump()
    updated_data.update(
        phase="discussion_update",
        domain_reviews=[review(domain, "reviewed_unchanged") for domain in RheumatologyDomain],
    )
    updated = RheumatologyDiscussionState(**updated_data)
    changes = [
        DomainChange(domain=domain, change_status="reviewed_unchanged", initial_view="首轮状态", updated_view="复核后不变", reason="无新的正式专科证据。")
        for domain in RheumatologyDomain
    ]
    evidence_map = DiscussionEvidenceMap()
    update = DiscussionStateUpdate(updated_state=updated, domain_changes=changes)
    consult = DiscussionConsultOutput()
    agent = RheumatologyAgent(
        FakeLLM([payload(evidence_map), payload(update), payload(consult)]),
        initial_case_reconstruction_prompt_path="src/prompts/rheumatology/initial_case_reconstruction.md",
        initial_autoimmune_assessment_prompt_path="src/prompts/rheumatology/initial_autoimmune_assessment.md",
        initial_consult_formulation_prompt_path="src/prompts/rheumatology/initial_consult_formulation.md",
        discussion_evidence_mapping_prompt_path="src/prompts/rheumatology/discussion_evidence_mapping.md",
        discussion_state_update_prompt_path="src/prompts/rheumatology/discussion_state_update.md",
        discussion_consult_response_prompt_path="src/prompts/rheumatology/discussion_consult_response.md",
        clinical_rules={}, temperature=0, max_tokens=2000,
    )
    result, _ = agent.discussion_response(RheumatologyDiscussionInput(case_input=case, initial_assessment=initial))
    assert result.updated_state.phase == "discussion_update"
    assert len(result.domain_changes) == len(RheumatologyDomain)


def test_rejects_non_rheumatology_input():
    case = case_input()
    case.target_specialty = MdtSpecialty.PULMONOLOGY
    reconstruction, _, _ = stages(case_input())
    with pytest.raises(ValueError, match="RheumatologyAgent"):
        RheumatologyAgent(
            FakeLLM([payload(reconstruction)]),
            initial_case_reconstruction_prompt_path="src/prompts/rheumatology/initial_case_reconstruction.md",
            initial_autoimmune_assessment_prompt_path="src/prompts/rheumatology/initial_autoimmune_assessment.md",
            initial_consult_formulation_prompt_path="src/prompts/rheumatology/initial_consult_formulation.md",
            discussion_evidence_mapping_prompt_path="src/prompts/rheumatology/discussion_evidence_mapping.md",
            discussion_state_update_prompt_path="src/prompts/rheumatology/discussion_state_update.md",
            discussion_consult_response_prompt_path="src/prompts/rheumatology/discussion_consult_response.md",
            clinical_rules={}, temperature=0, max_tokens=2000,
        ).initial_assessment(case)


def test_rejects_reference_only_evidence_as_initial_diagnostic_support():
    case = case_input()
    unit = next(unit for segment in case.segments for unit in segment.units if not unit.may_support_diagnostic_claim)
    reference = EvidencePointer(evidence_ids=[unit.clinical_propositions.evidence_blocks[0].evidence_id])
    stage = InitialCaseReconstruction(
        domain_reviews=[review(RheumatologyDomain.SOURCE_AND_EVALUABILITY), review(RheumatologyDomain.AUTOIMMUNE_PHENOTYPE)],
        case_orientation=item(reference),
    )
    with pytest.raises(ValueError, match="不能直接支持风湿科诊断性判断"):
        validate_initial_stage(stage, case)


def test_ipaf_classification_requires_a_working_label():
    with pytest.raises(ValueError, match="requires a leading diagnosis"):
        RheumaticDiseaseFormulation(
            assessment="IPAF 分类可能",
            confidence="low",
            reasoning_summary="当前仅用于验证模型约束。",
            classification_status="ipaf_classification_possible",
        )
