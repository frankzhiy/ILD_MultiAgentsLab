from pathlib import Path

import scripts.run.run_rheumatology_agent as runner
from scripts.agent_input.prepare_specialty_input import build_specialty_case_input
from src.agents.rheumatology.models import DomainReview, RheumatologyInitialAssessment, RheumatologyDomain
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty


RUN_DIR = Path("outputs/runs/20260714_163246_76-IPF_step2_step3")


def test_cli_writes_rheumatology_input_and_outputs(monkeypatch, tmp_path):
    case = build_specialty_case_input(RUN_DIR, MdtSpecialty.RHEUMATOLOGY)
    result = RheumatologyInitialAssessment(
        case_id=case.case_id,
        domain_reviews=[DomainReview(domain=domain, status="not_assessable", rationale="测试病例当前不可评价。") for domain in RheumatologyDomain],
    )
    calls = []

    class FakeAgent:
        @classmethod
        def from_config(cls, config_path, llm, **kwargs):
            return cls()

        def initial_assessment(self, case_input):
            return result, {"stages": []}

    monkeypatch.setattr(runner, "choose", lambda options, title, optional=False: tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "1")
    monkeypatch.setattr(runner, "build_specialty_case_input", lambda run_dir, specialty: calls.append((run_dir, specialty)) or case)
    monkeypatch.setattr(runner, "build_llm_client", lambda config: object())
    monkeypatch.setattr(runner, "RheumatologyAgent", FakeAgent)
    monkeypatch.setattr(runner, "load_env_file", lambda: None)

    assert runner.main() == 0
    assert calls == [(tmp_path, MdtSpecialty.RHEUMATOLOGY)]
    for suffix in ("input.json", "initial.json", "initial_trace.json", "initial.html"):
        assert (tmp_path / f"76-IPF_rheumatology_{suffix}").exists()


def test_cli_discovers_semantic_run_directory():
    assert RUN_DIR.resolve() in [path.resolve() for path in runner.discover_semantic_run_dirs()]


def test_cli_reports_llm_and_validation_time(capsys):
    reporter = runner.ProgressReporter()
    reporter.console = None
    reporter.generation_event(
        "stage_completed",
        {
            "stage": "initial_autoimmune_assessment",
            "duration_seconds": 12.5,
            "llm_duration_seconds": 12.2,
            "validation_duration_seconds": 0.1,
        },
    )

    output = capsys.readouterr().out
    assert "首轮 2/3：自身免疫诊断评估" in output
    assert "LLM 12.2s" in output
    assert "本地校验 0.1s" in output
