import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import src.workbench.workflow as workflow_module
from src.agents.pulmonology.agent import PulmonologyAgent
from src.workbench.catalog import RunCatalog, SPECIALTIES
from src.workbench.events import EventStore
from src.workbench.runner import AGENTS, RunOrchestrator
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


def test_catalog_exposes_chair_readiness_after_four_specialties(tmp_path):
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
    chair = catalog.chair("run-1")

    assert summary["status"] == "completed"
    assert summary["chair_complete"] is False
    assert chair["status"] == "unavailable"
    assert "clinical propositions" in chair["error"]
    assert all(not item["legacy"] for item in results)
    assert all(set(item) == {"specialty", "label", "status", "input_summary", "output", "legacy"} for item in results)

    write_json(run_dir / "case-1_clinical_propositions.json", {"segments": []})
    write_json(run_dir / "case-1_local_graphs.json", {"segments": []})
    assert catalog.chair("run-1")["status"] == "pending"
    assert catalog.chair("run-1")["runnable"] is True

    write_json(
        run_dir / "case-1_mdt_chair_integration.json",
        {
            "schema_version": "mdt_chair.v4",
            "integrated_conclusions": [
                {
                    "statement": "综合结论",
                    "medical_basis": "依据",
                    "decision_impact": "影响",
                    "evidence": {},
                    "guideline_evidence": [],
                }
            ],
            "conflicts": [],
        },
    )
    assert catalog.chair("run-1")["status"] == "completed"
    assert catalog.run_summary(run_dir)["chair_complete"] is True


def test_web_orchestrator_and_workflow_include_chair_without_html_reporting():
    source = Path(workflow_module.__file__).read_text(encoding="utf-8")

    assert "mdt_chair" in AGENTS
    assert hasattr(WorkbenchWorkflow, "run_chair")
    assert "scripts.run" not in source
    assert "src.reporting" not in source
    assert ".html" not in source


def test_full_run_executes_chair_after_all_specialties(monkeypatch, tmp_path):
    run_dir = tmp_path / "outputs/runs/run-1"
    run_dir.mkdir(parents=True)
    input_path = tmp_path / "case-1.txt"
    input_path.write_text("case", encoding="utf-8")
    write_json(
        run_dir / ".workbench_run.json",
        {
            "case_id": "case-1",
            "status": "queued",
            "configs": {agent: str(tmp_path / f"{agent}.yaml") for agent in AGENTS},
        },
    )
    orchestrator = RunOrchestrator(
        tmp_path, RunCatalog(tmp_path), EventStore(tmp_path / "events.sqlite3")
    )
    stages = []

    async def record_stage(_run_id, _run_dir, agent_id, stage, *_args):
        stages.append((agent_id, stage))

    monkeypatch.setattr(orchestrator, "_stage", record_stage)
    asyncio.run(orchestrator._execute("run-1", input_path))

    assert stages[-1] == ("mdt_chair", "cross_specialty_integration")
    assert {agent for agent, _stage in stages[-5:-1]} == set(SPECIALTIES)
