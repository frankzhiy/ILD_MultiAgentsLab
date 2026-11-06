import asyncio
import json
from pathlib import Path
from threading import Event, Lock

import pytest
import yaml

from src.workbench.catalog import RunCatalog
from src.workbench.events import EventStore
from src.workbench.runner import AGENTS, RunOrchestrator, SAFE_CASE_ID


def test_library_case_id_with_internal_space_is_safe():
    assert SAFE_CASE_ID.fullmatch("81- IPF")
    assert not SAFE_CASE_ID.fullmatch("../escape")


def test_batch_limits_case_concurrency_and_keeps_other_cases_running(monkeypatch, tmp_path):
    cases_dir = tmp_path / "data/raw_cases"
    cases_dir.mkdir(parents=True)
    for case_id in ("case-a", "case-b", "case-c"):
        (cases_dir / f"{case_id}.txt").write_text("case", encoding="utf-8")
    orchestrator = RunOrchestrator(
        tmp_path, RunCatalog(tmp_path), EventStore(tmp_path / "events.sqlite3")
    )
    monkeypatch.setattr(
        orchestrator,
        "prepare",
        lambda request: (f"run-{request['case_id']}", Path(f"/{request['case_id']}.txt")),
    )
    active = 0
    maximum = 0

    def start(_run_id, _input_path):
        async def complete():
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0)
            active -= 1

        return asyncio.create_task(complete())

    monkeypatch.setattr(orchestrator, "start", start)
    monkeypatch.setattr(
        orchestrator.catalog,
        "run_summary",
        lambda run_dir: {
            "discussion_complete": run_dir.name != "run-case-b",
            "manifest": {"error": "specialty failed"},
        },
    )
    monkeypatch.setattr(orchestrator.catalog, "run_dir", Path)

    async def exercise():
        batch = orchestrator.create_batch(
            {
                "kind": "run",
                "case_ids": ["case-a", "case-b", "case-c"],
                "max_concurrency": 1,
                "max_case_concurrency": 2,
                "max_request_concurrency": 3,
            }
        )
        await orchestrator.batch_tasks[batch["id"]]
        return orchestrator.batch(batch["id"])

    batch = asyncio.run(exercise())

    assert maximum == 2
    assert [item["status"] for item in batch["items"]] == ["completed", "failed", "completed"]
    assert batch["summary"]["failed"] == 1
    assert batch["status"] == "completed_with_errors"


def test_stopped_queued_case_is_skipped_without_affecting_other_cases(monkeypatch, tmp_path):
    import json
    cases = tmp_path / 'data/raw_cases'
    cases.mkdir(parents=True)
    for case in ('case-a', 'case-b'):
        (cases / f'{case}.txt').write_text('case')
        directory = tmp_path / f'outputs/runs/{case}'
        directory.mkdir(parents=True)
        (directory / '.workbench_run.json').write_text(json.dumps({'case_id': case, 'status': 'queued'}))
    runner = RunOrchestrator(tmp_path, RunCatalog(tmp_path), EventStore(tmp_path / 'events.sqlite3'))
    monkeypatch.setattr(runner, 'prepare', lambda request: (request['case_id'], cases / f"{request['case_id']}.txt"))
    entered, release = asyncio.Event(), asyncio.Event()
    started = []
    async def start(run_id, _input):
        started.append(run_id)
        entered.set()
        await release.wait()
    monkeypatch.setattr(runner, 'start', start)
    monkeypatch.setattr(runner.catalog, 'run_summary', lambda _: {'status': 'completed', 'discussion_complete': True})
    async def exercise():
        batch = runner.create_batch({'kind': 'run', 'case_ids': ['case-a', 'case-b'],
                                     'max_case_concurrency': 1, 'max_request_concurrency': 1})
        await entered.wait()
        runner.stop_run('case-b')
        release.set()
        await runner.batch_tasks[batch['id']]
        return runner.batch(batch['id'])
    batch = asyncio.run(exercise())
    assert started == ['case-a']
    assert [item['status'] for item in batch['items']] == ['completed', 'stopped']
    assert batch['summary']['stopped'] == 1


