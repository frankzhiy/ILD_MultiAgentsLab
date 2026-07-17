from pathlib import Path

from scripts.agent_input.prepare_specialty_input import (
    build_specialty_case_input as build_cli_specialty_input,
)
from src.agents.common.specialty_input import build_specialty_case_input
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty


RUN_DIR = Path("outputs/runs/20260716_163006_86-IPF_step2_step3")


def test_backend_specialty_projection_matches_established_cli_logic():
    backend = build_specialty_case_input(RUN_DIR, MdtSpecialty.PULMONOLOGY)
    established = build_cli_specialty_input(RUN_DIR, MdtSpecialty.PULMONOLOGY)
    assert backend.model_dump(mode="json") == established.model_dump(mode="json")
