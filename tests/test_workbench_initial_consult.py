import json
from pathlib import Path
from types import SimpleNamespace

import src.workbench.workflow as workflow_module
from src.agents.pulmonology.agent import PulmonologyAgent
from src.workbench.catalog import RunCatalog, SPECIALTIES
from src.workbench.events import EventStore
from src.workbench.runner import AGENTS
from src.workbench.workflow import WorkbenchWorkflow


FORMAL_OUTPUT = {
    "professional_conclusions": {"specialty_focus": "呼吸科首轮问题"},
    "clinical_reasoning": {"problem_representation": "病例表征"},
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_web_specialty_run_writes_two_layers_and_no_html(monkeypatch, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    workflow = WorkbenchWorkflow(tmp_path, EventStore(tmp_path / "events.sqlite3"))
    consultation = SimpleNamespace(
        internal_state={"schema_version": "pulmonology.v2", "phase": "initial_assessment"},
        formal_output=FORMAL_OUTPUT,
        trace={"stages": []},
    )

    monkeypatch.setattr(
        "src.workbench.workflow.build_specialty_case_input",
        lambda *_args, **_kwargs: {"case_id": "case-1"},
    )
    monkeypatch.setattr("src.workbench.workflow.load_yaml", lambda _path: {})
    monkeypatch.setattr("src.workbench.workflow.build_llm_client", lambda _config: object())
    monkeypatch.setattr(
        "src.agents.common.evidence_projection.build_specialty_working_input",
        lambda _case: {},
    )
    monkeypatch.setattr(
        "src.agents.common.evidence_projection.build_specialty_evidence_prompt_input",
        lambda _case: {},
    )
    monkeypatch.setattr(
        PulmonologyAgent,
        "from_config",
        classmethod(lambda _cls, *_args, **_kwargs: SimpleNamespace(
            initial_consult=lambda _case: consultation
        )),
    )

    workflow.run_specialty(
        "run-1", run_dir, "case-1", "pulmonology", tmp_path / "agent.yaml"
    )

    assert json.loads((run_dir / "case-1_pulmonology_internal_state.json").read_text()) == consultation.internal_state
    assert json.loads((run_dir / "case-1_pulmonology_initial.json").read_text()) == FORMAL_OUTPUT
    assert json.loads((run_dir / "case-1_pulmonology_initial_trace.json").read_text()) == consultation.trace
    assert not list(run_dir.glob("*.html"))


def test_catalog_completes_after_four_specialties_and_hides_internal_artifacts(tmp_path):
    run_dir = tmp_path / "outputs" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    write_json(run_dir / ".workbench_run.json", {"case_id": "case-1"})
    for specialty in SPECIALTIES:
        write_json(run_dir / f"case-1_{specialty}_initial.json", FORMAL_OUTPUT)
        write_json(run_dir / f"case-1_{specialty}_internal_state.json", {"private": True})
        write_json(run_dir / f"case-1_{specialty}_initial_trace.json", {"private": True})
        write_json(run_dir / f"case-1_{specialty}_input.json", {"summary": {}})

    catalog = RunCatalog(tmp_path)
    summary = catalog.run_summary(run_dir)
    results = catalog.specialties("run-1")["results"]

    assert summary["status"] == "completed"
    assert "chair_complete" not in summary
    assert all(not item["legacy"] for item in results)
    assert all(set(item) == {"specialty", "label", "status", "input_summary", "output", "legacy"} for item in results)


def test_web_orchestrator_and_workflow_have_no_chair_or_reporting_stage():
    source = Path(workflow_module.__file__).read_text(encoding="utf-8")

    assert "mdt_chair" not in AGENTS
    assert not hasattr(WorkbenchWorkflow, "run_chair")
    assert "scripts.run" not in source
    assert "src.reporting" not in source
    assert ".html" not in source
