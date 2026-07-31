import asyncio
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import src.workbench.workflow as workflow_module
from src.agents.pulmonology.agent import PulmonologyAgent
from src.workbench.catalog import RunCatalog, SPECIALTIES
from src.workbench.events import EventStore
from src.workbench.runner import AGENTS, RunOrchestrator
from src.workbench.workflow import WorkbenchWorkflow


FORMAL_OUTPUT = {
    "specialty_assessments": {"specialty_question": "呼吸科首轮问题"},
    "interspecialty_questions": {"questions": []},
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
            "schema_version": "mdt_chair.v6",
            "integrated_conclusions": [],
            "assessment_boundaries": [],
            "conflicts": [],
            "questions": [],
            "evidence_needs": [],
        },
    )
    assert catalog.chair("run-1")["status"] == "outdated"
    assert catalog.run_summary(run_dir)["chair_complete"] is False

    write_json(
        run_dir / "case-1_mdt_chair_integration.json",
        {
            "schema_version": "mdt_chair.v9",
            "integrated_conclusions": [],
            "assessment_boundaries": [],
            "conflicts": [],
            "questions": [],
            "evidence_needs": [],
        },
    )
    assert catalog.chair("run-1")["status"] == "completed"
    assert catalog.run_summary(run_dir)["chair_complete"] is True

    baseline_path = run_dir / "case-1_mdt_chair_integration.json"
    write_json(run_dir / ".workbench_run.json", {"case_id": "case-1", "status": "failed"})
    write_json(
        run_dir / "case-1_mdt_chair_cross_specialty_integration_failure_trace.json",
        {"error": "first chair attempt failed"},
    )
    write_json(
        run_dir / "case-1_workbench_failure_trace.json",
        {"error": "first run attempt failed"},
    )
    write_json(run_dir / "case-1_mdt_chair_integration.json", json.loads(baseline_path.read_text()))
    assert catalog.run_summary(run_dir)["status"] == "completed"
    assert not any(item["current"] for item in catalog.errors("run-1"))

    write_json(
        run_dir / "case-1_mdt_discussion_state.json",
        {
            "schema_version": "mdt_discussion.v2",
            "case_id": "case-1",
            "baseline_sha256": sha256(baseline_path.read_bytes()).hexdigest(),
            "status": "running",
            "max_rounds": 3,
            "rounds": [],
            "active_round": {
                "round_number": 1,
                "status": "running",
                "tasks": [{"task_id": "R01-Q001-pulmonology"}],
                "task_progress": {"R01-Q001-pulmonology": {"status": "running"}},
                "chair_status": "waiting",
            },
            "report_status": "waiting",
            "latest_chair_result": {},
        },
    )
    discussion = catalog.discussion("run-1")
    assert discussion["status"] == "running"
    assert discussion["current_round"] == 1
    assert discussion["active_round"]["task_progress"]["R01-Q001-pulmonology"]["status"] == "running"

    write_json(
        run_dir / "case-1_mdt_discussion_team_discussion_failure_trace.json",
        {"error": "discussion failed"},
    )
    write_json(
        run_dir / "case-1_mdt_discussion_state.json",
        {
            **json.loads((run_dir / "case-1_mdt_discussion_state.json").read_text()),
            "status": "failed",
            "error": "discussion failed",
        },
    )
    summary = catalog.run_summary(run_dir)
    assert summary["status"] == "failed"
    assert summary["status_source"] == "mdt_discussion"
    assert [item["agent_id"] for item in catalog.errors("run-1") if item["current"]] == [
        "mdt_discussion"
    ]


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


def test_successful_manual_chair_rerun_updates_failed_manifest(monkeypatch, tmp_path):
    run_dir = tmp_path / "outputs/runs/run-1"
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / ".workbench_run.json"
    write_json(manifest_path, {"case_id": "case-1", "status": "failed", "error": "old"})
    orchestrator = RunOrchestrator(
        tmp_path, RunCatalog(tmp_path), EventStore(tmp_path / "events.sqlite3")
    )

    async def successful_stage(*_args):
        return None

    monkeypatch.setattr(orchestrator, "_stage", successful_stage)
    asyncio.run(orchestrator._execute_chair("run-1", run_dir, tmp_path / "chair.yaml"))

    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "completed"
    assert manifest["error"] is None


def test_manual_discussion_updates_overall_manifest(monkeypatch, tmp_path):
    run_dir = tmp_path / "outputs/runs/run-1"
    run_dir.mkdir(parents=True)
    manifest_path = run_dir / ".workbench_run.json"
    write_json(manifest_path, {"case_id": "case-1", "status": "completed"})
    orchestrator = RunOrchestrator(
        tmp_path, RunCatalog(tmp_path), EventStore(tmp_path / "events.sqlite3")
    )

    async def failed_stage(*_args):
        raise RuntimeError("discussion failed")

    monkeypatch.setattr(orchestrator, "_stage", failed_stage)
    asyncio.run(orchestrator._execute_discussion("run-1", run_dir, {}))
    failed_manifest = json.loads(manifest_path.read_text())
    assert failed_manifest["status"] == "failed"
    assert failed_manifest["status_source"] == "mdt_discussion"
    assert orchestrator.catalog.run_summary(run_dir)["status"] == "failed"

    async def successful_stage(*_args):
        return None

    monkeypatch.setattr(orchestrator, "_stage", successful_stage)
    asyncio.run(orchestrator._execute_discussion("run-1", run_dir, {}))
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "completed"
    assert manifest["status_source"] == "mdt_discussion"
    assert manifest["error"] is None
    assert orchestrator.catalog.run_summary(run_dir)["status"] == "completed"
