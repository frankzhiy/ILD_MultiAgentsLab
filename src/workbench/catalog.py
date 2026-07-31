from __future__ import annotations

import json
from hashlib import sha256
from datetime import datetime
from pathlib import Path
from typing import Any

from src.schemas.semantic_graphing.graph_unit import MdtSpecialty


SPECIALTIES = (
    MdtSpecialty.PULMONOLOGY.value,
    MdtSpecialty.THORACIC_RADIOLOGY.value,
    MdtSpecialty.RHEUMATOLOGY.value,
    MdtSpecialty.PATHOLOGY.value,
)

SPECIALTY_LABELS = {
    "pulmonology": "呼吸科",
    "thoracic_radiology": "胸部影像科",
    "rheumatology": "风湿免疫科",
    "pathology": "病理科",
}


class RunCatalog:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.runs_dir = self.root / "outputs/runs"
        self.cases_dir = self.root / "data/raw_cases"
        self.guidelines_dir = self.root / "data/guidelines"

    def list_cases(self) -> list[dict[str, Any]]:
        return [
            {
                "id": path.stem,
                "filename": path.name,
                "bytes": path.stat().st_size,
                "preview": path.read_text(encoding="utf-8")[:180].replace("\n", " "),
            }
            for path in sorted(self.cases_dir.glob("*.txt"))
            if not path.name.startswith(".")
        ]

    def case_text(self, case_id: str) -> str:
        path = self._inside(self.cases_dir, f"{case_id}.txt")
        if not path.is_file():
            raise FileNotFoundError(case_id)
        return path.read_text(encoding="utf-8")

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.runs_dir.exists():
            return []
        runs = [self.run_summary(path) for path in self.runs_dir.iterdir() if path.is_dir()]
        return sorted(runs, key=lambda item: item["updated_at"], reverse=True)

    def run_dir(self, run_id: str) -> Path:
        path = self._inside(self.runs_dir, run_id)
        if not path.is_dir():
            raise FileNotFoundError(run_id)
        return path

    def case_id(self, run_dir: Path) -> str:
        manifest = self._json(run_dir / ".workbench_run.json", {})
        if manifest.get("case_id"):
            return str(manifest["case_id"])
        matches = sorted(run_dir.glob("*_discourse_segments.json"))
        if len(matches) == 1:
            return matches[0].name.removesuffix("_discourse_segments.json")
        inputs = sorted(run_dir.glob("*_input.txt"))
        if len(inputs) == 1:
            return inputs[0].name.removesuffix("_input.txt")
        name = run_dir.name
        return name.split("_", 2)[1] if name[:8].isdigit() and "_" in name else name

    def run_summary(self, run_dir: Path) -> dict[str, Any]:
        case_id = self.case_id(run_dir)
        manifest = self._json(run_dir / ".workbench_run.json", {})
        files = list(run_dir.iterdir())
        names = {path.name for path in files if path.is_file()}
        semantic_complete = f"{case_id}_local_graphs.json" in names
        chair_complete = self._is_current_chair_output(
            self._json(run_dir / f"{case_id}_mdt_chair_integration.json", None)
        )
        discussion = self.discussion(run_dir.name)
        completed_specialties = [
            specialty
            for specialty in SPECIALTIES
            if f"{case_id}_{specialty}_initial.json" in names
        ]
        has_error = any(name.endswith("_error.json") or "failure_trace" in name for name in names)
        candidates = []
        manifest_status = manifest.get("status")
        if manifest_status in {"queued", "running", "completed", "cancelled", "failed"}:
            timestamp = 0.0
            for field in ("status_updated_at", "finished_at", "started_at", "created_at"):
                if manifest.get(field):
                    timestamp = datetime.fromisoformat(manifest[field]).timestamp()
                    break
            candidates.append(
                (timestamp, manifest_status, manifest.get("status_source", "run"))
            )

        chair_result_path = run_dir / f"{case_id}_mdt_chair_integration.json"
        chair_failure_path = (
            run_dir / f"{case_id}_mdt_chair_cross_specialty_integration_failure_trace.json"
        )
        if chair_complete:
            candidates.append((chair_result_path.stat().st_mtime, "completed", "mdt_chair"))
        if chair_failure_path.exists():
            candidates.append((chair_failure_path.stat().st_mtime, "failed", "mdt_chair"))

        discussion_state_path = run_dir / f"{case_id}_mdt_discussion_state.json"
        discussion_failure_path = (
            run_dir / f"{case_id}_mdt_discussion_team_discussion_failure_trace.json"
        )
        if discussion_state_path.exists() and discussion["status"] in {
            "running", "completed", "failed", "cancelled"
        }:
            candidates.append(
                (discussion_state_path.stat().st_mtime, discussion["status"], "mdt_discussion")
            )
        if discussion_failure_path.exists():
            candidates.append(
                (discussion_failure_path.stat().st_mtime, "failed", "mdt_discussion")
            )

        if candidates:
            _, status, status_source = max(candidates, key=lambda item: item[0])
        elif len(completed_specialties) == len(SPECIALTIES):
            status, status_source = "completed", "specialties"
        elif completed_specialties:
            status, status_source = "specialists_running", "specialties"
        elif semantic_complete:
            status, status_source = "routing_pending", "semantic_graphing"
        elif has_error:
            status, status_source = "failed", "run"
        else:
            status, status_source = "semantic_running", "semantic_graphing"
        stat = run_dir.stat()
        return {
            "id": run_dir.name,
            "case_id": case_id,
            "status": status,
            "status_source": status_source,
            "semantic_complete": semantic_complete,
            "chair_complete": chair_complete,
            "discussion_complete": discussion["status"] == "completed",
            "completed_specialties": completed_specialties,
            "has_error_artifact": has_error,
            "orchestrated": bool(manifest),
            "manifest": manifest or None,
            "artifact_count": len(files),
            "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(),
            "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }

    def semantic(self, run_id: str) -> dict[str, Any]:
        run_dir = self.run_dir(run_id)
        case_id = self.case_id(run_dir)
        discourse = self._json(run_dir / f"{case_id}_discourse_segments.json", {})
        graph_units = self._json(run_dir / f"{case_id}_graph_units.json", {})
        primary_frames = self._json(run_dir / f"{case_id}_primary_frames.json", {})
        propositions = self._json(run_dir / f"{case_id}_clinical_propositions.json", {})
        validations = self._json(run_dir / f"{case_id}_proposition_validation.json", {})
        local_graphs = self._json(run_dir / f"{case_id}_local_graphs.json", {})
        input_path = run_dir / f"{case_id}_input.txt"

        segment_index = {item["segment_id"]: item for item in discourse.get("segments", [])}
        frame_index = self._unit_index(primary_frames)
        proposition_index = self._unit_index(propositions)
        validation_index = self._unit_index(validations)
        local_graph_index = self._unit_index(local_graphs)
        segments: list[dict[str, Any]] = []
        for graph_segment in graph_units.get("segments", []):
            segment_id = graph_segment.get("segment_id")
            segment = dict(segment_index.get(segment_id, {}))
            units = []
            for unit in graph_segment.get("graph_units", []):
                unit_id = unit.get("graph_unit_id")
                units.append(
                    {
                        **unit,
                        "primary_frame_detail": frame_index.get(unit_id),
                        "clinical_propositions": proposition_index.get(unit_id),
                        "validation": validation_index.get(unit_id),
                        "local_graph": local_graph_index.get(unit_id),
                    }
                )
            segments.append({**segment, "segment_id": segment_id, "units": units})
        return {
            "case_id": case_id,
            "raw_text": input_path.read_text(encoding="utf-8") if input_path.exists() else "",
            "detected_contained_source_types": discourse.get(
                "detected_contained_source_types", []
            ),
            "segments": segments,
            "summary": {
                "segment_count": len(segments),
                "unit_count": sum(len(item["units"]) for item in segments),
                **local_graphs.get("summary", {}),
            },
        }

    def routing(self, run_id: str) -> dict[str, Any]:
        run_dir = self.run_dir(run_id)
        case_id = self.case_id(run_dir)
        units_by_id: dict[str, dict[str, Any]] = {}
        for specialty in SPECIALTIES:
            payload = self._json(run_dir / f"{case_id}_{specialty}_input.json", None)
            if payload is None:
                continue
            for segment in payload.get("segments", []):
                segment_id = segment.get("segment", {}).get("segment_id")
                for item in segment.get("units", []):
                    graph_unit = item.get("graph_unit", {})
                    unit_id = graph_unit.get("graph_unit_id")
                    if not unit_id or unit_id in units_by_id:
                        continue
                    units_by_id[unit_id] = {
                        "segment_id": segment_id,
                        "graph_unit_id": unit_id,
                        "text": graph_unit.get("text"),
                        "mdt_specialty": graph_unit.get("mdt_specialty", []),
                        "locator_status": item.get("locator_status"),
                    }
        units = list(units_by_id.values())
        shared_count = sum(
            MdtSpecialty.SHARED_CONTEXT.value in item["mdt_specialty"] for item in units
        )
        available_count = sum(item["locator_status"] == "available" for item in units)
        return {
            "case_id": case_id,
            "specialties": [
                {"specialty": specialty, "label": SPECIALTY_LABELS[specialty]}
                for specialty in SPECIALTIES
            ],
            "summary": {
                "unit_count": len(units),
                "shared_context_unit_count": shared_count,
                "specialty_unit_count": len(units) - shared_count,
                "available_locator_count": available_count,
            },
            "units": units,
        }

    def specialties(self, run_id: str) -> dict[str, Any]:
        run_dir = self.run_dir(run_id)
        case_id = self.case_id(run_dir)
        results = []
        for specialty in SPECIALTIES:
            output_path = run_dir / f"{case_id}_{specialty}_initial.json"
            input_path = run_dir / f"{case_id}_{specialty}_input.json"
            output = self._json(output_path, None)
            results.append(
                {
                    "specialty": specialty,
                    "label": SPECIALTY_LABELS[specialty],
                    "status": "completed" if output_path.exists() else "pending",
                    "input_summary": self._json(input_path, {}).get("summary", {}),
                    "output": output,
                    "legacy": output is not None and not self._is_formal_output(output),
                }
            )
        return {"case_id": case_id, "results": results}

    def chair(self, run_id: str) -> dict[str, Any]:
        run_dir = self.run_dir(run_id)
        case_id = self.case_id(run_dir)
        specialty_paths = [
            run_dir / f"{case_id}_{specialty}_initial.json"
            for specialty in SPECIALTIES
        ]
        missing = [
            specialty
            for specialty, path in zip(SPECIALTIES, specialty_paths, strict=True)
            if not path.exists()
        ]
        legacy = [
            specialty
            for specialty, path in zip(SPECIALTIES, specialty_paths, strict=True)
            if path.exists() and not self._is_formal_output(self._json(path, None))
        ]
        semantic_paths = [
            run_dir / f"{case_id}_clinical_propositions.json",
            run_dir / f"{case_id}_local_graphs.json",
        ]
        runnable = not missing and not legacy and all(path.exists() for path in semantic_paths)
        readiness_error = None
        if missing:
            readiness_error = f"缺少专科正式输出：{', '.join(missing)}"
        elif legacy:
            readiness_error = f"以下专科仍是旧版输出：{', '.join(legacy)}"
        elif not all(path.exists() for path in semantic_paths):
            readiness_error = "缺少 clinical propositions 或 local graphs。"

        result_path = run_dir / f"{case_id}_mdt_chair_integration.json"
        failure_path = (
            run_dir
            / f"{case_id}_mdt_chair_cross_specialty_integration_failure_trace.json"
        )
        result = self._json(result_path, None)
        current_result = self._is_current_chair_output(result)
        latest_failure = failure_path.exists() and (
            not result_path.exists()
            or failure_path.stat().st_mtime > result_path.stat().st_mtime
        )
        failure = self._json(failure_path, {}) if latest_failure else {}
        status = (
            "failed"
            if latest_failure
            else "completed"
            if current_result
            else "outdated"
            if result is not None
            else "pending"
            if runnable
            else "unavailable"
        )
        return {
            "case_id": case_id,
            "status": status,
            "runnable": runnable,
            "result": result,
            "error": failure.get("error") or readiness_error,
        }

    def discussion(self, run_id: str) -> dict[str, Any]:
        run_dir = self.run_dir(run_id)
        case_id = self.case_id(run_dir)
        chair = self.chair(run_id)
        baseline_path = run_dir / f"{case_id}_mdt_chair_integration.json"
        runnable = chair["runnable"] and chair["status"] == "completed"
        readiness_error = (
            None
            if runnable
            else chair.get("error") or "需要先获得当前版本的主持人整合结果。"
        )
        state_path = run_dir / f"{case_id}_mdt_discussion_state.json"
        failure_path = (
            run_dir / f"{case_id}_mdt_discussion_team_discussion_failure_trace.json"
        )
        state = self._json(state_path, None)
        baseline_hash = (
            sha256(baseline_path.read_bytes()).hexdigest()
            if baseline_path.exists()
            else None
        )
        current = bool(state and state.get("baseline_sha256") == baseline_hash)
        latest_failure = failure_path.exists() and (
            not state_path.exists() or failure_path.stat().st_mtime > state_path.stat().st_mtime
        )
        failure = self._json(failure_path, {}) if latest_failure else {}
        if latest_failure:
            status = "failed"
        elif state and not current:
            status = "outdated"
        elif state:
            status = state.get("status", "pending")
        else:
            status = "pending" if runnable else "unavailable"
        return {
            "case_id": case_id,
            "status": status,
            "runnable": runnable,
            "current_round": (
                ((state or {}).get("active_round") or {}).get("round_number")
                or len((state or {}).get("rounds", []))
            ),
            "max_rounds": (state or {}).get("max_rounds", 3),
            "rounds": (state or {}).get("rounds", []),
            "active_round": (state or {}).get("active_round"),
            "report_status": (state or {}).get("report_status", "waiting"),
            "latest_chair_result": (state or {}).get("latest_chair_result"),
            "stop_reason": (state or {}).get("stop_reason"),
            "final_report": (state or {}).get("final_report"),
            "error": (
                failure.get("error") or (state or {}).get("error") or readiness_error
            ),
        }

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        run_dir = self.run_dir(run_id)
        result = []
        for path in sorted(item for item in run_dir.rglob("*") if item.is_file()):
            relative = path.relative_to(run_dir).as_posix()
            result.append(
                {
                    "name": relative,
                    "bytes": path.stat().st_size,
                    "kind": path.suffix.lstrip(".") or "file",
                    "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                }
            )
        return result

    def errors(self, run_id: str) -> list[dict[str, Any]]:
        run_dir = self.run_dir(run_id)
        case_id = self.case_id(run_dir)
        run_summary = self.run_summary(run_dir)
        records = []
        for path in sorted(run_dir.glob("*.json")):
            if not (path.name.endswith("_error.json") or "failure_trace" in path.name):
                continue
            payload = self._json(path, {})
            agent_id = self._error_agent(path.name)
            if "workbench_failure_trace" in path.name:
                current = (
                    run_summary["status"] == "failed"
                    and run_summary["status_source"] == "run"
                )
            elif agent_id == "mdt_chair":
                current = (
                    run_summary["status"] == "failed"
                    and run_summary["status_source"] == "mdt_chair"
                )
            elif agent_id == "mdt_discussion":
                current = (
                    run_summary["status"] == "failed"
                    and run_summary["status_source"] == "mdt_discussion"
                )
            else:
                success_path = run_dir / (
                    f"{case_id}_{agent_id}_initial.json"
                    if agent_id in SPECIALTIES
                    else f"{case_id}_local_graphs.json"
                )
                current = (
                    not success_path.exists()
                    or path.stat().st_mtime > success_path.stat().st_mtime
                )
            records.append(
                {
                    "artifact": path.name,
                    "agent_id": agent_id,
                    "current": current,
                    "failed_stage": payload.get("failed_stage") or payload.get("stage"),
                    "error_type": payload.get("error_type"),
                    "error": payload.get("error") or payload.get("message") or "产物记录了失败，但未提供摘要。",
                    "attempts": payload.get("attempts", []),
                    "exception_chain": payload.get("exception_chain", []),
                    "traceback": payload.get("traceback"),
                    "payload": payload,
                }
            )
        return records

    def artifact_path(self, run_id: str, relative_path: str) -> Path:
        run_dir = self.run_dir(run_id)
        path = self._inside(run_dir, relative_path)
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path

    def guidelines(self) -> list[dict[str, Any]]:
        catalog_path = self.guidelines_dir / "catalog.yaml"
        catalog_text = catalog_path.read_text(encoding="utf-8") if catalog_path.exists() else ""
        return [
            {
                "filename": path.name,
                "bytes": path.stat().st_size,
                "catalogued": path.name in catalog_text,
            }
            for path in sorted(self.guidelines_dir.glob("*.pdf"))
        ]

    def guideline_path(self, filename: str) -> Path:
        path = self._inside(self.guidelines_dir, filename)
        if path.suffix.lower() != ".pdf" or not path.is_file():
            raise FileNotFoundError(filename)
        return path

    @staticmethod
    def _json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _unit_index(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
        result = {}
        for segment in document.get("segments", []):
            for unit in segment.get("units", []):
                if unit.get("graph_unit_id"):
                    result[unit["graph_unit_id"]] = unit
        return result

    @staticmethod
    def _is_formal_output(value: Any) -> bool:
        return isinstance(value, dict) and set(value) == {
            "specialty_assessments",
            "interspecialty_questions",
        }

    @staticmethod
    def _is_current_chair_output(value: Any) -> bool:
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != "mdt_chair.v9"
        ):
            return False
        return all(
            isinstance(value.get(field), list)
            for field in (
                "integrated_conclusions",
                "assessment_boundaries",
                "conflicts",
                "questions",
                "evidence_needs",
            )
        )

    @staticmethod
    def _error_agent(filename: str) -> str:
        for specialty in (*SPECIALTIES, "mdt_chair", "mdt_discussion", "semantic_graphing"):
            if specialty in filename:
                return specialty
        return "semantic_graphing"

    @staticmethod
    def _inside(parent: Path, relative: str) -> Path:
        path = (parent / relative).resolve()
        if path != parent and parent not in path.parents:
            raise ValueError("Path escapes the allowed directory")
        return path
