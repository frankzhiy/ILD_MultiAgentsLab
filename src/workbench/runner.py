from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.utils.config import load_yaml
from src.llm.throttle import request_throttle
from src.workbench.catalog import RunCatalog, SPECIALTIES
from src.workbench.events import EventStore
from src.workbench.workflow import WorkbenchWorkflow


AGENTS = ("semantic_graphing", *SPECIALTIES, "mdt_chair")
SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


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
        self.active_chairs: set[str] = set()
        self.active_discussions: set[str] = set()
        self._recover_batches()

    def prepare(self, request: dict[str, Any]) -> tuple[str, Path]:
        case_id = str(request.get("case_id") or "").strip()
        if not SAFE_CASE_ID.fullmatch(case_id):
            raise ValueError(
                "case_id 只能包含字母、数字、点、下划线和连字符，且长度不超过 80。"
            )
        source = request.get("source", "library")
        if source == "library":
            input_path = self.catalog.cases_dir / f"{case_id}.txt"
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

        config_dir = run_dir / "workbench_config"
        config_dir.mkdir()
        overrides = request.get("agents") or {}
        config_paths: dict[str, str] = {}
        for agent_id in AGENTS:
            source_path = self.root / "configs/agents" / agent_id / "agent.yaml"
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

        semantic_config = load_yaml(Path(config_paths["semantic_graphing"]))
        signature = build_run_signature(semantic_config)
        self._write_json(run_dir / f"{case_id}_run_signature.json", signature)
        manifest = {
            "schema_version": "workbench.run.v1",
            "run_id": run_id,
            "case_id": case_id,
            "source": source,
            "input_path": str(input_path.resolve()),
            "status": "queued",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "max_concurrency": int(request.get("max_concurrency") or 6),
            "configs": config_paths,
        }
        self._write_json(run_dir / ".workbench_run.json", manifest)
        return run_id, input_path

    def start(self, run_id: str, input_path: Path) -> asyncio.Task[None]:
        if run_id in self.tasks and not self.tasks[run_id].done():
            raise ValueError(f"运行已启动：{run_id}")
        task = asyncio.create_task(self._execute(run_id, input_path), name=f"run:{run_id}")
        self.tasks[run_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(run_id, None))
        return task

    def start_chair(self, run_id: str) -> None:
        run_dir = self.catalog.run_dir(run_id)
        readiness = self.catalog.chair(run_id)
        if not readiness["runnable"]:
            raise ValueError(readiness["error"] or "四个专科正式输出尚未全部就绪。")
        key = f"{run_id}:chair"
        if run_id in self.tasks or key in self.tasks or run_id in self.active_discussions:
            raise ValueError("该运行仍在执行，不能重复启动主持人。")
        config_path = self._chair_config(run_dir)
        manifest_path = run_dir / ".workbench_run.json"
        if manifest_path.exists():
            self._update_manifest(
                manifest_path,
                status="running",
                status_source="mdt_chair",
                status_updated_at=self._now(),
                error=None,
            )
        task = asyncio.create_task(
            self._execute_chair(run_id, run_dir, config_path),
            name=f"chair:{run_id}",
        )
        self.active_chairs.add(run_id)
        self.tasks[key] = task
        task.add_done_callback(lambda _: self._finish_chair(key, run_id))

    def chair_running(self, run_id: str) -> bool:
        return run_id in self.active_chairs

    def start_discussion(self, run_id: str) -> asyncio.Task[None]:
        run_dir = self.catalog.run_dir(run_id)
        readiness = self.catalog.discussion(run_id)
        if not readiness["runnable"]:
            raise ValueError(readiness["error"] or "讨论所需的既有产物尚未就绪。")
        key = f"{run_id}:discussion"
        if run_id in self.tasks or key in self.tasks or run_id in self.active_chairs:
            raise ValueError("该运行仍在执行，不能重复启动团队讨论。")
        config_paths = self._discussion_configs(run_dir)
        manifest_path = run_dir / ".workbench_run.json"
        if manifest_path.exists():
            self._update_manifest(
                manifest_path,
                status="running",
                status_source="mdt_discussion",
                status_updated_at=self._now(),
                error=None,
            )
        task = asyncio.create_task(
            self._execute_discussion(run_id, run_dir, config_paths),
            name=f"discussion:{run_id}",
        )
        self.active_discussions.add(run_id)
        self.tasks[key] = task
        task.add_done_callback(lambda _: self._finish_discussion(key, run_id))
        return task

    def create_batch(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = str(request.get("kind") or "")
        if kind not in {"run", "discussion"}:
            raise ValueError("批次类型必须是 run 或 discussion。")
        if kind == "run":
            item_ids = self._unique_ids(request.get("case_ids"), "病例")
            for case_id in item_ids:
                if not SAFE_CASE_ID.fullmatch(case_id):
                    raise ValueError(f"病例 ID 不合法：{case_id}")
                if not (self.catalog.cases_dir / f"{case_id}.txt").is_file():
                    raise FileNotFoundError(f"病例不存在：{case_id}")
            items = []
            for case_id in item_ids:
                run_id, input_path = self.prepare(
                    {**request, "source": "library", "case_id": case_id}
                )
                items.append(
                    {
                        "case_id": case_id,
                        "run_id": run_id,
                        "input_path": str(input_path),
                        "status": "queued",
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
            (self.batch(path.stem) for path in self.batches_dir.glob("*.json")),
            key=lambda item: item["updated_at"],
            reverse=True,
        )

    def batch(self, batch_id: str) -> dict[str, Any]:
        value = self._read_batch(batch_id)
        counts = {status: 0 for status in ("queued", "running", "completed", "failed", "skipped", "interrupted")}
        for item in value["items"]:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {**value, "summary": {"total": len(value["items"]), **counts}}

    def retry_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self._read_batch(batch_id)
        retry_items = [item for item in batch["items"] if item["status"] in {"failed", "interrupted"}]
        if not retry_items:
            raise ValueError("该批次没有可重跑的失败或中断病例。")
        request = dict(batch["request"])
        if batch["kind"] == "run":
            request["case_ids"] = [item["case_id"] for item in retry_items]
        else:
            request["run_ids"] = [item["run_id"] for item in retry_items]
        return self.create_batch(request)

    def discussion_running(self, run_id: str) -> bool:
        return run_id in self.active_discussions

    async def _execute(self, run_id: str, input_path: Path) -> None:
        run_dir = self.catalog.run_dir(run_id)
        manifest_path = run_dir / ".workbench_run.json"
        manifest = self._read_json(manifest_path)
        case_id = manifest["case_id"]
        configs = manifest["configs"]
        self._update_manifest(
            manifest_path,
            status="running",
            status_source="run",
            started_at=self._now(),
            status_updated_at=self._now(),
        )
        self.events.append(run_id, "run_started", {"case_id": case_id}, stage="run")
        try:
            await self._stage(
                run_id,
                run_dir,
                "semantic_graphing",
                "semantic_graphing",
                self.workflow.run_semantic,
                run_id,
                run_dir,
                input_path,
                case_id,
                Path(configs["semantic_graphing"]),
            )

            specialty_results = await asyncio.gather(
                *(
                    self._stage(
                        run_id,
                        run_dir,
                        specialty,
                        "initial_consult",
                        self.workflow.run_specialty,
                        run_id,
                        run_dir,
                        case_id,
                        specialty,
                        Path(configs[specialty]),
                    )
                    for specialty in SPECIALTIES
                ),
                return_exceptions=True,
            )
            failures = [str(item) for item in specialty_results if isinstance(item, Exception)]
            if failures:
                raise RuntimeError("；".join(failures))

            self.active_chairs.add(run_id)
            try:
                await self._stage(
                    run_id,
                    run_dir,
                    "mdt_chair",
                    "cross_specialty_integration",
                    self.workflow.run_chair,
                    run_id,
                    run_dir,
                    case_id,
                    Path(configs["mdt_chair"]),
                )
            finally:
                self.active_chairs.discard(run_id)

            self._update_manifest(
                manifest_path,
                status="running",
                status_source="run",
                status_updated_at=self._now(),
            )
            self.active_discussions.add(run_id)
            try:
                await self._stage(
                    run_id,
                    run_dir,
                    "mdt_discussion",
                    "team_discussion",
                    self.workflow.run_discussion,
                    run_id,
                    run_dir,
                    case_id,
                    self._discussion_configs(run_dir),
                )
            finally:
                self.active_discussions.discard(run_id)
            if not self.catalog.run_summary(run_dir)["discussion_complete"]:
                raise RuntimeError("未完成 MDT 团队讨论及最终报告。")

        except asyncio.CancelledError:
            self._update_manifest(
                manifest_path,
                status="cancelled",
                status_source="run",
                finished_at=self._now(),
                status_updated_at=self._now(),
            )
            self.events.append(run_id, "run_cancelled", {}, stage="run")
            raise
        except Exception as error:
            failure_path = run_dir / f"{case_id}_workbench_failure_trace.json"
            self._write_json(
                failure_path,
                {
                    "schema_version": "workbench.failure.v1",
                    "failed_stage": "orchestration",
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
            )
            self._update_manifest(
                manifest_path,
                status="failed",
                status_source="run",
                finished_at=self._now(),
                status_updated_at=self._now(),
                error=str(error),
            )
            self.events.append(
                run_id,
                "run_failed",
                {"error": str(error), "artifact": failure_path.name},
                stage="run",
            )
            return
        self._update_manifest(
            manifest_path,
            status="completed",
            status_source="run",
            finished_at=self._now(),
            status_updated_at=self._now(),
        )
        self.events.append(run_id, "run_completed", {}, stage="run")

    async def _stage(
        self,
        run_id: str,
        run_dir: Path,
        agent_id: str,
        stage: str,
        function: Any,
        *args: Any,
    ) -> None:
        self.events.append(
            run_id, "stage_started", {}, agent_id=agent_id, stage=stage
        )
        try:
            await asyncio.to_thread(function, *args)
        except Exception as error:
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
        self.events.append(
            run_id, "stage_completed", {}, agent_id=agent_id, stage=stage
        )

    async def _execute_chair(
        self, run_id: str, run_dir: Path, config_path: Path
    ) -> None:
        manifest_path = run_dir / ".workbench_run.json"
        self.events.append(
            run_id,
            "manual_stage_started",
            {},
            agent_id="mdt_chair",
            stage="cross_specialty_integration",
        )
        try:
            await self._stage(
                run_id,
                run_dir,
                "mdt_chair",
                "cross_specialty_integration",
                self.workflow.run_chair,
                run_id,
                run_dir,
                self.catalog.case_id(run_dir),
                config_path,
            )
        except Exception as error:
            if manifest_path.exists():
                self._update_manifest(
                    manifest_path,
                    status="failed",
                    status_source="mdt_chair",
                    finished_at=self._now(),
                    status_updated_at=self._now(),
                    error=str(error),
                )
            return
        if manifest_path.exists():
            self._update_manifest(
                manifest_path,
                status="completed",
                status_source="mdt_chair",
                finished_at=self._now(),
                status_updated_at=self._now(),
                error=None,
            )
        self.events.append(
            run_id,
            "manual_stage_completed",
            {},
            agent_id="mdt_chair",
            stage="cross_specialty_integration",
        )

    async def _execute_discussion(
        self, run_id: str, run_dir: Path, config_paths: dict[str, Path]
    ) -> None:
        manifest_path = run_dir / ".workbench_run.json"
        self.events.append(
            run_id,
            "manual_stage_started",
            {},
            agent_id="mdt_discussion",
            stage="team_discussion",
        )
        try:
            await self._stage(
                run_id,
                run_dir,
                "mdt_discussion",
                "team_discussion",
                self.workflow.run_discussion,
                run_id,
                run_dir,
                self.catalog.case_id(run_dir),
                config_paths,
            )
        except Exception as error:
            if manifest_path.exists():
                self._update_manifest(
                    manifest_path,
                    status="failed",
                    status_source="mdt_discussion",
                    finished_at=self._now(),
                    status_updated_at=self._now(),
                    error=str(error),
                )
            return
        if manifest_path.exists():
            self._update_manifest(
                manifest_path,
                status="completed",
                status_source="mdt_discussion",
                finished_at=self._now(),
                status_updated_at=self._now(),
                error=None,
            )
        self.events.append(
            run_id,
            "manual_stage_completed",
            {},
            agent_id="mdt_discussion",
            stage="team_discussion",
        )

    def _finish_chair(self, key: str, run_id: str) -> None:
        self.tasks.pop(key, None)
        self.active_chairs.discard(run_id)

    def _finish_discussion(self, key: str, run_id: str) -> None:
        self.tasks.pop(key, None)
        self.active_discussions.discard(run_id)

    def _chair_config(self, run_dir: Path) -> Path:
        manifest_path = run_dir / ".workbench_run.json"
        manifest = self._read_json(manifest_path) if manifest_path.exists() else {}
        configured = (manifest.get("configs") or {}).get("mdt_chair")
        if configured and Path(configured).is_file():
            return Path(configured)

        config = load_yaml(self.root / "configs/agents/mdt_chair/agent.yaml")
        for key, value in list(config.items()):
            if (key == "prompt" or key.endswith("_prompt")) and isinstance(value, str):
                config[key] = str((self.root / value).resolve())
        target = run_dir / "workbench_config/mdt_chair.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        if manifest_path.exists():
            manifest.setdefault("configs", {})["mdt_chair"] = str(target)
            self._write_json(manifest_path, manifest)
        return target

    def _discussion_configs(self, run_dir: Path) -> dict[str, Path]:
        manifest_path = run_dir / ".workbench_run.json"
        manifest = self._read_json(manifest_path) if manifest_path.exists() else {}
        configured = manifest.setdefault("configs", {})
        result: dict[str, Path] = {}
        for agent_id in (*SPECIALTIES, "mdt_chair"):
            path = Path(configured.get(agent_id, ""))
            if not path.is_file():
                config = load_yaml(self.root / f"configs/agents/{agent_id}/agent.yaml")
                for key, value in list(config.items()):
                    if (key == "prompt" or key.endswith("_prompt")) and isinstance(value, str):
                        config[key] = str((self.root / value).resolve())
                retrieval = config.get("guideline_retrieval")
                if isinstance(retrieval, dict) and isinstance(retrieval.get("directory"), str):
                    retrieval["directory"] = str((self.root / retrieval["directory"]).resolve())
                path = run_dir / "workbench_config" / f"{agent_id}.yaml"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )
                configured[agent_id] = str(path)
            result[agent_id] = path
        if manifest_path.exists():
            self._write_json(manifest_path, manifest)
        return result

    async def _execute_batch(self, batch_id: str) -> None:
        batch = self._read_batch(batch_id)
        request_throttle.register(batch_id, int(batch["request"]["max_request_concurrency"]))
        self._update_batch(batch_id, status="running")
        limiter = asyncio.Semaphore(int(batch["request"]["max_case_concurrency"]))

        async def run_item(item: dict[str, Any]) -> None:
            async with limiter:
                self._update_batch_item(batch_id, item["run_id"], status="running", started_at=self._now(), error=None)
                try:
                    if batch["kind"] == "run":
                        await self.start(item["run_id"], Path(item["input_path"]))
                        result = self.catalog.run_summary(self.catalog.run_dir(item["run_id"]))
                        if not result["discussion_complete"]:
                            raise RuntimeError((result.get("manifest") or {}).get("error") or "未完成 MDT 团队讨论及最终报告。")
                    else:
                        if self._chair_sha256(item["run_id"]) != item["baseline_sha256"]:
                            self._update_batch_item(batch_id, item["run_id"], status="skipped", finished_at=self._now(), error="主持人整合结果已变化，请重新选择该运行。")
                            return
                        await self.start_discussion(item["run_id"])
                        result = self.catalog.discussion(item["run_id"])
                        if result["status"] != "completed":
                            raise RuntimeError(result.get("error") or "团队讨论未完成。")
                except Exception as error:
                    self._update_batch_item(batch_id, item["run_id"], status="failed", finished_at=self._now(), error=str(error))
                    return
                self._update_batch_item(batch_id, item["run_id"], status="completed", finished_at=self._now(), error=None)

        try:
            await asyncio.gather(*(run_item(item) for item in batch["items"]))
        finally:
            request_throttle.unregister(batch_id)
        self._update_batch(batch_id, status="completed")

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
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
