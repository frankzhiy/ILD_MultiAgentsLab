import json
from pathlib import Path
import shutil

import pytest

from scripts.agent_input.prepare_specialty_input import build_specialty_case_input
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
    assert result.summary.owned_unit_count == 4
    assert result.summary.shared_context_unit_count == 4
    assert result.summary.collaborative_context_unit_count == 2
    assert result.summary.reference_only_unit_count == 1

    units = {
        unit.graph_unit.graph_unit_id: unit for segment in result.segments for unit in segment.units
    }
    assert units["seg_003_gu_003"].evidence_role == EvidenceRole.COLLABORATIVE_CONTEXT
    assert units["seg_003_gu_003"].may_support_diagnostic_claim is True
    assert units["seg_003_gu_003"].allowed_uses == units["seg_003_gu_001"].allowed_uses
    assert len(units["seg_003_gu_003"].graph_unit.mdt_specialty) == 2
    assert units["seg_004_gu_001"].evidence_role == EvidenceRole.REFERENCE_ONLY
    assert units["seg_004_gu_001"].may_support_diagnostic_claim is False


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
