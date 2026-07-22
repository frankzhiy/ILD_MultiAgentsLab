from types import MethodType, SimpleNamespace

import pytest
from pydantic import BaseModel

from src.agents.common.initial_output import SpecialtyInitialOutput
from src.agents.common.initial_output_validation import validate_specialty_initial_output
from src.agents.pathology.agent import PathologyAgent
from src.agents.pulmonology.agent import PulmonologyAgent
from src.agents.rheumatology.agent import RheumatologyAgent
from src.agents.thoracic_radiology.agent import ThoracicRadiologyAgent
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget


def evidence_bundle():
    return {
        "supporting": [],
        "weakening": [],
        "discriminating": [],
        "background": [],
    }


def output_payload(*, target=None, candidates=True, conclusion_types=("working_diagnosis",)):
    questions = []
    if target:
        questions.append(
            {
                "target_specialty": target,
                "question": "请回答会改变本专科判断的具体问题。",
                "why_it_matters": "该答案影响当前结论边界。",
                "decision_unlocked": "决定能否提高分类程度。",
                "related_evidence": [],
            }
        )
    conclusions = [
        {
            "conclusion_id": f"conclusion_{index}",
            "role": "primary" if index == 1 else "scope_or_evaluability",
            "conclusion_type": conclusion_type,
            "statement": f"第 {index} 项当前专业结论。",
            "status": "favored",
            "medical_basis": "由现有专科证据和内部状态综合形成。",
            "decision_impact": "限定首轮专业判断。",
            "evidence": evidence_bundle(),
            "guideline_evidence": [],
            "limitations": [],
        }
        for index, conclusion_type in enumerate(conclusion_types, start=1)
    ]
    candidate_items = (
        [
            {
                "candidate_id": "candidate_1",
                "explanation": "当前主导候选解释。",
                "role": "leading",
                "fit_summary": "能够解释当前主要表现。",
                "evidence": evidence_bundle(),
                "guideline_evidence": [],
                "remaining_uncertainty": "仍缺少一项决定性资料。",
            }
        ]
        if candidates
        else []
    )
    return {
        "professional_conclusions": {
            "specialty_question": "本专科在当前资料范围内能够回答什么？",
            "assessability": "partially_assessable",
            "conclusions": conclusions,
            "interspecialty_questions": questions,
            "evidence_gaps": [],
            "boundaries": ["本结论不是最终 MDT 诊断。"],
        },
        "clinical_reasoning": {
            "problem_representation": "一次性首轮专科问题表征。",
            "candidate_explanations": candidate_items,
            "evidence_comparisons": [],
            "consistency_checks": [],
            "boundary_reviews": [
                {
                    "review_id": "boundary_1",
                    "boundary_type": "specialty_scope",
                    "finding": "结论保持在本专科权限内。",
                    "impact": "不升级为跨专科结论。",
                    "evidence": evidence_bundle(),
                }
            ],
            "synthesis": "现有证据支持有限的首轮专业结论。",
        },
    }


def pathology_material_plan_payload():
    payload = output_payload(
        target=SpecialistTarget.PULMONOLOGY,
        candidates=False,
        conclusion_types=("material_evaluability", "morphologic_pattern"),
    )
    payload["professional_conclusions"]["assessability"] = "not_assessable"
    for conclusion in payload["professional_conclusions"]["conclusions"]:
        conclusion["status"] = "not_assessable"
    payload["professional_conclusions"]["evidence_gaps"] = [
        {
            "available_information": "当前输入未提供可评价病理材料。",
            "missing_information": "既往病理报告、取材信息及可复核切片。",
            "why_it_matters": "这些资料决定能否开展组织学评估。",
            "decision_unlocked": "确认材料充分性、代表性及可评价模式范围。",
            "related_evidence": [],
        }
    ]
    return payload


