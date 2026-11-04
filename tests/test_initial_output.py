import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.agents.common.initial_output import SpecialtyInitialOutput
from src.agents.common.initial_output_validation import (
    validate_specialty_initial_output,
)
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
        "assessability": "not_assessable" if status == "not_assessable" else "partially_assessable",
        "direction": "indeterminate" if status == "not_assessable" else "supports",
        "confidence": "unknown" if status == "not_assessable" else "moderate",
        "clinical_role": "boundary" if status == "not_assessable" else "primary",
        "claims": [{"statement": "现有肺部异常需要病因鉴别。"}],
        "medical_basis": "病程与现有肺部资料形成连贯解释，替代病因仍需限定。",
        "decision_impact": "决定后续跨专科核对方向。",
        "evidence": evidence_bundle(),
        "guideline_evidence": [],
        "limitations": ["报告未描述病变分布，无法进一步区分影像模式。"],
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
                    "missing_information": "报告未描述病变分布。",
                    "why_it_matters": "不能可靠判断形态模式。",
                    "decision_unlocked": "完成影像模式判断。",
                    "related_assessment_ids": ["assessment_001"],
                    "related_evidence": [pointer()],
                }
            ],
            "boundaries": ["病原未定，限制具体病因判断。"],
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


@pytest.mark.parametrize('specialty', list(SpecialistTarget))
@pytest.mark.parametrize('text', [
    '未直接阅片。',
    '既有意见不构成跨专科冲突。',
    '未提供材料属于输入范围限制，不等于标本不充分、取样不具代表性或患者从未接受活检。',
])
def test_wording_does_not_reject_a_structurally_valid_opinion(monkeypatch, specialty, text):
    stub_evidence(monkeypatch)
    payload = output_payload()
    if specialty == SpecialistTarget.RHEUMATOLOGY:
        payload['specialty_assessments']['assessments'][0]['assessment_type'] = 'rheumatic_disease'
        payload['specialty_assessments']['assessments'].append(assessment('assessment_002', 'ild_attribution'))
    payload['specialty_assessments']['assessments'][0]['limitations'] = [text]
    output = SpecialtyInitialOutput.model_validate(payload)
    result = validate_specialty_initial_output(output, SimpleNamespace(), specialty)
    assert result.specialty_assessments.assessments[0].limitations == [text]


def test_limitations_are_not_copied_into_other_condition_fields():
    from src.agents.common.initial_output import synchronize_judgment_conditions
    payload = output_payload()
    item = SpecialtyInitialOutput.model_validate(payload).specialty_assessments.assessments[0]
    synchronize_judgment_conditions(item)
    assert item.limitations
    assert not item.conditions.evidence_scope.scope_limitations
    assert not item.conditions.applicability_conditions


def test_legacy_output_is_rejected_without_silent_migration():
    with pytest.raises(ValueError):
        SpecialtyInitialOutput.model_validate(legacy_payload())


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
def test_pathology_without_material_allows_boundary_without_request(monkeypatch, material_status):
    stub_evidence(monkeypatch)
    payload = output_payload()
    payload["specialty_assessments"]["assessability"] = "not_assessable"
    payload["specialty_assessments"]["evidence_gaps"] = []
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
    assert validated.specialty_assessments.evidence_gaps == []
    assert validated.interspecialty_questions.questions == []


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


def test_formal_output_does_not_use_keywords_to_detect_conflict(monkeypatch):
    stub_evidence(monkeypatch)
    payload = output_payload()
    payload["specialty_assessments"]["assessments"][0]["medical_basis"] = (
        "与影像科意见不存在冲突。"
    )
    output = SpecialtyInitialOutput.model_validate(payload)

    result = validate_specialty_initial_output(
        output, SimpleNamespace(), SpecialistTarget.PULMONOLOGY
    )
    assert result.specialty_assessments.assessments[0].medical_basis == '与影像科意见不存在冲突。'


def test_formal_output_accepts_independent_confidence():
    payload = output_payload()
    payload["specialty_assessments"]["assessments"][0]["confidence"] = "high"

    assert SpecialtyInitialOutput.model_validate(payload).specialty_assessments.assessments[0].confidence == "high"


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


