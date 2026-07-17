from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from src.agents.common.specialty_input import build_specialty_case_input
from src.agents.semantic_graphing.agent import SemanticGraphingAgent
from src.llm.factory import build_llm_client
from src.llm.prompting import llm_value
from src.reporting.html_report import render_report
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
        render_report(
            result,
            graph_units,
            source_filename=input_path.name,
            raw_text=input_text,
            timing=timing,
            output_path=run_dir / f"{case_id}_report.html",
            primary_frames=primary_frames,
            clinical_propositions=clinical_propositions,
            proposition_validation=validation,
            local_graphs=local_graphs,
        )

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
            from src.reporting.pulmonology_report import render_pulmonology_report

            working = build_specialty_working_input(case)
            self._write(run_dir / f"{stem}_working_input.json", working)
            self._write(
                run_dir / f"{stem}_evidence_input.json",
                llm_value(build_specialty_evidence_prompt_input(case)),
            )
            agent = PulmonologyAgent.from_config(
                config_path, llm, event_callback=callback
            )
            result, trace = agent.initial_assessment(case)
            report = render_pulmonology_report
        elif specialty == MdtSpecialty.RHEUMATOLOGY.value:
            from src.agents.common.evidence_projection import (
                build_specialty_evidence_prompt_input,
                build_specialty_working_input,
            )
            from src.agents.rheumatology.agent import RheumatologyAgent
            from src.reporting.rheumatology_report import render_rheumatology_report

            working = build_specialty_working_input(case)
            self._write(run_dir / f"{stem}_working_input.json", working)
            self._write(
                run_dir / f"{stem}_evidence_input.json",
                llm_value(build_specialty_evidence_prompt_input(case)),
            )
            agent = RheumatologyAgent.from_config(
                config_path, llm, event_callback=callback
            )
            result, trace = agent.initial_assessment(case)
            report = render_rheumatology_report
        elif specialty == MdtSpecialty.PATHOLOGY.value:
            from src.agents.common.evidence_projection import (
                build_specialty_evidence_prompt_input,
                build_specialty_working_input,
            )
            from src.agents.pathology.agent import PathologyAgent
            from src.reporting.pathology_report import render_pathology_report

            working = build_specialty_working_input(case)
            self._write(run_dir / f"{stem}_working_input.json", working)
            self._write(
                run_dir / f"{stem}_evidence_input.json",
                llm_value(build_specialty_evidence_prompt_input(case)),
            )
            agent = PathologyAgent.from_config(config_path, llm, event_callback=callback)
            result, trace = agent.initial_assessment(case)
            report = render_pathology_report
        elif specialty == MdtSpecialty.THORACIC_RADIOLOGY.value:
            from src.agents.thoracic_radiology.agent import ThoracicRadiologyAgent
            from src.agents.thoracic_radiology.evidence_projection import (
                build_radiology_evidence_prompt_input,
                build_radiology_reconstruction_prompt_input,
                build_radiology_working_input,
            )
            from src.reporting.thoracic_radiology_report import (
                render_thoracic_radiology_report,
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
            result, trace = agent.initial_assessment(case)
            report = render_thoracic_radiology_report
        else:  # pragma: no cover - enum validation guards this
            raise ValueError(f"Unsupported specialty: {specialty}")

        self._write(run_dir / f"{stem}_initial.json", result)
        self._write(run_dir / f"{stem}_initial_trace.json", trace)
        report(result, case, run_dir / f"{stem}_initial.html")

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
            SPECIALTIES,
            build_chair_prompt_bundle,
            build_semantic_evidence_catalog,
        )
        from src.reporting.mdt_chair_report import render_mdt_chair_report

        outputs = {
            specialty: self._read(run_dir / f"{case_id}_{specialty}_initial.json")
            for specialty in SPECIALTIES
        }
        input_summaries = {
            specialty: self._read(
                run_dir / f"{case_id}_{specialty}_input.json"
            ).get("summary", {})
            for specialty in SPECIALTIES
        }
        semantic_evidence = build_semantic_evidence_catalog(
            self._read(run_dir / f"{case_id}_clinical_propositions.json"),
            self._read(run_dir / f"{case_id}_local_graphs.json"),
        )
        bundle = build_chair_prompt_bundle(
            case_id, outputs, input_summaries, semantic_evidence
        )
        self._write(run_dir / f"{case_id}_mdt_chair_prompt_input.json", bundle.prompt_input)
        config = load_yaml(config_path)
        agent = MDTChairAgent.from_config(
            config_path,
            build_llm_client(config),
            event_callback=self._progress(run_id, "mdt_chair"),
        )
        result, trace = agent.synthesize(bundle)
        self._write(run_dir / f"{case_id}_mdt_chair_initial.json", result)
        self._write(run_dir / f"{case_id}_mdt_chair_initial_trace.json", trace)
        render_mdt_chair_report(result, run_dir / f"{case_id}_mdt_chair_initial.html")

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

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))
