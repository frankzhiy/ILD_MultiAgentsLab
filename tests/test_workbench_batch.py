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


def test_restart_marks_only_unfinished_cases_interrupted(tmp_path):
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
    recovered = RunOrchestrator(tmp_path, catalog, events).batch("restart-batch")
    assert recovered["status"] == "interrupted"
    assert [item["status"] for item in recovered["items"]] == ["completed", "interrupted", "interrupted"]
    assert catalog.run_summary(catalog.run_dir("case-b"))["status"] == "interrupted"
    assert catalog.run_summary(catalog.run_dir("case-c"))["status"] == "interrupted"
