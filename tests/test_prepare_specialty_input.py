import json
from pathlib import Path
import shutil

import pytest

from scripts.agent_input.prepare_specialty_input import build_specialty_case_input
from src.agents.common.evidence_projection import (
    build_specialty_evidence_prompt_input,
    build_specialty_working_input,
)
from src.agents.thoracic_radiology.evidence_projection import (
    build_radiology_evidence_prompt_input,
    build_radiology_reconstruction_prompt_input,
    build_radiology_working_input,
)
from src.llm.prompting import prompt_json
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import EvidenceRole


RUN_DIR = Path("outputs/runs/20260714_163246_76-IPF_step2_step3")
CASE_ID = "76-IPF"
SUFFIXES = (
    "discourse_segments",
    "graph_units",
    "primary_frames",
    "clinical_propositions",
    "proposition_validation",
    "local_graphs",
)


def copy_semantic_outputs(destination: Path) -> None:
    for suffix in SUFFIXES:
        shutil.copy(RUN_DIR / f"{CASE_ID}_{suffix}.json", destination)


def test_builds_ordered_complete_pulmonology_input_from_current_run():
    result = build_specialty_case_input(RUN_DIR, MdtSpecialty.PULMONOLOGY)

    assert [segment.segment.segment_id for segment in result.segments] == [
        "seg_001",
        "seg_002",
        "seg_003",
        "seg_004",
        "seg_005",
        "seg_006",
    ]
    assert [
        unit.graph_unit.graph_unit_id for segment in result.segments for unit in segment.units
    ] == [
        "seg_001_gu_001",
        "seg_001_gu_002",
        "seg_002_gu_001",
        "seg_002_gu_002",
        "seg_003_gu_001",
        "seg_003_gu_002",
        "seg_003_gu_003",
        "seg_003_gu_004",
        "seg_004_gu_001",
        "seg_005_gu_001",
        "seg_006_gu_001",
    ]
    assert result.summary.unit_count == 11
    assert result.summary.owned_unit_count == 6
    assert result.summary.shared_context_unit_count == 4
    assert result.summary.reference_only_unit_count == 1

    units = {
        unit.graph_unit.graph_unit_id: unit for segment in result.segments for unit in segment.units
    }
    assert units["seg_003_gu_003"].evidence_role == EvidenceRole.OWNED
    assert units["seg_003_gu_003"].may_support_diagnostic_claim is True
    assert units["seg_003_gu_003"].allowed_uses == units["seg_003_gu_001"].allowed_uses
    assert len(units["seg_003_gu_003"].graph_unit.mdt_specialty) == 2
    assert units["seg_004_gu_001"].evidence_role == EvidenceRole.REFERENCE_ONLY
    assert units["seg_004_gu_001"].may_support_diagnostic_claim is False
    assert all(
        unit.may_support_diagnostic_claim
        == ("diagnostic_support" in unit.allowed_uses)
        for unit in units.values()
    )


def test_working_input_preserves_verbatim_sources_without_semantic_graph_payloads():
    full = build_specialty_case_input(RUN_DIR, MdtSpecialty.PULMONOLOGY)
    working = build_specialty_working_input(full)

    assert [item.segment.text for item in working.segments] == [
        item.segment.text for item in full.segments
    ]
    assert [unit.graph_unit.text for item in working.segments for unit in item.units] == [
        unit.graph_unit.text for item in full.segments for unit in item.units
    ]
    assert [
        block.model_dump()
        for item in working.segments
        for unit in item.units
        for block in unit.evidence_blocks
    ] == [
        block.model_dump()
        for item in full.segments
        for unit in item.units
        for block in unit.clinical_propositions.evidence_blocks
    ]

    working_json = working.model_dump_json()
    full_json = full.model_dump_json()
    assert len(working_json) < len(full_json) * 0.2
    for omitted_key in (
        '"local_graph"',
        '"clinical_propositions"',
        '"proposition_validation"',
        '"primary_frame"',
        '"source_run_dir"',
    ):
        assert omitted_key not in working_json


