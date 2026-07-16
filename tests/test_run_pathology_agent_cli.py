import scripts.run.run_pathology_agent as runner
from src.agents.pathology.models import (
    PathologyDomain,
    PathologyFormulation,
    PathologyInitialAssessment,
    SourceAssessment,
)
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from tests.test_pathology_agent import case_input, review


def test_cli_writes_pathology_input_and_outputs(monkeypatch, tmp_path):
    case = case_input()
    result = PathologyInitialAssessment(
        case_id=case.case_id,
        domain_reviews=[review(domain, "not_assessable") for domain in PathologyDomain],
        source_assessment=SourceAssessment(
            assessment="当前输入未提供可评价病理材料。",
            confidence="high",
            reasoning_summary="输入可评价性说明。",
            material_status="no_pathology_material",
            review_basis="no_material",
        ),
        pathology_formulation=PathologyFormulation(
            classification_status="no_pathology_material",
            formulation="当前无病理材料可形成组织学模式。",
            confidence="unknown",
            reasoning_summary="不能从缺失资料推断模式。",
        ),
    )
    calls = []

    class FakeAgent:
        @classmethod
        def from_config(cls, config_path, llm, **kwargs):
            return cls()

        def initial_assessment(self, case_input):
            return result, {"schema_version": "pathology.v1", "stages": []}

    monkeypatch.setattr(runner, "choose", lambda options, title, optional=False: tmp_path)
    monkeypatch.setattr("builtins.input", lambda _: "1")
    monkeypatch.setattr(
        runner,
        "build_specialty_case_input",
        lambda run_dir, specialty: calls.append((run_dir, specialty)) or case,
    )
    monkeypatch.setattr(runner, "build_llm_client", lambda config: object())
    monkeypatch.setattr(runner, "PathologyAgent", FakeAgent)
    monkeypatch.setattr(runner, "load_env_file", lambda: None)

    assert runner.main() == 0
    assert calls == [(tmp_path, MdtSpecialty.PATHOLOGY)]
    for suffix in (
        "input.json",
        "working_input.json",
        "evidence_input.json",
        "initial.json",
        "initial_trace.json",
        "initial.html",
    ):
        assert (tmp_path / f"pathology-case_pathology_{suffix}").exists()


def test_progress_reporter_labels_pathology_stage(capsys):
    reporter = runner.ProgressReporter()
    reporter.generation_event(
        "stage_completed",
        {
            "stage": "initial_morphologic_assessment",
            "duration_seconds": 2.0,
            "llm_duration_seconds": 1.7,
            "validation_duration_seconds": 0.2,
        },
    )
    output = capsys.readouterr().out
    assert "首轮 2/3：组织形态评估" in output
    assert "LLM 1.7s" in output