def test_real_batch_flow_isolates_failure_and_retries_original_snapshots(monkeypatch, tmp_path):
    for agent in AGENTS:
        directory = tmp_path / "configs/agents" / agent
        directory.mkdir(parents=True)
        (directory / "agent.yaml").write_text("model: default-model\n", encoding="utf-8")
    cases = tmp_path / "data/raw_cases"
    cases.mkdir(parents=True)
    for case in ("case-a", "case-b", "case-c"):
        (cases / f"{case}.txt").write_text(f"original {case}", encoding="utf-8")
    runner = RunOrchestrator(tmp_path, RunCatalog(tmp_path), EventStore(tmp_path / "events.sqlite3"))
    entered, release, lock = Event(), Event(), Lock()
    active = maximum = 0
    attempts = []

    def semantic(_run, _directory, input_path, case, config_path):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            attempts.append(case)
            if active == 2:
                entered.set()
        try:
            assert release.wait(5)
            assert input_path.read_text() == f"original {case}"
            assert yaml.safe_load(config_path.read_text())["model"] == "semantic-model"
            if case == "case-b" and attempts.count(case) == 1:
                raise RuntimeError("simulated case failure")
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(runner.workflow, "run_semantic", semantic)
    for stage in ("run_specialty", "run_chair", "run_discussion"):
        monkeypatch.setattr(runner.workflow, stage, lambda *_args: None)
    monkeypatch.setattr(runner.workflow, "run_report", lambda _run, directory, case, _config:
                        (directory / f"{case}_mdt_final_report.json").write_text('{"result": "done"}'))
    monkeypatch.setattr(runner.catalog, "run_summary", lambda directory: {
        "discussion_complete": (directory / f"{runner.catalog.case_id(directory)}_mdt_final_report.json").exists(),
        "manifest": json.loads((directory / ".workbench_run.json").read_text()),
    })

    async def exercise():
        batch = runner.create_batch({"kind": "run", "case_ids": ["case-a", "case-b", "case-c"],
                                     "agents": {"semantic_graphing": {"model": "semantic-model"},
                                                "pulmonology": {"model": "specialty-model"}},
                                     "max_case_concurrency": 2, "max_request_concurrency": 3})
        task = runner.batch_tasks[batch["id"]]
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            running = runner.batch(batch["id"])
            assert running["summary"]["running"] == 2
            assert running["summary"]["queued"] == 1
            assert running["items"][0]["progress"]["stage"] == "semantic_graphing"
            with pytest.raises(ValueError, match="等待"):
                runner.retry_batch(batch["id"])
        finally:
            release.set()
            await task
        finished = runner.batch(batch["id"])
        assert finished["status"] == "completed_with_errors"
        assert [item["status"] for item in finished["items"]] == ["completed", "failed", "completed"]
        assert finished["items"][1]["error"] == "simulated case failure"
        assert finished["items"][1]["progress"]["stage"] == "semantic_graphing"
        assert finished["agents"]["pulmonology"]["model"] == "specialty-model"
        for source in cases.glob("*.txt"):
            source.unlink()
        for config in (tmp_path / "configs/agents").glob("*/agent.yaml"):
            config.write_text("model: changed-default\n")
        retry = runner.retry_batch(batch["id"])
        await runner.batch_tasks[retry["id"]]
        retry = runner.batch(retry["id"])
        assert retry["status"] == "completed"
        assert retry["retry_of"] == batch["id"]
        assert [item["case_id"] for item in retry["items"]] == ["case-b"]
        assert retry["items"][0]["run_id"] != finished["items"][1]["run_id"]
        assert retry["agents"] == finished["agents"]
        assert runner.batch(batch["id"]) == finished
        # Reopening uses persisted records, not live tasks or browser state.
        reopened = RunOrchestrator(tmp_path, runner.catalog, runner.events)
        assert reopened.batch(retry["id"]) == retry

    asyncio.run(exercise())
    assert maximum == 2
    assert sorted(attempts[:3]) == ["case-a", "case-b", "case-c"]
    assert attempts[3:] == ["case-b"]


