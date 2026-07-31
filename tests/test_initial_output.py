from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.agents.common.initial_output import SpecialtyInitialOutput
from src.agents.common.initial_output_validation import (
    assign_specialty_initial_evidence,
    validate_specialty_initial_output,
)
from src.llm.structured import json_schema_response_format
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget


def pointer(evidence_id: str = "ev_1") -> dict:
    return {"evidence_ids": [evidence_id]}


def evidence_bundle() -> dict:
    return {"evidence_relations": []}


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


def test_formal_output_allows_probability_terms_in_clinical_text(monkeypatch):
    stub_evidence(monkeypatch)
    payload = output_payload()
    payload["specialty_assessments"]["assessments"][0]["medical_basis"] = (
        "当前资料不足以量化诊断概率或给出置信度。"
    )
    output = SpecialtyInitialOutput.model_validate(payload)

    validated = validate_specialty_initial_output(
        output, SimpleNamespace(), SpecialistTarget.PULMONOLOGY
    )

    assert validated is output


def test_formal_output_rejects_cross_specialty_conflict(monkeypatch):
    stub_evidence(monkeypatch)
    payload = output_payload()
    payload["specialty_assessments"]["assessments"][0]["medical_basis"] = (
        "与影像科意见存在冲突。"
    )
    output = SpecialtyInitialOutput.model_validate(payload)

    with pytest.raises(ValueError, match="cross-specialty conflict"):
        validate_specialty_initial_output(
            output, SimpleNamespace(), SpecialistTarget.PULMONOLOGY
        )


def test_formal_output_schema_forbids_confidence_result_fields():
    payload = output_payload()
    payload["specialty_assessments"]["assessments"][0]["confidence"] = "high"

    with pytest.raises(ValidationError, match="confidence"):
        SpecialtyInitialOutput.model_validate(payload)


def test_same_graph_unit_relations_are_merged_with_all_locators(monkeypatch):
    stub_evidence(monkeypatch)
    payload = output_payload()
    payload["specialty_assessments"]["assessments"][0]["evidence"]["evidence_relations"] = [
        {
            "segment_id": "seg_001",
            "graph_unit_id": "gu_001",
            "evidence_ids": ["ev_1"],
            "node_ids": ["node_1"],
            "quote": "双肺间质性增粗；",
            "direction": "supports",
            "function": "foundational",
        },
        {
            "segment_id": "seg_001",
            "graph_unit_id": "gu_001",
            "evidence_ids": ["ev_2"],
            "node_ids": ["node_2"],
            "quote": "伴局灶性肺实质异常。",
            "direction": "supports",
            "function": "foundational",
        },
    ]
    output = SpecialtyInitialOutput.model_validate(payload)

    validated = validate_specialty_initial_output(
        output,
        SimpleNamespace(),
        SpecialistTarget.PULMONOLOGY,
        diagnostic_evidence_ids={"ev_1", "ev_2"},
    )

    relations = validated.specialty_assessments.assessments[0].evidence.evidence_relations
    assert len(relations) == 1
    assert relations[0].graph_unit_id == "gu_001"
    assert relations[0].evidence_ids == ["ev_1", "ev_2"]
    assert relations[0].node_ids == ["node_1", "node_2"]


def test_one_relation_can_express_supporting_direction_and_qualifying_function(monkeypatch):
    stub_evidence(monkeypatch)
    payload = output_payload()
    payload["specialty_assessments"]["assessments"][0]["evidence"] = {
        "evidence_relations": [{
            "segment_id": "seg_001",
            "graph_unit_id": "gu_001",
            "evidence_ids": ["ev_1"],
            "quote": "胸部CT：双肺间质性增粗。",
            "direction": "supports",
            "function": "qualifying",
        }]
    }
    output = SpecialtyInitialOutput.model_validate(payload)

    validated = validate_specialty_initial_output(
        output,
        SimpleNamespace(),
        SpecialistTarget.PULMONOLOGY,
        diagnostic_evidence_ids={"ev_1"},
    )

    relation = validated.specialty_assessments.assessments[0].evidence.evidence_relations[0]
    assert relation.direction == "supports"
    assert relation.function == "qualifying"


def test_same_evidence_locator_cannot_appear_in_two_relations(monkeypatch):
    stub_evidence(monkeypatch)
    payload = output_payload()
    shared = {
        "segment_id": "seg_001",
        "graph_unit_id": "gu_001",
        "evidence_ids": ["ev_1"],
        "quote": "胸部CT：双肺间质性增粗。",
    }
    payload["specialty_assessments"]["assessments"][0]["evidence"] = {
        "evidence_relations": [
            {**shared, "direction": "supports", "function": "foundational"},
            {**shared, "direction": "neutral", "function": "qualifying"},
        ]
    }
    output = SpecialtyInitialOutput.model_validate(payload)

    with pytest.raises(ValueError, match="must appear once"):
        validate_specialty_initial_output(
            output,
            SimpleNamespace(),
            SpecialistTarget.PULMONOLOGY,
            diagnostic_evidence_ids={"ev_1"},
        )


