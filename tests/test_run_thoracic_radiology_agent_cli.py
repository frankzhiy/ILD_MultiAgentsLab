from pathlib import Path

import scripts.run.run_thoracic_radiology_agent as runner
from scripts.agent_input.prepare_specialty_input import build_specialty_case_input
from src.agents.thoracic_radiology.models import (
    DescriptionDerivedObservationState,
    DomainReview,
    ImagingInterpretationState,
    ImagingSourceState,
    ThoracicRadiologyDomain,
    ThoracicRadiologyInitialAssessment,
)
from src.llm.structured import StructuredGenerationError
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty


RUN_DIR = Path("outputs/runs/20260714_163246_76-IPF_step2_step3")


def minimal_assessment(case):
    return ThoracicRadiologyInitialAssessment(
        case_id=case.case_id,
        domain_reviews=[
            DomainReview(
                domain=domain,
                status="not_assessable",
                rationale="测试状态。",
            )
            for domain in ThoracicRadiologyDomain
        ],
        source_state=ImagingSourceState(
            overall_evaluability="insufficient_for_pattern_assessment",
            reasoning_summary="测试输入不可评价。",
        ),
        observation_state=DescriptionDerivedObservationState(reasoning_summary="没有可提取观察。"),
        interpretation_state=ImagingInterpretationState(),
    )


def test_failure_trace_uses_radiology_schema(tmp_path):
    error = StructuredGenerationError(
        "validation failed",
        attempts=[{"attempt": 1, "content": "raw invalid response"}],
        stage="initial_morphologic_assessment",
    )

    path = runner.write_failure_trace(tmp_path, "case_thoracic_radiology", "initial", error)
    trace = runner.read_json(path)

    assert trace["schema_version"] == "thoracic_radiology.v1"
    assert trace["failed_stage"] == "initial_morphologic_assessment"


def test_main_uses_shared_input_builder_with_radiology_target(monkeypatch, tmp_path):
    case = build_specialty_case_input(RUN_DIR, MdtSpecialty.THORACIC_RADIOLOGY)
    result = minimal_assessment(case)
    calls = []

    def fake_build(run_dir, specialty):
        calls.append((run_dir, specialty))
        return case

    class FakeAgent:
        @classmethod
        def from_config(cls, config_path, llm, **kwargs):
            return cls()

        def initial_assessment(self, case_input):
            assert case_input == case
            return result, {"schema_version": "thoracic_radiology.v1", "stages": []}

    monkeypatch.setattr(runner, "choose_phase", lambda: "initial")
    monkeypatch.setattr(runner, "discover_semantic_run_dirs", lambda: [tmp_path])
    monkeypatch.setattr(runner, "choose_file", lambda paths, title, optional=False: paths[0])
    monkeypatch.setattr(runner, "build_specialty_case_input", fake_build)
    monkeypatch.setattr(runner, "build_llm_client", lambda config: object())
    monkeypatch.setattr(runner, "ThoracicRadiologyAgent", FakeAgent)
    monkeypatch.setattr(runner, "load_env_file", lambda: None)

    assert runner.main() == 0
    assert calls == [(tmp_path, MdtSpecialty.THORACIC_RADIOLOGY)]
    assert (tmp_path / "76-IPF_thoracic_radiology_input.json").exists()
    assert (tmp_path / "76-IPF_thoracic_radiology_initial.json").exists()
    assert (tmp_path / "76-IPF_thoracic_radiology_initial_trace.json").exists()
    report = tmp_path / "76-IPF_thoracic_radiology_initial.html"
    assert report.exists()
    assert "七问处理状态" in report.read_text(encoding="utf-8")