def test_inline_claim_evidence_keeps_distinct_uses_and_resolves_sources():
    from synthetic_case import case_input
    case = case_input()
    unit = next(unit for segment in case.segments for unit in segment.units
                if unit.may_support_diagnostic_claim)
    block = unit.clinical_propositions.evidence_blocks[0]
    payload = output_payload()
    payload["specialty_assessments"]["evidence_gaps"] = []
    payload["specialty_assessments"]["assessments"][0]["claims"] = [
        {"statement": "当前症状支持肺部病变。", "evidence_relations": [
            {"evidence_ids": [block.evidence_id], "direction": "supports", "function": "foundational"},
        ]},
        {"statement": "同期发热限定慢性病变的解释范围。", "evidence_relations": [
            {"evidence_ids": [block.evidence_id], "direction": "neutral", "function": "qualifying"},
        ]},
    ]
    output = SpecialtyInitialOutput.model_validate(payload)
    validated = validate_specialty_initial_output(
        output, case, SpecialistTarget.PULMONOLOGY, require_claim_evidence=True,
    )
    item = validated.specialty_assessments.assessments[0]
    assert [claim.claim_id for claim in item.claims] == ["assessment_001_c001", "assessment_001_c002"]
    relations = item.evidence.evidence_relations
    assert len(relations) == 2
    assert {relation.target_claim_id for relation in relations} == {claim.claim_id for claim in item.claims}
    assert all(relation.quote == block.text for relation in relations)
    assert all(relation.graph_unit_id == unit.graph_unit.graph_unit_id for relation in relations)
    assert all(claim.evidence_relations[0].quote == block.text for claim in item.claims)
    assert item.conditions.evidence_scope.evidence_ids == [block.evidence_id]
    saved = validated.model_dump(mode="json")
    assert validate_specialty_initial_output(
        validated, case, SpecialistTarget.PULMONOLOGY, require_claim_evidence=True,
    ).model_dump(mode="json") == saved


@pytest.mark.parametrize("relations", [
    [{"evidence_ids": ["invented"], "direction": "supports", "function": "foundational"}],
    [{"evidence_ids": ["context"], "direction": "supports", "function": "foundational"}],
    [
        {"evidence_ids": ["diagnostic"], "direction": "supports", "function": "foundational"},
        {"evidence_ids": ["diagnostic"], "direction": "weakens", "function": "foundational"},
    ],
])
def test_inline_claim_rejects_unknown_ineligible_or_conflicting_evidence(relations):
    from synthetic_case import case_input
    case = case_input()
    units = [unit for segment in case.segments for unit in segment.units]
    units[0].may_support_diagnostic_claim = False
    ids = {"context": next(unit for unit in units if not unit.may_support_diagnostic_claim)
           .clinical_propositions.evidence_blocks[0].evidence_id,
           "diagnostic": next(unit for unit in units if unit.may_support_diagnostic_claim)
           .clinical_propositions.evidence_blocks[0].evidence_id}
    payload = output_payload()
    payload["specialty_assessments"]["evidence_gaps"] = []
    payload["specialty_assessments"]["assessments"][0]["claims"] = [{
        "statement": "当前肺部判断。",
        "evidence_relations": [{**relation, "evidence_ids": [ids.get(relation["evidence_ids"][0], "invented")]}
                               for relation in relations],
    }]
    with pytest.raises(ValueError, match="unknown evidence_ids|context-only|must appear once"):
        validate_specialty_initial_output(
            SpecialtyInitialOutput.model_validate(payload), case, SpecialistTarget.PULMONOLOGY,
            require_claim_evidence=True,
        )


def test_empty_inline_evidence_is_explicit_and_legacy_evidence_remains_readable(monkeypatch):
    stub_evidence(monkeypatch)
    payload = output_payload()
    output = SpecialtyInitialOutput.model_validate(payload)
    with pytest.raises(ValueError, match="explicitly return evidence_relations"):
        validate_specialty_initial_output(
            output, SimpleNamespace(), SpecialistTarget.PULMONOLOGY, require_claim_evidence=True,
        )
    payload["specialty_assessments"]["assessments"][0]["claims"] = [{
        "statement": "肺功能未提供，无法评价功能严重度。", "evidence_relations": [],
    }]
    validated = validate_specialty_initial_output(
        SpecialtyInitialOutput.model_validate(payload), SimpleNamespace(), SpecialistTarget.PULMONOLOGY,
        require_claim_evidence=True,
    )
    assert not validated.specialty_assessments.assessments[0].evidence.evidence_relations
    legacy = output_payload()
    legacy["specialty_assessments"]["assessments"][0]["evidence"] = {
        "evidence_relations": [{"evidence_ids": ["ev_1"], "direction": "supports", "function": "foundational"}],
    }
    assert validate_specialty_initial_output(
        SpecialtyInitialOutput.model_validate(legacy), SimpleNamespace(), SpecialistTarget.PULMONOLOGY,
        diagnostic_evidence_ids={"ev_1"},
    ).specialty_assessments.assessments[0].evidence.evidence_relations


def test_legacy_evidence_role_lists_are_rejected():
    payload = output_payload()
    payload["specialty_assessments"]["assessments"][0]["evidence"] = {"supporting": [pointer()]}
    with pytest.raises(ValueError):
        SpecialtyInitialOutput.model_validate(payload)
