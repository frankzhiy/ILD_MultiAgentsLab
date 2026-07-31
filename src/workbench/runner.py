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
        self.active_chairs: set[str] = set()
        self.active_discussions: set[str] = set()

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

    def start(self, run_id: str, input_path: Path) -> None:
        if run_id in self.tasks and not self.tasks[run_id].done():
            raise ValueError(f"运行已启动：{run_id}")
        task = asyncio.create_task(self._execute(run_id, input_path), name=f"run:{run_id}")
        self.tasks[run_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(run_id, None))

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

    def start_discussion(self, run_id: str) -> None:
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
