from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from src.agents.common.specialty_input import build_specialty_case_input
from src.agents.semantic_graphing.agent import SemanticGraphingAgent
from src.llm.factory import build_llm_client
from src.llm.prompting import llm_value
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.utils.config import load_yaml
from src.workbench.events import EventStore


class WorkbenchWorkflow:
    """Application service that runs agents directly, independently of CLI scripts."""

    def __init__(self, root: Path, events: EventStore) -> None:
        self.root = root.resolve()
        self.events = events

    def run_semantic(
        self,
        run_id: str,
        run_dir: Path,
        input_path: Path,
        case_id: str,
        config_path: Path,
    ) -> None:
        self._load_env()
        input_text = input_path.read_text(encoding="utf-8")
        (run_dir / f"{case_id}_input.txt").write_text(input_text, encoding="utf-8")
        config = load_yaml(config_path)
        llm = build_llm_client(config)
        agent = SemanticGraphingAgent.from_config(config_path, llm)
        progress = self._progress(run_id, "semantic_graphing")
        trace: dict[str, Any] = {
            "case_id": case_id,
            "model": getattr(llm, "model", config.get("model")),
            "runtime_config": {
                key: config.get(key)
                for key in (
                    "max_concurrency",
                    "max_attempts",
                    "retry_backoff_seconds",
                    "clinical_proposition_max_chunk_chars",
                    "clinical_proposition_enable_chunking",
                )
            },
        }
        started = time.perf_counter()

        result = agent.classify(input_text, case_id=case_id, progress=progress)
        self._write(run_dir / f"{case_id}_discourse_segments.json", result.classification)
        trace.update(result.trace)
        self._write(run_dir / f"{case_id}_trace.json", trace)

        graph_units, graph_trace = agent.extract_graph_units(
            result.classification,
            progress=progress,
            cache_dir=run_dir / "task_cache/graph_units",
        )
        self._write(run_dir / f"{case_id}_graph_units.json", graph_units)
        trace["graph_unit_extraction"] = graph_trace
        self._write(run_dir / f"{case_id}_trace.json", trace)

        primary_frames, frame_trace = agent.select_primary_frames(
            graph_units,
            progress=progress,
            cache_dir=run_dir / "task_cache/primary_frames",
        )
        self._write(run_dir / f"{case_id}_primary_frames.json", primary_frames)
        trace["primary_frame_selection"] = frame_trace
        self._write(run_dir / f"{case_id}_trace.json", trace)

        clinical_propositions, proposition_trace = agent.extract_clinical_propositions(
            graph_units,
            primary_frames,
            progress=progress,
            cache_dir=run_dir / "task_cache/clinical_propositions",
        )
        self._write(
            run_dir / f"{case_id}_clinical_propositions.json", clinical_propositions
        )
        trace["clinical_proposition_extraction"] = proposition_trace
        self._write(run_dir / f"{case_id}_trace.json", trace)

        validation = agent.validate_clinical_propositions(
            graph_units, primary_frames, clinical_propositions, progress=progress
        )
        local_graphs = agent.build_local_graphs(
            graph_units,
            primary_frames,
            clinical_propositions,
            validation,
            progress=progress,
        )
        self._write(run_dir / f"{case_id}_proposition_validation.json", validation)
        self._write(run_dir / f"{case_id}_local_graphs.json", local_graphs)
        self._write(run_dir / f"{case_id}_trace.json", trace)
        timing = {"total_elapsed_seconds": round(time.perf_counter() - started, 3)}
        self._write(run_dir / f"{case_id}_timing.json", timing)

    def run_specialty(
        self,
        run_id: str,
        run_dir: Path,
        case_id: str,
        specialty: str,
        config_path: Path,
    ) -> None:
        self._load_env()
        enum = MdtSpecialty(specialty)
        case = build_specialty_case_input(run_dir, enum, case_id=case_id)
        stem = f"{case_id}_{specialty}"
        self._write(run_dir / f"{stem}_input.json", case)
        callback = self._progress(run_id, specialty)
        config = load_yaml(config_path)
        llm = build_llm_client(config)

        if specialty == MdtSpecialty.PULMONOLOGY.value:
            from src.agents.common.evidence_projection import (
                build_specialty_evidence_prompt_input,
                build_specialty_working_input,
            )
            from src.agents.pulmonology.agent import PulmonologyAgent

            working = build_specialty_working_input(case)
            self._write(run_dir / f"{stem}_working_input.json", working)
            self._write(
                run_dir / f"{stem}_evidence_input.json",
                llm_value(build_specialty_evidence_prompt_input(case)),
            )
            agent = PulmonologyAgent.from_config(
                config_path, llm, event_callback=callback
            )
            consultation = agent.initial_consult(case)
        elif specialty == MdtSpecialty.RHEUMATOLOGY.value:
            from src.agents.common.evidence_projection import (
                build_specialty_evidence_prompt_input,
                build_specialty_working_input,
            )
            from src.agents.rheumatology.agent import RheumatologyAgent

            working = build_specialty_working_input(case)
            self._write(run_dir / f"{stem}_working_input.json", working)
            self._write(
                run_dir / f"{stem}_evidence_input.json",
                llm_value(build_specialty_evidence_prompt_input(case)),
            )
            agent = RheumatologyAgent.from_config(
                config_path, llm, event_callback=callback
            )
            consultation = agent.initial_consult(case)
        elif specialty == MdtSpecialty.PATHOLOGY.value:
            from src.agents.common.evidence_projection import (
                build_specialty_evidence_prompt_input,
                build_specialty_working_input,
            )
            from src.agents.pathology.agent import PathologyAgent

            working = build_specialty_working_input(case)
            self._write(run_dir / f"{stem}_working_input.json", working)
            self._write(
                run_dir / f"{stem}_evidence_input.json",
                llm_value(build_specialty_evidence_prompt_input(case)),
            )
            agent = PathologyAgent.from_config(config_path, llm, event_callback=callback)
            consultation = agent.initial_consult(case)
        elif specialty == MdtSpecialty.THORACIC_RADIOLOGY.value:
            from src.agents.thoracic_radiology.agent import ThoracicRadiologyAgent
            from src.agents.thoracic_radiology.evidence_projection import (
                build_radiology_evidence_prompt_input,
                build_radiology_reconstruction_prompt_input,
                build_radiology_working_input,
            )

            working = build_radiology_working_input(case)
            self._write(run_dir / f"{stem}_working_input.json", working)
            self._write(
                run_dir / f"{stem}_reconstruction_input.json",
                llm_value(build_radiology_reconstruction_prompt_input(case, working)),
            )
            self._write(
                run_dir / f"{stem}_evidence_input.json",
                llm_value(build_radiology_evidence_prompt_input(working)),
            )
            agent = ThoracicRadiologyAgent.from_config(
                config_path, llm, event_callback=callback
            )
            consultation = agent.initial_consult(case)
        else:  # pragma: no cover - enum validation guards this
            raise ValueError(f"Unsupported specialty: {specialty}")

        self._write(run_dir / f"{stem}_internal_state.json", consultation.internal_state)
        self._write(run_dir / f"{stem}_initial.json", consultation.formal_output)
        self._write(run_dir / f"{stem}_initial_trace.json", consultation.trace)

    def run_chair(
        self,
        run_id: str,
        run_dir: Path,
        case_id: str,
        config_path: Path,
    ) -> None:
        self._load_env()
        from src.agents.mdt_chair.agent import (
            MDTChairAgent,
            build_chair_prompt_bundle,
            build_semantic_evidence_catalog,
        )

        def read(path: Path) -> Any:
            return json.loads(path.read_text(encoding="utf-8"))
        outputs = {
            specialty: read(run_dir / f"{case_id}_{specialty}_initial.json")
            for specialty in (
                "pulmonology",
                "thoracic_radiology",
                "rheumatology",
                "pathology",
            )
        }
        semantic_evidence = build_semantic_evidence_catalog(
            read(run_dir / f"{case_id}_clinical_propositions.json"),
            read(run_dir / f"{case_id}_local_graphs.json"),
        )
        bundle = build_chair_prompt_bundle(
            case_id, outputs, semantic_evidence=semantic_evidence
        )
        self._write(
            run_dir / f"{case_id}_mdt_chair_prompt_input.json",
            bundle.prompt_input,
        )
        config = load_yaml(config_path)
        llm = build_llm_client(config)
        agent = MDTChairAgent.from_config(
            config_path,
            llm,
            event_callback=self._progress(run_id, "mdt_chair"),
        )
        result, trace = agent.integrate(bundle)
        self._write(run_dir / f"{case_id}_mdt_chair_integration.json", result)
        self._write(
            run_dir / f"{case_id}_mdt_chair_integration_trace.json", trace
        )

    def run_discussion(
        self,
        run_id: str,
        run_dir: Path,
        case_id: str,
        config_paths: dict[str, Path],
        *,
        max_rounds: int = 3,
    ) -> None:
        """Run only the MDT discussion, starting from an existing chair result."""

        self._load_env()
        from src.agents.mdt_chair.agent import (
            MDTChairAgent,
            build_chair_prompt_bundle,
            build_semantic_evidence_catalog,
        )
        from src.agents.mdt_chair.models import MDTChairIntegration
        from src.agents.mdt_discussion.final_report import FinalReportAgent
        from src.agents.mdt_discussion.integration import (
            append_round_responses,
            move_stalled_issues_to_boundaries,
            stabilize_integration_ids,
        )
        from src.agents.mdt_discussion.models import (
            DiscussionRound,
            MDTDiscussionState,
            SpecialtyRoundResponse,
        )
        from src.agents.mdt_discussion.routing import (
            build_discussion_tasks,
            group_tasks_by_specialty,
        )
        from src.agents.mdt_discussion.specialty_agent import SpecialtyDiscussionAgent

        def read(path: Path) -> Any:
            return json.loads(path.read_text(encoding="utf-8"))

        baseline_path = run_dir / f"{case_id}_mdt_chair_integration.json"
        baseline_bytes = baseline_path.read_bytes()
        baseline = MDTChairIntegration.model_validate(json.loads(baseline_bytes))
        clinical_propositions = read(run_dir / f"{case_id}_clinical_propositions.json")
        local_graphs = read(run_dir / f"{case_id}_local_graphs.json")
        semantic_evidence = build_semantic_evidence_catalog(
            clinical_propositions,
            local_graphs,
        )
        initial_outputs = {
            specialty: read(run_dir / f"{case_id}_{specialty}_initial.json")
            for specialty in (
                "pulmonology",
                "thoracic_radiology",
                "rheumatology",
                "pathology",
            )
        }
        cumulative_outputs = initial_outputs
        state_path = run_dir / f"{case_id}_mdt_discussion_state.json"
        trace_path = run_dir / f"{case_id}_mdt_discussion_trace.json"
        state = MDTDiscussionState(
            case_id=case_id,
            baseline_sha256=sha256(baseline_bytes).hexdigest(),
            status="running",
            max_rounds=max_rounds,
            latest_chair_result=baseline.model_dump(mode="json"),
        )
        traces: dict[str, Any] = {"rounds": []}
        self._write(state_path, state)
        self.events.append(
            run_id,
            "discussion_started",
            {"max_rounds": max_rounds},
            stage="mdt_discussion",
        )
        latest = baseline
        try:
            for round_number in range(1, max_rounds + 1):
                tasks = build_discussion_tasks(
                    chair_result=latest.model_dump(mode="json"),
                    clinical_propositions=clinical_propositions,
                    local_graphs=local_graphs,
                    round_number=round_number,
                    previous_rounds=state.rounds,
                )
                if not tasks:
                    state.stop_reason = "没有仍需专科处理的问题或冲突。"
                    break
                grouped = group_tasks_by_specialty(tasks)
                task_payloads = [task.model_dump(mode="json") for task in tasks]
                state.active_round = {
                    "round_number": round_number,
                    "status": "running",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "tasks": task_payloads,
                    "task_progress": {
                        task.task_id: {
                            "status": "waiting",
                            "started_at": "",
                            "completed_at": "",
                            "answer": None,
                            "error": "",
                        }
                        for task in tasks
                    },
                    "chair_status": "waiting",
                    "chair_result": None,
                }
                self._write(state_path, state)
                self.events.append(
                    run_id,
                    "discussion_round_started",
                    {
                        "round_number": round_number,
                        "task_count": len(tasks),
                        "specialties": list(grouped),
                        "tasks": task_payloads,
                    },
                    stage="mdt_discussion",
                )
                self._write(
                    run_dir / f"{case_id}_mdt_round_{round_number:02d}_tasks.json",
                    task_payloads,
                )

                answers_by_specialty = {specialty: [] for specialty in grouped}
                round_trace: dict[str, Any] = {
                    "round_number": round_number,
                    "tasks": {},
                }
                progress_lock = Lock()

                def timestamp() -> str:
                    return datetime.now(timezone.utc).isoformat()

                def run_task(task):
                    with progress_lock:
                        progress = state.active_round["task_progress"][task.task_id]
                        progress["status"] = "running"
                        progress["started_at"] = timestamp()
                        self._write(state_path, state)
                        self.events.append(
                            run_id,
                            "discussion_task_started",
                            {
                                "round_number": round_number,
                                "task_id": task.task_id,
                                "specialty": task.specialty,
                            },
                            agent_id=task.specialty,
                            stage="mdt_discussion",
                        )
                    try:
                        config_path = config_paths[task.specialty]
                        config = load_yaml(config_path)
                        llm = build_llm_client(config)
                        agent = SpecialtyDiscussionAgent.from_config(
                            config_path,
                            llm,
                            specialty=task.specialty,
                            event_callback=self._progress(run_id, task.specialty),
                        )
                        answer, task_trace = agent.respond_to_task(
                            task=task,
                            specialty_initial_output=initial_outputs[task.specialty],
                            chair_result=latest.model_dump(mode="json"),
                        )
                    except Exception as error:
                        with progress_lock:
                            progress = state.active_round["task_progress"][task.task_id]
                            progress["status"] = "failed"
                            progress["completed_at"] = timestamp()
                            progress["error"] = str(error)
                            self._write(state_path, state)
                            self.events.append(
                                run_id,
                                "discussion_task_failed",
                                {
                                    "round_number": round_number,
                                    "task_id": task.task_id,
                                    "specialty": task.specialty,
                                    "error": str(error),
                                },
                                agent_id=task.specialty,
                                stage="mdt_discussion",
                            )
                        raise
                    with progress_lock:
                        progress = state.active_round["task_progress"][task.task_id]
                        progress["status"] = "completed"
                        progress["completed_at"] = timestamp()
                        progress["answer"] = answer.model_dump(mode="json")
                        self._write(state_path, state)
                        self.events.append(
                            run_id,
                            "discussion_task_completed",
                            {
                                "round_number": round_number,
                                "task_id": task.task_id,
                                "specialty": task.specialty,
                            },
                            agent_id=task.specialty,
                            stage="mdt_discussion",
                        )
                    return task, answer, task_trace

                with ThreadPoolExecutor(max_workers=min(len(tasks), 6)) as executor:
                    futures = [executor.submit(run_task, task) for task in tasks]
                    for future in as_completed(futures):
                        task, answer, task_trace = future.result()
                        answers_by_specialty[task.specialty].append(answer)
                        round_trace["tasks"][task.task_id] = task_trace
                task_order = {task.task_id: index for index, task in enumerate(tasks)}
                responses = []
                for specialty, specialty_tasks in grouped.items():
                    response = SpecialtyRoundResponse(
                        case_id=case_id,
                        round_number=round_number,
                        specialty=specialty,
                        answers=sorted(
                            answers_by_specialty[specialty],
                            key=lambda answer: task_order[answer.task_id],
                        ),
                    )
                    responses.append(response)
                    self._write(
                        run_dir / f"{case_id}_{specialty}_round_{round_number:02d}_response.json",
                        response,
                    )
                cumulative_outputs = append_round_responses(cumulative_outputs, responses)
                state.active_round["chair_status"] = "running"
                self._write(state_path, state)
                self.events.append(
                    run_id,
                    "discussion_chair_started",
                    {"round_number": round_number},
                    agent_id="mdt_chair",
                    stage="mdt_discussion",
                )
                bundle = build_chair_prompt_bundle(
                    case_id,
                    cumulative_outputs,
                    semantic_evidence=semantic_evidence,
                )
                chair_config = load_yaml(config_paths["mdt_chair"])
                chair_llm = build_llm_client(chair_config)
                chair = MDTChairAgent.from_config(
                    config_paths["mdt_chair"],
                    chair_llm,
                    event_callback=self._progress(run_id, "mdt_chair"),
                )
                updated, chair_trace = chair.integrate(
                    bundle,
                    discussion_previous=latest,
                    discussion_responses=responses,
                )
                updated = stabilize_integration_ids(updated, latest)
                updated = move_stalled_issues_to_boundaries(
                    updated,
                    state.rounds,
                    responses,
                )
                updated = stabilize_integration_ids(updated, latest)
                latest = updated
                discussion_round = DiscussionRound(
                    round_number=round_number,
                    tasks=tasks,
                    specialty_responses=responses,
                    chair_result=latest.model_dump(mode="json"),
                )
                state.rounds.append(discussion_round)
                state.latest_chair_result = latest.model_dump(mode="json")
                state.active_round = None
                round_trace["chair"] = chair_trace
                traces["rounds"].append(round_trace)
                self._write(
                    run_dir / f"{case_id}_mdt_round_{round_number:02d}_chair.json",
                    latest,
                )
                self._write(state_path, state)
                self._write(trace_path, traces)
                self.events.append(
                    run_id,
                    "discussion_round_completed",
                    {"round_number": round_number},
                    stage="mdt_discussion",
                )
            else:
                state.stop_reason = "已达到最多三轮讨论。"

            if not state.stop_reason:
                state.stop_reason = "讨论已结束。"
            state.report_status = "running"
            self._write(state_path, state)
            self.events.append(
                run_id,
                "discussion_report_started",
                {"discussion_rounds": len(state.rounds)},
                agent_id="mdt_chair",
                stage="mdt_discussion",
            )
            report_config = load_yaml(config_paths["mdt_chair"])
            report_llm = build_llm_client(report_config)
            report_agent = FinalReportAgent.from_config(
                config_paths["mdt_chair"],
                report_llm,
                event_callback=self._progress(run_id, "mdt_chair"),
            )
            report, report_trace = report_agent.generate(
                case_id=case_id,
                chair_result=latest.model_dump(mode="json"),
                rounds=state.rounds,
                stop_reason=state.stop_reason,
            )
            state.final_report = report
            state.status = "completed"
            state.report_status = "completed"
            traces["final_report"] = report_trace
            self._write(run_dir / f"{case_id}_mdt_final_report.json", report)
            self._write(state_path, state)
            self._write(trace_path, traces)
            self.events.append(
                run_id,
                "discussion_completed",
                {"discussion_rounds": len(state.rounds)},
                stage="mdt_discussion",
            )
        except Exception as error:
            state.status = "failed"
            if state.active_round and state.active_round.get("chair_status") == "running":
                state.active_round["chair_status"] = "failed"
            if state.report_status == "running":
                state.report_status = "failed"
            state.error = str(error)
            self._write(state_path, state)
            self._write(trace_path, traces)
            self.events.append(
                run_id,
                "discussion_failed",
                {"error": str(error)},
                stage="mdt_discussion",
            )
            raise

    def _progress(self, run_id: str, agent_id: str) -> Callable[[str, dict], None]:
        def callback(event: str, payload: dict) -> None:
            safe_payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
            self.events.append(
                run_id,
                "agent_event",
                {"event": event, **safe_payload},
                agent_id=agent_id,
                stage=str(payload.get("stage") or event),
            )

        return callback

    def _load_env(self) -> None:
        path = self.root / ".env"
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() and key.strip() not in os.environ:
                os.environ[key.strip()] = value.strip().strip('"').strip("'")

    @staticmethod
    def _write(path: Path, value: Any) -> None:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
