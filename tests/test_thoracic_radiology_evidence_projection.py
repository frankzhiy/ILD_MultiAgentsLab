import json

import pytest

from scripts.agent_input.prepare_specialty_input import build_specialty_case_input
from src.agents.thoracic_radiology.evidence_projection import (
    ProjectionDisposition,
    ProjectedStatementKind,
    build_radiology_working_input,
)
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty


RUN_0714 = "outputs/runs/20260714_163246_76-IPF_step2_step3"
RUN_0715 = "outputs/runs/20260715_121324_77-IPF_step2_step3"


def project(run_dir):
    case = build_specialty_case_input(run_dir, MdtSpecialty.THORACIC_RADIOLOGY)
    return case, build_radiology_working_input(case)


def statement(working, unit_id, proposition_id):
    return next(
        item
        for unit in working.evidence_units
        for item in unit.statements
        if item.graph_unit_id == unit_id and item.proposition_id == proposition_id
    )


def test_0714_keeps_all_units_for_orientation_but_projects_exact_ct_propositions():
    case, working = project(RUN_0714)

    assert working.summary.orientation_unit_count == case.summary.unit_count == 11
    assert working.summary.radiology_candidate_unit_count == 2
    assert working.summary.thoracic_evidence_unit_count == 2

    finding = statement(working, "seg_003_gu_003", "prop_006")
    assert finding.thoracic_imaging_eligible is True
    assert finding.kind == ProjectedStatementKind.REPORTED_FINDING
    assert finding.quote == "双肺间质增粗纹理走形杂乱"
    assert "抗感染治疗后" not in finding.quote

    report_impression = statement(working, "seg_003_gu_003", "prop_010")
    assert report_impression.kind == ProjectedStatementKind.REPORTED_IMPRESSION
    assert report_impression.thoracic_imaging_eligible is True

    treatment = statement(working, "seg_003_gu_003", "prop_011")
    outcome = statement(working, "seg_003_gu_003", "prop_012")
    admission_diagnosis = statement(working, "seg_003_gu_003", "prop_013")
    assert treatment.disposition == ProjectionDisposition.CLINICAL_CONTEXT
    assert outcome.disposition == ProjectionDisposition.CLINICAL_CONTEXT
    assert admission_diagnosis.disposition == ProjectionDisposition.CLINICAL_CONTEXT
    assert admission_diagnosis.thoracic_imaging_eligible is False


def test_0715_excludes_misrouted_echo_pft_and_limb_ultrasound_unit():
    case, working = project(RUN_0715)

    assert working.summary.orientation_unit_count == case.summary.unit_count == 18
    assert working.summary.radiology_candidate_unit_count == 2
    assert working.summary.thoracic_evidence_unit_count == 1
    assert [item.graph_unit_id for item in working.excluded_radiology_candidates] == [
        "seg_003_gu_001"
    ]
    assert [item.graph_unit_id for item in working.evidence_units] == ["seg_004_gu_003"]

    pe_statement = statement(working, "seg_004_gu_003", "prop_001")
    assert pe_statement.quote == "CTPA未见明确中央型肺栓塞直接征象"
    assert pe_statement.thoracic_imaging_eligible is True
    assert "排除肺栓塞" not in pe_statement.quote


def test_projection_is_substantially_smaller_than_shared_specialty_input():
    case, working = project(RUN_0715)
    full_size = len(json.dumps(case.model_dump(mode="json"), ensure_ascii=False))
    projected_size = len(json.dumps(working.model_dump(mode="json"), ensure_ascii=False))

    assert projected_size < full_size * 0.3


def test_projection_rejects_wrong_target_specialty():
    case = build_specialty_case_input(RUN_0714, MdtSpecialty.PULMONOLOGY)

    with pytest.raises(ValueError, match="target_specialty=thoracic_radiology"):
        build_radiology_working_input(case)