def test_formal_output_has_exactly_two_top_level_sections():
    output = SpecialtyInitialOutput.model_validate(output_payload())

    assert set(output.model_dump(mode="json")) == {
        "professional_conclusions",
        "clinical_reasoning",
    }
    schema = SpecialtyInitialOutput.model_json_schema()
    assert "schema_version" not in str(schema)
    assert "confidence" not in str(schema)
    assert "probability" not in str(schema)
    assert "conflicts" not in str(schema)

    payload = output_payload()
    payload["schema_version"] = "specialty_initial.v1"
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        SpecialtyInitialOutput.model_validate(payload)


def test_validation_rejects_question_to_issuing_specialty(monkeypatch):
    output = SpecialtyInitialOutput.model_validate(
        output_payload(target=SpecialistTarget.PULMONOLOGY)
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.resolve_evidence_pointers",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.case_units", lambda _: {}
    )

    with pytest.raises(ValueError, match="cannot target the issuing specialty"):
        validate_specialty_initial_output(
            output, SimpleNamespace(), SpecialistTarget.PULMONOLOGY
        )


def test_pathology_without_material_cannot_construct_pattern(monkeypatch):
    output = SpecialtyInitialOutput.model_validate(
        output_payload(conclusion_types=("morphologic_pattern",))
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.resolve_evidence_pointers",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.case_units", lambda _: {}
    )
    internal_state = SimpleNamespace(
        source_assessment=SimpleNamespace(material_status="no_pathology_material")
    )

    with pytest.raises(ValueError, match="cannot construct a pattern candidate"):
        validate_specialty_initial_output(
            output,
            SimpleNamespace(),
            SpecialistTarget.PATHOLOGY,
            internal_state,
        )


@pytest.mark.parametrize(
    "material_status",
    [
        "no_pathology_material",
        "pathology_mentioned_without_report",
        "uncertain_availability",
    ],
)
def test_pathology_without_assessable_material_returns_a_recovery_plan(
    monkeypatch, material_status
):
    output = SpecialtyInitialOutput.model_validate(pathology_material_plan_payload())
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.resolve_evidence_pointers",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.case_units", lambda _: {}
    )
    internal_state = SimpleNamespace(
        source_assessment=SimpleNamespace(material_status=material_status)
    )

    validated = validate_specialty_initial_output(
        output,
        SimpleNamespace(),
        SpecialistTarget.PATHOLOGY,
        internal_state,
    )

    assert validated.clinical_reasoning.candidate_explanations == []
    assert validated.professional_conclusions.evidence_gaps[0].decision_unlocked


def test_rheumatology_requires_separate_disease_and_ild_attribution(monkeypatch):
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.resolve_evidence_pointers",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.case_units", lambda _: {}
    )
    incomplete = SpecialtyInitialOutput.model_validate(output_payload())

    with pytest.raises(ValueError, match="requires separate conclusion types"):
        validate_specialty_initial_output(
            incomplete, SimpleNamespace(), SpecialistTarget.RHEUMATOLOGY
        )

    complete = SpecialtyInitialOutput.model_validate(
        output_payload(conclusion_types=("rheumatic_disease", "ild_attribution"))
    )
    validate_specialty_initial_output(
        complete, SimpleNamespace(), SpecialistTarget.RHEUMATOLOGY
    )


def test_numeric_physiology_percent_is_allowed_but_probability_percent_is_not(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.resolve_evidence_pointers",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.case_units", lambda _: {}
    )
    output = SpecialtyInitialOutput.model_validate(output_payload())
    output.clinical_reasoning.synthesis = (
        "FVC 较基线下降 8%，属于病例生理事实；当前保留两种可能性。"
    )
    validate_specialty_initial_output(
        output, SimpleNamespace(), SpecialistTarget.PULMONOLOGY
    )

    output.clinical_reasoning.synthesis = "诊断概率为 80%。"
    with pytest.raises(ValueError, match="probability or confidence"):
        validate_specialty_initial_output(
            output, SimpleNamespace(), SpecialistTarget.PULMONOLOGY
        )

    output.clinical_reasoning.synthesis = "当前诊断置信度较低。"
    with pytest.raises(ValueError, match="probability or confidence"):
        validate_specialty_initial_output(
            output, SimpleNamespace(), SpecialistTarget.PULMONOLOGY
        )


