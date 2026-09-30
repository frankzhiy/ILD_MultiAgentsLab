from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from threading import Event

import yaml

from src.utils.config import load_yaml
from src.llm.throttle import request_throttle
from src.workbench.catalog import RunCatalog, SPECIALTIES
from src.workbench.events import EventStore
from src.workbench.workflow import WorkbenchWorkflow, RunStopped


AGENTS = ("semantic_graphing", *SPECIALTIES, "mdt_chair")
SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,79}$")


def build_run_signature(config: dict[str, Any]) -> dict[str, Any]:
    prompt_hashes = {}
    for key, value in config.items():
        if (key == "prompt" or key.endswith("_prompt")) and isinstance(value, str):
            prompt_hashes[key] = hashlib.sha256(Path(value).read_bytes()).hexdigest()
    return {"config": config, "prompt_sha256": prompt_hashes}


class RunOrchestrator:
    def __init__(self, root: Path, catalog: RunCatalog, events: EventStore) -> None:
        self.root = root.resolve()
        self.catalog = catalog
        self.events = events
        self.workflow = WorkbenchWorkflow(self.root, events)
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.batch_tasks: dict[str, asyncio.Task[None]] = {}
        self.batches_dir = self.root / "outputs/batches"
        self.batches_dir.mkdir(parents=True, exist_ok=True)
        self.active_reports: set[str] = set()
        self.active_chairs: set[str] = set()
        self.active_discussions: set[str] = set()
        self._recover_batches()

    def prepare(self, request: dict[str, Any], *, retry_run_id: str | None = None) -> tuple[str, Path]:
        case_id = str(request.get("case_id") or "").strip()
        if not SAFE_CASE_ID.fullmatch(case_id):
            raise ValueError(
                "case_id 只能包含字母、数字、空格、点、下划线和连字符，且长度不超过 80。"
            )
        retry_dir = self.catalog.run_dir(retry_run_id) if retry_run_id else None
        retry_manifest = self._read_json(retry_dir / ".workbench_run.json") if retry_dir else {}
        if retry_dir and retry_manifest["case_id"] != case_id:
            raise ValueError("重跑必须使用同一病例的快照。")
        parent_run_id = request.get("parent_run_id")
        parent_version = None
        if parent_run_id:
            parent_dir = self.catalog.run_dir(parent_run_id)
            if self.catalog.case_id(parent_dir) != case_id:
                raise ValueError("后续资料必须关联同一病例的前序运行。")
            parent_snapshot = parent_dir / f"{case_id}_input.txt"
            parent_version = hashlib.sha256(parent_snapshot.read_bytes()).hexdigest()
            parent_manifest = self.catalog._json(parent_dir / ".workbench_run.json", {})
            if parent_manifest.get("case_version_id", parent_version) != parent_version:
                raise ValueError("前序病例快照已改变，不能建立版本关联。")
        source = request.get("source", "library")
        if source == "library":
            input_path = (retry_dir or self.catalog.cases_dir) / f"{case_id}{'_input' if retry_dir else ''}.txt"
            if not input_path.is_file():
                raise FileNotFoundError(f"病例不存在：{case_id}")
        elif source == "paste":
            raw_text = str(request.get("raw_text") or "").strip()
            if not raw_text:
                raise ValueError("粘贴病例原文不能为空。")
            input_path = self.root / "outputs/workbench_inputs" / case_id / f"{case_id}.txt"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(raw_text, encoding="utf-8")
        else:
            raise ValueError("source 必须是 library 或 paste。")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_id = f"{stamp}_{case_id}"
        run_id = base_id
        counter = 2
        while (self.catalog.runs_dir / run_id).exists():
            run_id = f"{base_id}_{counter}"
            counter += 1
        run_dir = self.catalog.runs_dir / run_id
        run_dir.mkdir(parents=True)
        snapshot = run_dir / f"{case_id}_input.txt"
        snapshot.write_bytes(input_path.read_bytes())
        input_path = snapshot
        case_version = hashlib.sha256(snapshot.read_bytes()).hexdigest()

        config_dir = run_dir / "workbench_config"
        config_dir.mkdir()
        overrides = request.get("agents") or {}
        config_paths: dict[str, str] = {}
        agent_settings = {}
        for agent_id in AGENTS:
            source_path = (Path(retry_manifest["configs"][agent_id]) if retry_dir
                           else self.root / "configs/agents" / agent_id / "agent.yaml")
            config = load_yaml(source_path)
            for key, value in list(config.items()):
                if (key == "prompt" or key.endswith("_prompt")) and isinstance(value, str):
                    config[key] = str((self.root / value).resolve())
            retrieval = config.get("guideline_retrieval")
            if isinstance(retrieval, dict) and isinstance(retrieval.get("directory"), str):
                retrieval["directory"] = str((self.root / retrieval["directory"]).resolve())
            override = overrides.get(agent_id) or {}
            if override.get("model"):
                config["model"] = str(override["model"])
            if override.get("reasoning_effort"):
                request_options = dict(config.get("request_options") or {})
                request_options["reasoning_effort"] = str(override["reasoning_effort"])
                config["request_options"] = request_options
            if agent_id == "semantic_graphing" and request.get("max_concurrency"):
                config["max_concurrency"] = int(request["max_concurrency"])
            target = config_dir / f"{agent_id}.yaml"
            target.write_text(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            config_paths[agent_id] = str(target)
            agent_settings[agent_id] = {
                "model": config.get("model"),
                "reasoning_effort": config.get("request_options", {}).get("reasoning_effort", "none"),
            }

        semantic_config = load_yaml(Path(config_paths["semantic_graphing"]))
        signature = build_run_signature(semantic_config)
        self._write_json(run_dir / f"{case_id}_run_signature.json", signature)
        self._write_json(run_dir / "protocol_manifest.json", {
            "protocol": "expert.v2",
            "patient_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "agents": {key: build_run_signature(load_yaml(path)) for key, path in config_paths.items()},
            "prompts": {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted((self.root / "src/prompts").rglob("*.md"))},
            "guidelines": {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in sorted((self.root / "data/guidelines").glob("*.pdf"))},
        })
        manifest = {
            "schema_version": "workbench.run.v1",
            "run_id": run_id,
            "case_id": case_id,
            "source": source,
            "case_version_id": case_version,
            "parent_run_id": parent_run_id,
            "parent_case_version_id": parent_version,
            "input_path": str(input_path.resolve()),
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "max_concurrency": int(request.get("max_concurrency") or 6),
            "configs": config_paths,
            "agent_overrides": overrides,
            "agent_settings": agent_settings,
        }
        self._write_json(run_dir / ".workbench_run.json", manifest)
        return run_id, input_path

    def start(self, run_id: str, input_path: Path) -> asyncio.Task[None]:
        return self._launch(run_id, input_path=input_path)

    def _launch(self, run_id: str, *, input_path: Path | None = None,
                start_stage: str = "semantic_graphing") -> asyncio.Task[None]:
        if self._busy(run_id):
            raise ValueError("该运行仍在执行，请等待结束。")
        self._update_manifest(self.catalog.run_dir(run_id) / ".workbench_run.json",
                              status="queued", status_source="run", status_updated_at=self._now(),
                              error=None, finished_at=None)
        self.workflow.stop_events[run_id] = Event()
        task = asyncio.create_task(self._execute(run_id, input_path, start_stage=start_stage),
                                   name=f"run:{run_id}")
        self.tasks[run_id] = task
        def finish(_):
            if self.tasks.get(run_id) is task:
                self.tasks.pop(run_id, None)
        task.add_done_callback(finish)
        return task

    def start_chair(self, run_id: str) -> asyncio.Task[None]:
        run_dir = self.catalog.run_dir(run_id)
        readiness = self.catalog.chair(run_id)
        if not readiness["runnable"]:
            raise ValueError(readiness["error"] or "四个专科正式输出尚未全部就绪。")
        if self._busy(run_id):
            raise ValueError("该运行仍在执行，不能重复启动主持人。")
        self._refresh_configs(run_dir, (*SPECIALTIES, "mdt_chair"))
        return self._launch(run_id, start_stage="mdt_chair")

    def chair_running(self, run_id: str) -> bool:
        return run_id in self.active_chairs

    def start_discussion(self, run_id: str) -> asyncio.Task[None]:
        run_dir = self.catalog.run_dir(run_id)
        readiness = self.catalog.discussion(run_id)
        if not readiness["runnable"]:
            raise ValueError(readiness["error"] or "讨论所需的既有产物尚未就绪。")
        if self._busy(run_id):
            raise ValueError("该运行仍在执行，不能重复启动团队讨论。")
        self._refresh_configs(run_dir, (*SPECIALTIES, "mdt_chair"))
        return self._launch(run_id, start_stage="mdt_discussion")

    def _busy(self, run_id: str) -> bool:
        task = self.tasks.get(run_id)
        return task is not None and not task.done()

    def stop_run(self, run_id: str) -> None:
        manifest_path = self.catalog.run_dir(run_id) / ".workbench_run.json"
        manifest = self._read_json(manifest_path)
        if self._busy(run_id):
            self.workflow.stop_events[run_id].set()
            self._update_manifest(manifest_path, status="stopping", status_source="run",
                                  status_updated_at=self._now())
            self.events.append(run_id, "run_stop_requested", {}, stage="run")
        elif manifest.get("status") == "queued":
            self._update_manifest(manifest_path, status="stopped", status_source="run",
                                  finished_at=self._now(), status_updated_at=self._now())
            self.events.append(run_id, "run_stopped", {}, stage="run")
        else:
            raise ValueError("当前没有正在执行或排队的病例流程。")

    def start_report(self, run_id: str) -> asyncio.Task[None]:
        readiness = self.catalog.report(run_id)
        if self._busy(run_id):
            raise ValueError("该病例仍有阶段在运行，请等待结束。")
        if not readiness["runnable"]:
            raise ValueError(readiness["error"] or "请先完成当前版本的团队讨论。")
        run_dir = self.catalog.run_dir(run_id)
        self._refresh_configs(run_dir, ("mdt_chair",))
        return self._launch(run_id, start_stage="mdt_report")

    def create_batch(self, request: dict[str, Any], *, retry_of: str | None = None) -> dict[str, Any]:
        kind = str(request.get("kind") or "")
        if kind not in {"run", "discussion"}:
            raise ValueError("批次类型必须是 run 或 discussion。")
        if kind == "run":
            item_ids = self._unique_ids(request.get("case_ids"), "病例")
            previous_items = {item["case_id"]: item for item in self._read_batch(retry_of)["items"]} if retry_of else {}
            for case_id in item_ids:
                if not SAFE_CASE_ID.fullmatch(case_id):
                    raise ValueError(f"病例 ID 不合法：{case_id}")
                input_path = (Path(previous_items[case_id]["input_path"]) if retry_of
                              else self.catalog.cases_dir / f"{case_id}.txt")
                if not input_path.is_file():
                    raise FileNotFoundError(f"病例不存在：{case_id}")
            items = []
            for case_id in item_ids:
                run_request = {**request, "source": "library", "case_id": case_id}
                run_id, input_path = (self.prepare(run_request, retry_run_id=previous_items[case_id]["run_id"])
                                      if retry_of else self.prepare(run_request))
                manifest = self.catalog._json(self.catalog.runs_dir / run_id / ".workbench_run.json", {})
                items.append(
                    {
                        "case_id": case_id,
                        "run_id": run_id,
                        "input_path": str(input_path),
                        "status": "queued",
                        "case_version_id": manifest.get("case_version_id"),
                    }
                )
        else:
            item_ids = self._unique_ids(request.get("run_ids"), "运行")
            items = []
            for run_id in item_ids:
                readiness = self.catalog.discussion(run_id)
                if not readiness["runnable"]:
                    raise ValueError(f"{run_id} 不满足团队讨论条件：{readiness['error']}")
                items.append(
                    {
                        "case_id": self.catalog.case_id(self.catalog.run_dir(run_id)),
                        "run_id": run_id,
                        "baseline_sha256": self._chair_sha256(run_id),
                        "status": "queued",
                    }
                )

        batch_id = self._new_batch_id(kind)
        batch = {
            "schema_version": "workbench.batch.v1",
            "id": batch_id,
            "kind": kind,
            "status": "queued",
            "created_at": self._now(),
            "updated_at": self._now(),
            "retry_of": retry_of,
            "agents": (manifest.get("agent_settings", request.get("agents", {})) if kind == "run" else {}),
            "request": {**request, "case_ids": item_ids if kind == "run" else [], "run_ids": item_ids if kind == "discussion" else []},
            "items": items,
        }
        self._write_batch(batch)
        task = asyncio.create_task(self._execute_batch(batch_id), name=f"batch:{batch_id}")
        self.batch_tasks[batch_id] = task
        task.add_done_callback(lambda _: self.batch_tasks.pop(batch_id, None))
        return self.batch(batch_id)

    def list_batches(self) -> list[dict[str, Any]]:
        return sorted(
            (self.batch(path.stem, include_progress=False) for path in self.batches_dir.glob("*.json")),
            key=lambda item: item["updated_at"],
            reverse=True,
        )

    def batch(self, batch_id: str, *, include_progress: bool = True) -> dict[str, Any]:
        value = self._read_batch(batch_id)
        counts = {status: 0 for status in ("queued", "running", "completed", "failed", "skipped", "interrupted", "stopped")}
        for item in value["items"]:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
            if include_progress:
                item["progress"] = item.get("progress") or self.events.progress(item["run_id"], before=item.get("finished_at"))
                started = item.get("started_at")
                item["elapsed_seconds"] = (max(0, round((
                    datetime.fromisoformat(item.get("finished_at") or self._now())
                    - datetime.fromisoformat(started)
                ).total_seconds())) if started else None)
        return {**value, "summary": {"total": len(value["items"]), **counts}}

    def retry_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self._read_batch(batch_id)
        if batch["status"] in {"queued", "running"}:
            raise ValueError("请等待本批次结束后重跑未完成病例。")
        retry_items = [item for item in batch["items"] if item["status"] in {"failed", "interrupted", "stopped"}]
        if not retry_items:
            raise ValueError("该批次没有可重跑的失败或中断病例。")
        request = dict(batch["request"])
        if batch["kind"] == "run":
            request["case_ids"] = [item["case_id"] for item in retry_items]
        else:
            request["run_ids"] = [item["run_id"] for item in retry_items]
        return self.create_batch(request, retry_of=batch_id)

    def discussion_running(self, run_id: str) -> bool:
        return run_id in self.active_discussions

    async def _execute(self, run_id: str, input_path: Path | None = None,
                       *, start_stage: str = "semantic_graphing") -> None:
        run_dir = self.catalog.run_dir(run_id)
        manifest_path = run_dir / ".workbench_run.json"
        manifest = self._read_json(manifest_path)
        case_id = manifest["case_id"]
        configs = manifest["configs"]
        try:
            self.workflow._check_stop(run_id)
            self._update_manifest(manifest_path, status="running", status_source="run",
                                  started_at=self._now(), status_updated_at=self._now(), error=None)
            self.events.append(run_id, "run_started", {"case_id": case_id, "start_stage": start_stage}, stage="run")
            if start_stage == "semantic_graphing":
                await self._stage(
                    run_id, run_dir, "semantic_graphing", "semantic_graphing",
                    self.workflow.run_semantic, run_id, run_dir, input_path, case_id,
                    Path(configs["semantic_graphing"]),
                )
                specialty_results = await asyncio.gather(*(
                    self._stage(run_id, run_dir, specialty, "initial_consult",
                                self.workflow.run_specialty, run_id, run_dir, case_id,
                                specialty, Path(configs[specialty]))
                    for specialty in SPECIALTIES
                ), return_exceptions=True)
                self.workflow._check_stop(run_id)
                failures = [str(item) for item in specialty_results if isinstance(item, Exception)]
                if failures:
                    raise RuntimeError("；".join(failures))

            if start_stage in {"semantic_graphing", "mdt_chair"}:
                await self._stage(
                    run_id, run_dir, "mdt_chair", "cross_specialty_integration",
                    self.workflow.run_chair, run_id, run_dir, case_id, Path(configs["mdt_chair"]),
                )
            if start_stage != "mdt_report":
                await self._stage(
                    run_id, run_dir, "mdt_discussion", "team_discussion",
                    self.workflow.run_discussion, run_id, run_dir, case_id,
                    {agent: Path(configs[agent]) for agent in (*SPECIALTIES, "mdt_chair")},
                )
            await self._stage(
                run_id, run_dir, "mdt_report", "final_report", self.workflow.run_report,
                run_id, run_dir, case_id, Path(configs["mdt_chair"]),
            )
            self.workflow._check_stop(run_id)
            if not self.catalog.run_summary(run_dir)["discussion_complete"]:
                raise RuntimeError("未完成 MDT 团队讨论及最终报告。")
        except RunStopped:
            self._update_manifest(manifest_path, status="stopped", status_source="run",
                                  finished_at=self._now(), status_updated_at=self._now(), error=None)
            self.events.append(run_id, "run_stopped", {}, stage="run")
        except asyncio.CancelledError:
            self._update_manifest(manifest_path, status="cancelled", status_source="run",
                                  finished_at=self._now(), status_updated_at=self._now())
            self.events.append(run_id, "run_cancelled", {}, stage="run")
            raise
        except Exception as error:
            failure_path = run_dir / f"{case_id}_workbench_failure_trace.json"
            self._write_json(failure_path, {
                "schema_version": "workbench.failure.v1", "failed_stage": "orchestration",
                "error_type": type(error).__name__, "error": str(error),
            })
            self._update_manifest(manifest_path, status="failed", status_source="run",
                                  finished_at=self._now(), status_updated_at=self._now(), error=str(error))
            self.events.append(run_id, "run_failed", {"error": str(error), "artifact": failure_path.name}, stage="run")
        else:
            self._update_manifest(manifest_path, status="completed", status_source="run",
                                  finished_at=self._now(), status_updated_at=self._now(), error=None)
            self.events.append(run_id, "run_completed", {}, stage="run")
        finally:
            self.workflow.stop_events.pop(run_id, None)

    async def _stage(
        self,
        run_id: str,
        run_dir: Path,
        agent_id: str,
        stage: str,
        function: Any,
        *args: Any,
    ) -> None:
        self.workflow._check_stop(run_id)
        active = {"mdt_chair": self.active_chairs, "mdt_discussion": self.active_discussions,
                  "mdt_report": self.active_reports}.get(agent_id)
        if active is not None:
            active.add(run_id)
        self.events.append(
            run_id, "stage_started", {}, agent_id=agent_id, stage=stage
        )
        try:
            if agent_id in {"mdt_chair", "mdt_discussion", "mdt_report"}:
                self._archive_stage(run_dir, agent_id)
                self._record_stage_rules(run_dir, agent_id)
            await asyncio.to_thread(function, *args)
            self.workflow._check_stop(run_id)
        except RunStopped:
            raise
        except Exception as error:
            self.workflow._check_stop(run_id)
            case_id = self.catalog.case_id(run_dir)
            failure_path = run_dir / f"{case_id}_{agent_id}_{stage}_failure_trace.json"
            self._write_json(
                failure_path,
                {
                    "schema_version": "workbench.agent_failure.v1",
                    "failed_stage": stage,
                    "agent_id": agent_id,
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "attempts": getattr(error, "attempts", []),
                },
            )
            self.events.append(
                run_id,
                "agent_error",
                {"error": str(error), "artifact": failure_path.name},
                agent_id=agent_id,
                stage=stage,
            )
            raise
        finally:
            if active is not None:
                active.discard(run_id)
        self.events.append(
            run_id, "stage_completed", {}, agent_id=agent_id, stage=stage
        )

    def _archive_stage(self, run_dir: Path, stage: str) -> None:
        case = self.catalog.case_id(run_dir)
        patterns = {
            "mdt_chair": [f"{case}_mdt_chair*.json"],
            "mdt_discussion": [f"{case}_mdt_discussion*.json", f"{case}_mdt_round_*.json",
                               f"{case}_*_round_*.json", f"{case}_mdt_final_report*.json", f"{case}_mdt_report*.json"],
            "mdt_report": [f"{case}_mdt_final_report*.json", f"{case}_mdt_report*failure_trace.json"],
        }[stage]
        files = {p for pattern in patterns for p in run_dir.glob(pattern)}
        previous_rules = run_dir / f"{stage}_stage_rules.json"
        if previous_rules.exists():
            files.add(previous_rules)
        if files:
            history = run_dir / "stage_history" / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + stage)
            history.mkdir(parents=True)
            for path in files:
                path.rename(history / path.name)

    def _record_stage_rules(self, run_dir: Path, stage: str) -> None:
        manifest = self._read_json(run_dir / ".workbench_run.json")
        agents = (*SPECIALTIES, "mdt_chair") if stage == "mdt_discussion" else ("mdt_chair",)
        self._write_json(run_dir / f"{stage}_stage_rules.json", {
            "stage": stage, "created_at": self._now(),
            "configs": {agent: load_yaml(manifest["configs"][agent]) for agent in agents},
            "prompts": {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted((self.root / "src/prompts").rglob("*.md"))},
            "guidelines": {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in sorted((self.root / "data/guidelines").glob("*.pdf"))},
        })

    def _refresh_configs(self, run_dir: Path, agents) -> None:
        manifest_path = run_dir / ".workbench_run.json"
        manifest = self._read_json(manifest_path) if manifest_path.exists() else {
            "case_id": self.catalog.case_id(run_dir), "run_id": run_dir.name,
        }
        for agent in agents:
            config = load_yaml(self.root / f"configs/agents/{agent}/agent.yaml")
            override = manifest.get("agent_overrides", {}).get(agent, {})
            if override.get("model"):
                config["model"] = override["model"]
            if override.get("reasoning_effort"):
                config.setdefault("request_options", {})["reasoning_effort"] = override["reasoning_effort"]
            for key, value in list(config.items()):
                if (key == "prompt" or key.endswith("_prompt")) and isinstance(value, str):
                    config[key] = str((self.root / value).resolve())
            if config.get("guideline_retrieval", {}).get("directory"):
                config["guideline_retrieval"]["directory"] = str((self.root / config["guideline_retrieval"]["directory"]).resolve())
            target = run_dir / "workbench_config" / f"{agent}.yaml"
            target.parent.mkdir(exist_ok=True)
            target.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
            manifest.setdefault("configs", {})[agent] = str(target)
        self._write_json(manifest_path, manifest)

    async def _execute_batch(self, batch_id: str) -> None:
        batch = self._read_batch(batch_id)
        request_throttle.register(batch_id, int(batch["request"]["max_request_concurrency"]))
        self._update_batch(batch_id, status="running")
        limiter = asyncio.Semaphore(int(batch["request"]["max_case_concurrency"]))

        async def run_item(item: dict[str, Any]) -> None:
            async with limiter:
                try:
                    run_dir = self.catalog.run_dir(item["run_id"])
                    manifest = self.catalog._json(run_dir / ".workbench_run.json", {})
                    if batch["kind"] == "run" and manifest.get("status") == "stopped":
                        self._update_batch_item(batch_id, item["run_id"], status="stopped", finished_at=self._now())
                        return
                    self._update_batch_item(batch_id, item["run_id"], status="running", started_at=self._now(), error=None)
                    if batch["kind"] == "run":
                        await self.start(item["run_id"], Path(item["input_path"]))
                    else:
                        if self._chair_sha256(item["run_id"]) != item["baseline_sha256"]:
                            self._update_batch_item(batch_id, item["run_id"], status="skipped", finished_at=self._now(), error="主持人整合结果已变化，请重新选择该运行。")
                            return
                        await self.start_discussion(item["run_id"])
                    result = self.catalog.run_summary(run_dir)
                    if result.get("status") in {"stopped", "cancelled"}:
                        self._update_batch_item(batch_id, item["run_id"], status="stopped", finished_at=self._now())
                        return
                    if not result["discussion_complete"] or result.get("status") == "failed":
                        raise RuntimeError((result.get("manifest") or {}).get("error") or "未完成 MDT 团队讨论及最终报告。")
                except Exception as error:
                    self._update_batch_item(batch_id, item["run_id"], status="failed", finished_at=self._now(), error=str(error))
                    return
                self._update_batch_item(batch_id, item["run_id"], status="completed", finished_at=self._now(), error=None)

        try:
            await asyncio.gather(*(run_item(item) for item in batch["items"]))
        finally:
            request_throttle.unregister(batch_id)
        finished = self._read_batch(batch_id)
        status = "completed" if all(item["status"] == "completed" for item in finished["items"]) else "completed_with_errors"
        self._update_batch(batch_id, status=status, finished_at=self._now())

    def _recover_batches(self) -> None:
        for path in self.batches_dir.glob("*.json"):
            batch = self._read_json(path)
            if batch.get("status") not in {"queued", "running"}:
                continue
            for item in batch.get("items", []):
                if item.get("status") in {"queued", "running"}:
                    item["status"] = "interrupted"
                    item["finished_at"] = self._now()
                    item["error"] = "服务重启，未完成的批次任务已安全中断。"
                    item["progress"] = self.events.progress(item["run_id"])
                    manifest_path = self.catalog.runs_dir / item["run_id"] / ".workbench_run.json"
                    if manifest_path.exists():
                        self._update_manifest(manifest_path, status="interrupted", status_source="run",
                                              finished_at=item["finished_at"], status_updated_at=item["finished_at"],
                                              error=item["error"])
            batch["status"] = "interrupted"
            batch["updated_at"] = self._now()
            self._write_json(path, batch)

    def _new_batch_id(self, kind: str) -> str:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{stamp}_{kind}_batch"
        batch_id = base
        counter = 2
        while self._batch_path(batch_id).exists():
            batch_id = f"{base}_{counter}"
            counter += 1
        return batch_id

    def _unique_ids(self, values: Any, label: str) -> list[str]:
        if not isinstance(values, list):
            raise ValueError(f"请至少选择一个{label}。")
        result = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
        if not result:
            raise ValueError(f"请至少选择一个{label}。")
        return result

    def _chair_sha256(self, run_id: str) -> str:
        run_dir = self.catalog.run_dir(run_id)
        path = run_dir / f"{self.catalog.case_id(run_dir)}_mdt_chair_integration.json"
        if not path.is_file():
            raise FileNotFoundError(f"主持人整合结果不存在：{run_id}")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _batch_path(self, batch_id: str) -> Path:
        if not SAFE_CASE_ID.fullmatch(batch_id):
            raise FileNotFoundError(batch_id)
        return self.batches_dir / f"{batch_id}.json"

    def _read_batch(self, batch_id: str) -> dict[str, Any]:
        path = self._batch_path(batch_id)
        if not path.is_file():
            raise FileNotFoundError(batch_id)
        return self._read_json(path)

    def _write_batch(self, batch: dict[str, Any]) -> None:
        self._write_json(self._batch_path(batch["id"]), batch)

    def _update_batch(self, batch_id: str, **changes: Any) -> None:
        batch = self._read_batch(batch_id)
        batch.update(changes)
        batch["updated_at"] = self._now()
        self._write_batch(batch)

    def _update_batch_item(self, batch_id: str, run_id: str, **changes: Any) -> None:
        batch = self._read_batch(batch_id)
        for item in batch["items"]:
            if item["run_id"] == run_id:
                if changes.get("status") not in {None, "queued", "running"}:
                    changes["progress"] = self.events.progress(run_id)
                item.update(changes)
                batch["updated_at"] = self._now()
                self._write_batch(batch)
                return
        raise FileNotFoundError(run_id)

    def _update_manifest(self, path: Path, **changes: Any) -> None:
        manifest = self._read_json(path)
        manifest.update(changes)
        self._write_json(path, manifest)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        from src.utils.persistence import write_json
        write_json(path, value)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