def test_later_specialty_and_radiology_stage_payloads_are_bounded():
    pulmonary = build_specialty_case_input(RUN_DIR, MdtSpecialty.PULMONOLOGY)
    full_working = prompt_json(build_specialty_working_input(pulmonary))
    later = prompt_json(build_specialty_evidence_prompt_input(pulmonary))
    assert len(later) < len(full_working) * 0.6
    later_value = json.loads(later)
    assert "units" not in later_value
    assert later_value["diagnostic_evidence_units"]
    assert later_value["context_only_evidence_units"]

    radiology = build_specialty_case_input(RUN_DIR, MdtSpecialty.THORACIC_RADIOLOGY)
    audit = build_radiology_working_input(radiology)
    reconstruction = prompt_json(
        build_radiology_reconstruction_prompt_input(radiology, audit)
    )
    evidence = prompt_json(build_radiology_evidence_prompt_input(audit))
    assert len(reconstruction) < len(audit.model_dump_json()) * 0.5
    assert len(evidence) < len(audit.model_dump_json()) * 0.35


def test_rejects_conflicting_embedded_and_separate_primary_frames(tmp_path):
    copy_semantic_outputs(tmp_path)
    path = tmp_path / f"{CASE_ID}_graph_units.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["segments"][0]["graph_units"][0]["primary_frame"] = "encounter"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="embeds primary_frame"):
        build_specialty_case_input(tmp_path, MdtSpecialty.PULMONOLOGY)


def test_builds_same_complete_input_for_thoracic_radiology_with_new_roles():
    result = build_specialty_case_input(RUN_DIR, MdtSpecialty.THORACIC_RADIOLOGY)

    assert result.target_specialty == MdtSpecialty.THORACIC_RADIOLOGY
    assert [
        unit.graph_unit.graph_unit_id for segment in result.segments for unit in segment.units
    ] == [
        "seg_001_gu_001",
        "seg_001_gu_002",
        "seg_002_gu_001",
        "seg_002_gu_002",
        "seg_003_gu_001",
        "seg_003_gu_002",
        "seg_003_gu_003",
        "seg_003_gu_004",
        "seg_004_gu_001",
        "seg_005_gu_001",
        "seg_006_gu_001",
    ]
    assert result.summary.model_dump() == {
        "segment_count": 6,
        "unit_count": 11,
        "owned_unit_count": 2,
        "shared_context_unit_count": 4,
        "reference_only_unit_count": 5,
        "available_locator_count": 11,
        "degraded_locator_count": 0,
    }

    units = {
        unit.graph_unit.graph_unit_id: unit for segment in result.segments for unit in segment.units
    }
    assert units["seg_003_gu_003"].evidence_role == EvidenceRole.OWNED
    assert units["seg_004_gu_001"].evidence_role == EvidenceRole.OWNED
    assert units["seg_005_gu_001"].evidence_role == EvidenceRole.REFERENCE_ONLY


def test_rejects_misaligned_unit_ids(tmp_path):
    copy_semantic_outputs(tmp_path)
    path = tmp_path / f"{CASE_ID}_primary_frames.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["segments"][0]["units"].pop()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="do not align"):
        build_specialty_case_input(tmp_path, MdtSpecialty.PULMONOLOGY)


def test_keeps_unit_text_when_locator_is_degraded(tmp_path):
    copy_semantic_outputs(tmp_path)
    validation_path = tmp_path / f"{CASE_ID}_proposition_validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["segments"][0]["units"][0]["is_graph_ready"] = False
    validation_path.write_text(json.dumps(validation, ensure_ascii=False), encoding="utf-8")

    graph_path = tmp_path / f"{CASE_ID}_local_graphs.json"
    graphs = json.loads(graph_path.read_text(encoding="utf-8"))
    graphs["segments"][0]["units"][0]["build_status"] = "blocked"
    graph_path.write_text(json.dumps(graphs, ensure_ascii=False), encoding="utf-8")

    result = build_specialty_case_input(tmp_path, MdtSpecialty.PULMONOLOGY)
    unit = result.segments[0].units[0]

    assert unit.locator_status == "degraded"
    assert unit.graph_unit.text == "患者,女,77岁"
    assert result.summary.degraded_locator_count == 1


def test_shared_context_cannot_be_target_specialty():
    with pytest.raises(ValueError, match="not a target specialty"):
        build_specialty_case_input(RUN_DIR, MdtSpecialty.SHARED_CONTEXT)
