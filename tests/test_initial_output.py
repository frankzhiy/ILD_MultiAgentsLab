from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.agents.common.initial_output import SpecialtyInitialOutput
from src.agents.common.initial_output_validation import validate_specialty_initial_output
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget


def pointer(evidence_id: str = "ev_1") -> dict:
    return {"evidence_ids": [evidence_id]}


def evidence_bundle() -> dict:
    return {
        "supporting": [],
        "weakening": [],
        "discriminating": [],
        "background": [],
    }


def assessment(
    assessment_id: str = "assessment_001",
    assessment_type: str = "working_diagnosis",
    status: str = "favored",
) -> dict:
    return {
        "assessment_id": assessment_id,
        "role": "primary",
        "assessment_type": assessment_type,
        "statement": "当前倾向未分类间质性肺病工作判断。",
        "status": status,
        "medical_basis": "病程与现有肺部资料形成连贯解释，替代病因仍需限定。",
        "decision_impact": "决定后续跨专科核对方向。",
        "evidence": evidence_bundle(),
        "guideline_evidence": [],
        "limitations": ["缺少原始薄层影像。"],
    }


def question(target: str = "thoracic_radiology") -> dict:
    return {
        "target_specialty": target,
        "question": "现有影像能否支持特定形态模式？",
        "why_it_matters": "形态判断影响疾病层工作判断。",
        "decision_unlocked": "限定当前工作判断强度。",
        "related_assessment_ids": ["assessment_001"],
        "related_evidence": [pointer()],
    }


def output_payload(*, questions: list[dict] | None = None) -> dict:
    return {
        "specialty_assessments": {
            "specialty_question": "本专科在当前资料范围内能够形成什么初步判断？",
            "assessability": "partially_assessable",
            "assessments": [assessment()],
            "evidence_gaps": [
                {
                    "available_information": "仅有影像报告摘录。",
                    "missing_information": "缺少原始薄层影像。",
                    "why_it_matters": "不能可靠判断形态模式。",
                    "decision_unlocked": "完成影像模式判断。",
                    "related_assessment_ids": ["assessment_001"],
                    "related_evidence": [pointer()],
                }
            ],
            "boundaries": ["本判断不是最终 MDT 诊断。"],
        },
        "interspecialty_questions": {"questions": questions or []},
    }


def legacy_payload() -> dict:
    return {
        "professional_conclusions": {
            "specialty_question": "旧版问题",
            "assessability": "partially_assessable",
            "conclusions": [
                {
                    **assessment(),
                    "conclusion_id": "assessment_001",
                    "conclusion_type": "working_diagnosis",
                }
            ],
            "interspecialty_questions": [question()],
            "evidence_gaps": [],
            "boundaries": ["旧版边界。"],
        },
        "clinical_reasoning": {"legacy": "ignored"},
    }


def stub_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.resolve_evidence_pointers",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.case_units", lambda _: {}
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.validate_pointers",
        lambda *args: None,
    )


def test_formal_output_has_exactly_two_top_level_sections():
    output = SpecialtyInitialOutput.model_validate(output_payload(questions=[question()]))

    assert set(output.model_dump(mode="json")) == {
        "specialty_assessments",
        "interspecialty_questions",
    }
    schema = SpecialtyInitialOutput.model_json_schema()
    assert "clinical_reasoning" not in str(schema)
    assert "professional_conclusions" not in schema["properties"]


def test_legacy_output_is_readable_but_serializes_with_current_labels():
    payload = legacy_payload()
    payload["professional_conclusions"]["conclusions"][0].pop("assessment_id")
    payload["professional_conclusions"]["conclusions"][0].pop("assessment_type")

    output = SpecialtyInitialOutput.model_validate(payload)

    assert output.specialty_assessments.assessments[0].assessment_id == "assessment_001"
    assert output.interspecialty_questions.questions[0].target_specialty == "thoracic_radiology"
    assert set(output.model_dump(mode="json")) == {
        "specialty_assessments",
        "interspecialty_questions",
    }


def test_extra_top_level_section_is_rejected():
    payload = output_payload()
    payload["extra"] = {}
    with pytest.raises(ValidationError):
        SpecialtyInitialOutput.model_validate(payload)


def test_question_cannot_target_issuing_specialty(monkeypatch):
    stub_evidence(monkeypatch)
    output = SpecialtyInitialOutput.model_validate(
        output_payload(questions=[question("pulmonology")])
    )

    with pytest.raises(ValueError, match="cannot target the issuing specialty"):
        validate_specialty_initial_output(
            output, SimpleNamespace(), SpecialistTarget.PULMONOLOGY
        )


def test_question_and_evidence_need_must_reference_known_assessment(monkeypatch):
    stub_evidence(monkeypatch)
    payload = output_payload(questions=[question()])
    payload["interspecialty_questions"]["questions"][0][
        "related_assessment_ids"
    ] = ["missing"]
    output = SpecialtyInitialOutput.model_validate(payload)

    with pytest.raises(ValueError, match="unknown assessment_ids"):
        validate_specialty_initial_output(
            output, SimpleNamespace(), SpecialistTarget.PULMONOLOGY
        )


def test_rheumatology_requires_disease_and_ild_attribution_assessments(monkeypatch):
    stub_evidence(monkeypatch)
    output = SpecialtyInitialOutput.model_validate(output_payload())

    with pytest.raises(ValueError, match="requires separate assessment types"):
        validate_specialty_initial_output(
            output, SimpleNamespace(), SpecialistTarget.RHEUMATOLOGY
        )


@pytest.mark.parametrize(
    "material_status",
    [
        "no_pathology_material",
        "pathology_mentioned_without_report",
        "uncertain_availability",
    ],
)
def test_pathology_without_material_requires_boundary_plan(monkeypatch, material_status):
    stub_evidence(monkeypatch)
    payload = output_payload(questions=[question("pulmonology")])
    payload["specialty_assessments"]["assessability"] = "not_assessable"
    payload["specialty_assessments"]["assessments"] = [
        assessment(
            assessment_type="material_evaluability",
            status="not_assessable",
        )
    ]
    output = SpecialtyInitialOutput.model_validate(payload)

    validated = validate_specialty_initial_output(
        output,
        SimpleNamespace(),
        SpecialistTarget.PATHOLOGY,
        SimpleNamespace(
            source_assessment=SimpleNamespace(material_status=material_status)
        ),
    )

    assert validated.specialty_assessments.assessability == "not_assessable"
    assert validated.specialty_assessments.evidence_gaps
    assert validated.interspecialty_questions.questions


def test_formal_output_rejects_probability_and_cross_specialty_conflict(monkeypatch):
    stub_evidence(monkeypatch)
    payload = output_payload()
    payload["specialty_assessments"]["assessments"][0]["medical_basis"] = (
        "诊断概率为 80%，并且与影像科意见存在冲突。"
    )
    output = SpecialtyInitialOutput.model_validate(payload)

    with pytest.raises(ValueError, match="probability or confidence"):
        validate_specialty_initial_output(
            output, SimpleNamespace(), SpecialistTarget.PULMONOLOGY
        )