@pytest.mark.parametrize(("failure_stage", "changed_prompt"), [
    ("pathology", False), ("pathology", True), ("report", False),
])
def test_retry_reuses_compatible_stages(monkeypatch, tmp_path, failure_stage, changed_prompt):
    from hashlib import sha256
    from shutil import copyfile
    from src.agents.common.specialty_input import SEMANTIC_OUTPUT_MODELS
    from synthetic_case import write_run
    from test_expert_protocol import run_expert, setup_case
    from src.agents.common.decision_state import project_active_specialty_outputs
    from src.agents.mdt_discussion.models import MDTDiscussionState
    from src.utils.persistence import write_json

    case = "synthetic-case"
    semantic = write_run(tmp_path / "fixture")
    cases = tmp_path / "data/raw_cases"
    cases.mkdir(parents=True)
    copyfile(semantic / f"{case}_input.txt", cases / f"{case}.txt")
    prompt = tmp_path / "src/prompts/pulmonology/initial_reasoning_output.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("original prompt")
    for agent in AGENTS:
        directory = tmp_path / "configs/agents" / agent
        directory.mkdir(parents=True)
        config = {"initial_reasoning_output_prompt": str(prompt)} if agent == "pulmonology" else {}
        (directory / "agent.yaml").write_text(yaml.safe_dump(config))
    runner = RunOrchestrator(tmp_path, RunCatalog(tmp_path), EventStore(tmp_path / "events.sqlite3"))
    decision_state, bundle, payload = setup_case()
    outputs = project_active_specialty_outputs(decision_state)
    calls = []
    failed_once = False

    def run_semantic(_run_id, directory, _input, _case, _config):
        calls.append("semantic_graphing")
        for suffix in SEMANTIC_OUTPUT_MODELS:
            name = f"{case}_{suffix}.json"
            copyfile(semantic / name, directory / name)

    def run_specialty(_run_id, directory, _case, specialty, _config):
        nonlocal failed_once
        calls.append(specialty)
        if specialty == failure_stage and not failed_once:
            failed_once = True
            raise RuntimeError("simulated pathology failure")
        output = outputs[specialty]
        for assessment in output["specialty_assessments"]["assessments"]:
            for field in ("judgment_id", "version_id", "previous_version_id", "origin"):
                assessment.pop(field, None)
        write_json(directory / f"{case}_{specialty}_initial.json", output)

    chair = run_expert(bundle, [payload])
    chair.case_id = case
    chair_name = f"{case}_mdt_chair_integration.json"

    def run_chair(_run_id, directory, *_):
        calls.append("chair")
        write_json(directory / chair_name, chair)

    def run_discussion(_run_id, directory, *_):
        calls.append("discussion")
        write_json(directory / f"{case}_mdt_discussion_state.json", MDTDiscussionState(
            case_id=case, baseline_sha256=sha256((directory / chair_name).read_bytes()).hexdigest(),
            status="completed", latest_chair_result=chair.model_dump(mode="json"),
            decision_state=decision_state,
        ))

    def run_report(_run_id, directory, *_):
        nonlocal failed_once
        calls.append("report")
        if failure_stage == "report" and not failed_once:
            failed_once = True
            raise RuntimeError("simulated report failure")
        (directory / "report").touch()

    monkeypatch.setattr(runner.workflow, "run_semantic", run_semantic)
    monkeypatch.setattr(runner.workflow, "run_specialty", run_specialty)
    monkeypatch.setattr(runner.workflow, "run_chair", run_chair)
    monkeypatch.setattr(runner.workflow, "run_discussion", run_discussion)
    monkeypatch.setattr(runner.workflow, "run_report", run_report)
    monkeypatch.setattr(runner.catalog, "run_summary", lambda directory: {
        "discussion_complete": (directory / "report").exists(),
        "manifest": json.loads((directory / ".workbench_run.json").read_text()),
    })

    async def exercise():
        batch = runner.create_batch({"kind": "run", "case_ids": [case],
                                     "max_case_concurrency": 1, "max_request_concurrency": 1})
        await runner.batch_tasks[batch["id"]]
        if changed_prompt:
            prompt.write_text("revised prompt")
        calls.clear()
        retry = runner.retry_batch(batch["id"])
        await runner.batch_tasks[retry["id"]]
        return runner.batch(retry["id"])

    retry = asyncio.run(exercise())
    assert retry["status"] == "completed", retry["items"][0]["error"]
    expected = {"report"}
    if failure_stage == "pathology":
        expected.update({"pathology", "chair", "discussion"})
    if changed_prompt:
        expected.add("pulmonology")
    assert set(calls) == expected
    assert set(retry["items"][0]["progress"]["completed_specialties"]) == set(outputs)