def test_fixed_slots_allow_one_locator_once_per_atomic_claim(monkeypatch):
    unit = SimpleNamespace(
        may_support_diagnostic_claim=True,
        clinical_propositions=SimpleNamespace(
            evidence_blocks=[
                SimpleNamespace(evidence_id="ev_1", text="血气分析未见二氧化碳潴留。")
            ]
        ),
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.case_units",
        lambda _: {"gu_001": unit},
    )
    def resolve(value, *_):
        for item in value.specialty_assessments.assessments:
            for relation in item.evidence.evidence_relations:
                relation.graph_unit_id = "gu_001"

    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.resolve_evidence_pointers",
        resolve,
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.validate_pointers",
        lambda *args: None,
    )
    payload = output_payload()
    payload["specialty_assessments"]["assessments"][0]["claims"] = [
        {"statement": "未见二氧化碳潴留。"},
        {"statement": "总体严重度只能部分评价。"},
    ]
    output = SpecialtyInitialOutput.model_validate(payload)

    class Generator:
        def generate(self, *, schema_model, **kwargs):
            schema = json_schema_response_format(
                schema_model,
                "evidence_assignments",
                dependent_field_constraints=kwargs["dependent_field_constraints"],
            )["json_schema"]["schema"]
            valid_pairs = {
                (direction, function)
                for alternative in schema["$defs"]["_EvidenceSlotDecision"]["anyOf"]
                for direction in alternative["properties"]["direction"]["enum"]
                for function in alternative["properties"]["function"]["enum"]
            }
            assert ("neutral", "background") in valid_pairs
            assert ("supports", "foundational") in valid_pairs
            assert ("supports", "background") not in valid_pairs
            assert ("neutral", "foundational") not in valid_pairs
            return schema_model.model_validate({
                "slot_0001": {
                    "direction": "supports",
                    "function": "foundational",
                },
                "slot_0002": {
                    "direction": "neutral",
                    "function": "qualifying",
                },
            }), {"validated": True}

    validated, trace = assign_specialty_initial_evidence(
        output,
        SimpleNamespace(),
        SpecialistTarget.PULMONOLOGY,
        Generator(),
    )

    relations = validated.specialty_assessments.assessments[0].evidence.evidence_relations
    assert trace["slot_count"] == 2
    assert trace["batch_count"] == 1
    assert trace["batches"][0]["validated"] is True
    assert len(relations) == 2
    assert {relation.target_claim_id for relation in relations} == {
        "assessment_001_c001",
        "assessment_001_c002",
    }
    assert {relation.evidence_ids[0] for relation in relations} == {"ev_1"}


def test_fixed_slots_are_split_into_bounded_unique_batches(monkeypatch):
    unit = SimpleNamespace(
        may_support_diagnostic_claim=True,
        clinical_propositions=SimpleNamespace(
            evidence_blocks=[
                SimpleNamespace(evidence_id=f"ev_{number}", text=f"证据{number}")
                for number in range(1, 4)
            ]
        ),
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.case_units",
        lambda _: {"gu_001": unit},
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation._MAX_EVIDENCE_SLOTS_PER_CALL",
        2,
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.resolve_evidence_pointers",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "src.agents.common.initial_output_validation.validate_pointers",
        lambda *args: None,
    )
    output = SpecialtyInitialOutput.model_validate(output_payload())

    class Generator:
        def __init__(self):
            self.slot_ids = []

        def generate(self, *, schema_model, **kwargs):
            batch_slot_ids = list(schema_model.model_fields)
            self.slot_ids.extend(batch_slot_ids)
            return schema_model.model_validate(
                {slot_id: None for slot_id in batch_slot_ids}
            ), {"slot_ids": batch_slot_ids}

    generator = Generator()
    _, trace = assign_specialty_initial_evidence(
        output,
        SimpleNamespace(),
        SpecialistTarget.PULMONOLOGY,
        generator,
    )

    assert generator.slot_ids == ["slot_0001", "slot_0002", "slot_0003"]
    assert len(generator.slot_ids) == len(set(generator.slot_ids))
    assert trace["slot_count"] == 3
    assert trace["batch_count"] == 2


def test_legacy_evidence_role_lists_migrate_to_relations():
    payload = output_payload()
    payload["specialty_assessments"]["assessments"][0]["evidence"] = {
        "supporting": [pointer()],
        "weakening": [],
        "discriminating": [],
        "qualifying": [],
        "background": [],
    }

    output = SpecialtyInitialOutput.model_validate(payload)
    evidence = output.model_dump(mode="json")["specialty_assessments"]["assessments"][0][
        "evidence"
    ]

    assert set(evidence) == {"evidence_relations"}
    assert evidence["evidence_relations"][0]["direction"] == "supports"
    assert evidence["evidence_relations"][0]["function"] == "foundational"