def test_validation_rejects_repeated_candidate_in_comparison(monkeypatch):
    payload = output_payload()
    payload["clinical_reasoning"]["evidence_comparisons"] = [
        {
            "comparison_id": "comparison_1",
            "effect": "supports",
            "candidate_ids": ["candidate_1", "candidate_1"],
            "interpretation": "同一候选不能在一次比较中重复引用。",
            "evidence": evidence_bundle(),
        }
    ]
    output = SpecialtyInitialOutput.model_validate(payload)
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.resolve_evidence_pointers",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.case_units", lambda _: {}
    )

    with pytest.raises(ValueError, match="repeats candidate_ids"):
        validate_specialty_initial_output(
            output, SimpleNamespace(), SpecialistTarget.PULMONOLOGY
        )


@pytest.mark.parametrize(
    ("module_name", "agent_class"),
    [
        ("pulmonology", PulmonologyAgent),
        ("rheumatology", RheumatologyAgent),
        ("pathology", PathologyAgent),
        ("thoracic_radiology", ThoracicRadiologyAgent),
    ],
)
def test_four_agents_expose_initial_consult(monkeypatch, module_name, agent_class):
    output = SpecialtyInitialOutput.model_validate(output_payload())
    internal_state = output
    agent = object.__new__(agent_class)
    agent.clinical_rules = {}
    agent.initial_assessment = MethodType(
        lambda self, case: (internal_state, {"stages": [{"stage": "internal"}]}),
        agent,
    )
    agent._generate = MethodType(
        lambda self, *args, **kwargs: (output, {"schema_name": "specialty_initial"}),
        agent,
    )
    module = f"src.agents.{module_name}.agent"
    monkeypatch.setattr(
        f"{module}.formal_evidence_schema_constraints", lambda *args: {}
    )
    if module_name == "thoracic_radiology":
        monkeypatch.setattr(
            f"{module}.build_radiology_working_input",
            lambda case: SimpleNamespace(evidence_units=[]),
        )
        monkeypatch.setattr(
            f"{module}.build_radiology_evidence_prompt_input", lambda working: {}
        )
    else:
        monkeypatch.setattr(
            f"{module}.build_specialty_evidence_prompt_input", lambda case: {}
        )

    result = agent.initial_consult(SimpleNamespace())

    assert result.internal_state is internal_state
    assert result.formal_output is output
    assert result.trace["stages"][-1]["stage"] == "initial_reasoning_output"


def test_pathology_material_plan_schema_forbids_candidates(monkeypatch):
    class MaterialSource(BaseModel):
        material_status: str

    class InternalState(BaseModel):
        source_assessment: MaterialSource

    output_payload_value = pathology_material_plan_payload()
    internal_state = InternalState(
        source_assessment=MaterialSource(material_status="no_pathology_material")
    )
    agent = object.__new__(PathologyAgent)
    agent.clinical_rules = {}
    agent.initial_assessment = MethodType(
        lambda self, case: (internal_state, {"stages": []}), agent
    )
    captured = {}

    def generate(self, stage, schema_model, *args, **kwargs):
        captured["schema_model"] = schema_model
        return schema_model.model_validate(output_payload_value), {}

    agent._generate = MethodType(generate, agent)
    monkeypatch.setattr(
        "src.agents.pathology.agent.build_specialty_evidence_prompt_input", lambda case: {}
    )
    monkeypatch.setattr(
        "src.agents.pathology.agent.formal_evidence_schema_constraints", lambda case: {}
    )
    result = agent.initial_consult(SimpleNamespace())

    assert result.formal_output.clinical_reasoning.candidate_explanations == []
    invalid = pathology_material_plan_payload()
    invalid["clinical_reasoning"]["candidate_explanations"] = output_payload()[
        "clinical_reasoning"
    ]["candidate_explanations"]
    with pytest.raises(ValueError, match="at most 0 items"):
        captured["schema_model"].model_validate(invalid)