def test_batch_retry_resumes_only_missing_semantic_unit(monkeypatch, tmp_path):
    from shutil import copyfile
    from types import SimpleNamespace

    from src.agents.common.specialty_input import SEMANTIC_OUTPUT_MODELS
    from src.agents.semantic_graphing.agent import SemanticGraphingAgent
    from src.utils.persistence import write_json
    from synthetic_case import write_run

    case = "synthetic-case"
    fixture = write_run(tmp_path / "fixture")
    documents = {
        suffix: model.model_validate_json((fixture / f"{case}_{suffix}.json").read_bytes())
        for suffix, model in SEMANTIC_OUTPUT_MODELS.items()
    }
    cases = tmp_path / "data/raw_cases"
    cases.mkdir(parents=True)
    copyfile(fixture / f"{case}_input.txt", cases / f"{case}.txt")
    for name in AGENTS:
        config = tmp_path / f"configs/agents/{name}/agent.yaml"
        config.parent.mkdir(parents=True)
        config.write_text("{}")
    runner = RunOrchestrator(tmp_path, RunCatalog(tmp_path), EventStore(tmp_path / "events.sqlite3"))
    units = {unit.graph_unit_id: unit for segment in documents["clinical_propositions"].segments for unit in segment.units}
    missing = next(iter(units))
    extracted = []

    def extract_unit(unit, *_):
        extracted.append(unit.graph_unit_id)
        return units[unit.graph_unit_id], {}

    component = SimpleNamespace(generator=SimpleNamespace())
    agent = SemanticGraphingAgent(
        classifier=component, graph_unit_extractor=component, primary_frame_selector=component,
        clinical_proposition_extractor=SimpleNamespace(generator=SimpleNamespace(), extract_unit=extract_unit),
    )
    monkeypatch.setattr(agent, "validate_clinical_propositions", lambda *_, **__: documents["proposition_validation"])
    monkeypatch.setattr(agent, "build_local_graphs", lambda *_, **__: documents["local_graphs"])
    monkeypatch.setattr(SemanticGraphingAgent, "from_config", lambda *_: agent)
    monkeypatch.setattr("src.workbench.workflow.build_llm_client", lambda _: None)

    def interrupted(_run, directory, *_):
        for suffix in ("discourse_segments", "graph_units", "primary_frames"):
            write_json(directory / f"{case}_{suffix}.json", documents[suffix])
        cache = directory / "task_cache/clinical_propositions"
        cache.mkdir(parents=True)
        for unit_id, result in units.items():
            if unit_id != missing:
                write_json(cache / f"{unit_id}.json", {"result": result.model_dump(mode="json")})
        (cache / f"{missing}.json").write_text('{"result":')
        raise RuntimeError("simulated interrupted write")

    run_semantic = runner.workflow.run_semantic
    monkeypatch.setattr(runner.workflow, "run_semantic", interrupted)
    for stage in ("run_specialty", "run_chair", "run_discussion", "run_report"):
        monkeypatch.setattr(runner.workflow, stage, lambda *_: None)
    monkeypatch.setattr(runner.catalog, "run_summary", lambda directory: {
        "discussion_complete": (directory / f"{case}_local_graphs.json").exists(),
        "manifest": json.loads((directory / ".workbench_run.json").read_text()),
    })

    async def exercise():
        batch = runner.create_batch({"kind": "run", "case_ids": [case],
                                     "max_case_concurrency": 1, "max_request_concurrency": 1})
        await runner.batch_tasks[batch["id"]]
        monkeypatch.setattr(runner.workflow, "run_semantic", run_semantic)
        retry = runner.retry_batch(batch["id"])
        await runner.batch_tasks[retry["id"]]
        return runner.batch(retry["id"])

    retry = asyncio.run(exercise())
    assert retry["status"] == "completed", retry["items"][0]["error"]
    assert extracted == [missing]


def test_initialization_preserves_live_batches_and_startup_recovers_unfinished_cases(tmp_path, monkeypatch):
    catalog = RunCatalog(tmp_path)
    events = EventStore(tmp_path / "events.sqlite3")
    runner = RunOrchestrator(tmp_path, catalog, events)
    items = []
    for case, status in (("case-a", "completed"), ("case-b", "running"), ("case-c", "queued")):
        directory = catalog.runs_dir / case
        directory.mkdir(parents=True)
        runner._write_json(directory / ".workbench_run.json", {"case_id": case, "status": status})
        items.append({"case_id": case, "run_id": case, "status": status})
    runner._write_batch({"id": "restart-batch", "kind": "run", "status": "running", "items": items})
    reopened = RunOrchestrator(tmp_path, catalog, events)
    assert reopened.batch("restart-batch")["status"] == "running"
    assert catalog.run_summary(catalog.run_dir("case-b"))["status"] == "running"
    from fastapi.testclient import TestClient
    from src.workbench import app as api
    monkeypatch.setattr(api, "orchestrator", reopened)
    with TestClient(api.app) as client:
        recovered = client.get("/api/batches/restart-batch").json()
    assert recovered["status"] == "interrupted"
    assert [item["status"] for item in recovered["items"]] == ["completed", "interrupted", "interrupted"]
    assert catalog.run_summary(catalog.run_dir("case-b"))["status"] == "interrupted"
    assert catalog.run_summary(catalog.run_dir("case-c"))["status"] == "interrupted"
