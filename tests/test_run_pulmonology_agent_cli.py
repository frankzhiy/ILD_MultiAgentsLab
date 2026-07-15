from pathlib import Path

import pytest

from scripts.agent_input.prepare_specialty_input import build_specialty_case_input
import scripts.run.run_pulmonology_agent as runner
from scripts.run.run_pulmonology_agent import (
    choose_file,
    choose_phase,
    discover_semantic_run_dirs,
    write_failure_trace,
)
from src.agents.pulmonology.models import (
    DomainReview,
    PulmonologyInitialAssessment,
    PulmonologyDomain,
)
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.llm.structured import StructuredGenerationError


RUN_DIR = Path("outputs/runs/20260714_163246_76-IPF_step2_step3")


def test_cli_selects_phase_by_number(monkeypatch):
    answers = iter(["wrong", "2"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    assert choose_phase() == "discussion"


def test_cli_selects_file_by_number(monkeypatch):
    paths = [Path("first.json"), Path("second.json")]
    monkeypatch.setattr("builtins.input", lambda _: "2")

    assert choose_file(paths, "测试文件") == paths[1]


def test_cli_can_skip_optional_file(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "0")

    assert choose_file([Path("optional.json")], "可选文件", optional=True) is None


def test_cli_discovers_semantic_graphing_run_directory():
    assert RUN_DIR.resolve() in [path.resolve() for path in discover_semantic_run_dirs()]


def test_failed_generation_trace_is_written_with_raw_attempts(tmp_path):
    error = StructuredGenerationError(
        "validation failed",
        attempts=[{"attempt": 1, "content": "raw invalid response"}],
        stage="initial_pulmonary_assessment",
    )

    path = write_failure_trace(tmp_path, "76-IPF_pulmonology", "initial", error)

    data = runner.read_json(path)
    assert path.name == "76-IPF_pulmonology_initial_failure_trace.json"
    assert data["failed_stage"] == "initial_pulmonary_assessment"
    assert data["attempts"][0]["content"] == "raw invalid response"


def test_main_builds_input_with_prepare_specialty_input_before_running_agent(
    monkeypatch,
    tmp_path,
    capsys,
):
    case = build_specialty_case_input(RUN_DIR, MdtSpecialty.PULMONOLOGY)
    result = PulmonologyInitialAssessment(
        case_id=case.case_id,
        domain_reviews=[
            DomainReview(
                domain=domain,
                status="not_assessable",
                rationale="测试病例当前不可评价。",
            )
            for domain in PulmonologyDomain
        ],
    )
    calls = []

    def fake_build(run_dir, specialty):
        calls.append((run_dir, specialty))
        return case

    class FakeAgent:
        @classmethod
        def from_config(cls, config_path, llm):
            return cls()

        def initial_assessment(self, case_input):
            assert case_input == case
            return result, {"attempts": []}

    monkeypatch.setattr(runner, "choose_phase", lambda: "initial")
    monkeypatch.setattr(runner, "discover_semantic_run_dirs", lambda: [tmp_path])
    monkeypatch.setattr(runner, "choose_file", lambda paths, title, optional=False: paths[0])
    monkeypatch.setattr(runner, "build_specialty_case_input", fake_build)
    monkeypatch.setattr(runner, "build_llm_client", lambda config: object())
    monkeypatch.setattr(runner, "PulmonologyAgent", FakeAgent)
    monkeypatch.setattr(runner, "load_env_file", lambda: None)

    assert runner.main() == 0
    assert calls == [(tmp_path, MdtSpecialty.PULMONOLOGY)]
    assert (tmp_path / "76-IPF_pulmonology_input.json").exists()
    assert (tmp_path / "76-IPF_pulmonology_initial.json").exists()
    assert (tmp_path / "76-IPF_pulmonology_initial_trace.json").exists()
    report_path = tmp_path / "76-IPF_pulmonology_initial.html"
    assert report_path.exists()
    assert "八问处理状态" in report_path.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "准备并写入呼吸科输入" in output
    assert "HTML 报告" in output


def test_main_saves_failed_stage_trace_before_reraising(monkeypatch, tmp_path):
    case = build_specialty_case_input(RUN_DIR, MdtSpecialty.PULMONOLOGY)

    class FailingAgent:
        @classmethod
        def from_config(cls, config_path, llm):
            return cls()

        def initial_assessment(self, case_input):
            raise StructuredGenerationError(
                "invalid evidence",
                attempts=[{"attempt": 1, "content": "bad clinical JSON"}],
                stage="initial_pulmonary_assessment",
            )

    monkeypatch.setattr(runner, "choose_phase", lambda: "initial")
    monkeypatch.setattr(runner, "discover_semantic_run_dirs", lambda: [tmp_path])
    monkeypatch.setattr(runner, "choose_file", lambda paths, title, optional=False: paths[0])
    monkeypatch.setattr(runner, "build_specialty_case_input", lambda *_: case)
    monkeypatch.setattr(runner, "build_llm_client", lambda config: object())
    monkeypatch.setattr(runner, "PulmonologyAgent", FailingAgent)
    monkeypatch.setattr(runner, "load_env_file", lambda: None)

    with pytest.raises(StructuredGenerationError, match="invalid evidence"):
        runner.main()

    trace = runner.read_json(tmp_path / "76-IPF_pulmonology_initial_failure_trace.json")
    assert trace["failed_stage"] == "initial_pulmonary_assessment"
    assert trace["attempts"][0]["content"] == "bad clinical JSON"
