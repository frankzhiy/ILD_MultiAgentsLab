import asyncio
from pathlib import Path

from src.workbench.catalog import RunCatalog
from src.workbench.events import EventStore
from src.workbench.runner import RunOrchestrator, SAFE_CASE_ID


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
