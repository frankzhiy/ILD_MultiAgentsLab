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


AGENTS = ("semantic_graphing", *SPECIALTIES)
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
        base_id = f"{stamp}_{case_id}_step2_step3"
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

    async def _execute(self, run_id: str, input_path: Path) -> None:
        run_dir = self.catalog.run_dir(run_id)
        manifest_path = run_dir / ".workbench_run.json"
        manifest = self._read_json(manifest_path)
        case_id = manifest["case_id"]
        configs = manifest["configs"]
        self._update_manifest(manifest_path, status="running", started_at=self._now())
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

        except asyncio.CancelledError:
            self._update_manifest(manifest_path, status="cancelled", finished_at=self._now())
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
                manifest_path, status="failed", finished_at=self._now(), error=str(error)
            )
            self.events.append(
                run_id,
                "run_failed",
                {"error": str(error), "artifact": failure_path.name},
                stage="run",
            )
            return
        self._update_manifest(manifest_path, status="completed", finished_at=self._now())
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
