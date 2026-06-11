from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable

from src.agents.semantic_graphing.clinical_proposition_extractor import (
    ClinicalPropositionExtractor,
)
from src.agents.semantic_graphing.clinical_proposition_validator import (
    ClinicalPropositionValidator,
)
from src.agents.semantic_graphing.document_classifier import DocumentClassifier
from src.agents.semantic_graphing.graph_unit_extractor import SegmentGraphUnitExtractor
from src.agents.semantic_graphing.primary_frame_selector import PrimaryFrameSelector

from src.llm.base import LLMClient
from src.schemas.semantic_graphing import (
    DocumentClassification,
    DocumentClinicalPropositions,
    DocumentGraphUnits,
    DocumentPrimaryFrames,
    DocumentPropositionValidation,
    GraphUnitClinicalPropositions,
    GraphUnitPrimaryFrame,
    SegmentClinicalPropositions,
    SegmentGraphUnits,
    SegmentPrimaryFrames,
)
from src.utils.config import load_yaml


@dataclass
class ClassificationRunResult:
    case_id: str
    classification: DocumentClassification
    trace: dict[str, Any]


class SemanticGraphingAgent:
    def __init__(
        self,
        *,
        classifier: DocumentClassifier,
        graph_unit_extractor: SegmentGraphUnitExtractor,
        primary_frame_selector: PrimaryFrameSelector,
        clinical_proposition_extractor: ClinicalPropositionExtractor,
        clinical_proposition_validator: ClinicalPropositionValidator | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        if max_concurrency is not None and max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        self.classifier = classifier
        self.graph_unit_extractor = graph_unit_extractor
        self.primary_frame_selector = primary_frame_selector
        self.clinical_proposition_extractor = clinical_proposition_extractor
        self.max_concurrency = max_concurrency
        self.clinical_proposition_validator = (
            clinical_proposition_validator or ClinicalPropositionValidator()
        )

    @classmethod
    def from_config(cls, config_path: str | Path, llm: LLMClient) -> "SemanticGraphingAgent":
        config = load_yaml(config_path)
        temperature = float(config.get("temperature", 0.1))
        max_tokens = int(config.get("max_tokens", 6000))
        max_attempts = int(config.get("max_attempts", 2))
        retry_backoff_seconds = float(config.get("retry_backoff_seconds", 2))

        def stage_llm(stage: str) -> LLMClient:
            model = str(config.get(f"{stage}_model", config.get("model", "")))
            with_model = getattr(llm, "with_model", None)
            return with_model(model) if model and callable(with_model) else llm

        return cls(
            classifier=DocumentClassifier(
                stage_llm("classification"),
                config["classification_prompt"],
                temperature=temperature,
                max_tokens=int(config.get("classification_max_tokens", max_tokens)),
                max_attempts=int(config.get("classification_max_attempts", max_attempts)),
                retry_backoff_seconds=retry_backoff_seconds,
            ),
            graph_unit_extractor=SegmentGraphUnitExtractor(
                stage_llm("graph_unit"),
                config.get(
                    "graph_unit_prompt",
                    "src/prompts/semantic_graphing/graph_unit_extraction.md",
                ),
                temperature=temperature,
                max_tokens=int(config.get("graph_unit_max_tokens", max_tokens)),
                max_attempts=int(config.get("graph_unit_max_attempts", max_attempts)),
                retry_backoff_seconds=retry_backoff_seconds,
            ),
            primary_frame_selector=PrimaryFrameSelector(
                stage_llm("primary_frame"),
                config.get(
                    "primary_frame_prompt",
                    "src/prompts/semantic_graphing/primary_frame_selection.md",
                ),
                temperature=temperature,
                max_tokens=int(config.get("primary_frame_max_tokens", max_tokens)),
                max_attempts=int(config.get("primary_frame_max_attempts", max_attempts)),
                retry_backoff_seconds=retry_backoff_seconds,
            ),
            clinical_proposition_extractor=ClinicalPropositionExtractor(
                stage_llm("clinical_proposition"),
                config.get(
                    "clinical_proposition_prompt",
                    "src/prompts/semantic_graphing/clinical_proposition_extraction.md",
                ),
                temperature=temperature,
                max_tokens=int(config.get("clinical_proposition_max_tokens", max_tokens)),
                max_attempts=int(config.get("clinical_proposition_max_attempts", max_attempts)),
                retry_backoff_seconds=retry_backoff_seconds,
                max_chunk_chars=int(config.get("clinical_proposition_max_chunk_chars", 300)),
                enable_chunking=bool(config.get("clinical_proposition_enable_chunking", False)),
            ),
            clinical_proposition_validator=ClinicalPropositionValidator(),
            max_concurrency=(
                int(config["max_concurrency"]) if config.get("max_concurrency") is not None else None
            ),
        )

    def _worker_count(self, task_count: int) -> int:
        if self.max_concurrency is None:
            return max(1, task_count)
        return max(1, min(task_count, self.max_concurrency))

    def classify(
        self,
        input_text: str,
        *,
        case_id: str,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> ClassificationRunResult:
        def report(event: str, **payload: Any) -> None:
            if progress:
                progress(event, payload)

        report("classification_started")
        classification, classification_trace = self.classifier.classify(input_text)
        report(
            "classification_completed",
            segment_count=len(classification.segments),
            contained_source_types=[
                str(item) for item in classification.detected_contained_source_types
            ],
        )

        return ClassificationRunResult(
            case_id=case_id,
            classification=classification,
            trace={
                "case_id": case_id,
                "classification": classification_trace,
            },
        )

    def extract_graph_units(
        self,
        classification: DocumentClassification,
        *,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
        cache_dir: str | Path | None = None,
    ) -> tuple[DocumentGraphUnits, dict[str, Any]]:
        def report(event: str, **payload: Any) -> None:
            if progress:
                progress(event, payload)

        segments = list(classification.segments)
        worker_count = self._worker_count(len(segments))
        report(
            "graph_unit_extraction_started",
            segment_count=len(segments),
            concurrent_tasks=worker_count,
        )

        indexed_results: dict[int, Any] = {}
        indexed_traces: dict[int, dict[str, Any]] = {}
        pending_segments = []
        for index, segment in enumerate(segments):
            cached = _read_task_cache(cache_dir, segment.segment_id, SegmentGraphUnits)
            if cached is None:
                pending_segments.append((index, segment))
                continue
            graph_units, trace = cached
            indexed_results[index] = graph_units
            indexed_traces[index] = {"segment_id": segment.segment_id, "trace": trace}
            report(
                "graph_unit_segment_done",
                segment_id=segment.segment_id,
                index=index + 1,
                total=len(segments),
                graph_unit_count=len(graph_units.graph_units),
                cached=True,
            )

        with ThreadPoolExecutor(max_workers=self._worker_count(len(pending_segments))) as executor:
            futures = {
                executor.submit(self.graph_unit_extractor.extract, segment): (index, segment)
                for index, segment in pending_segments
            }
            first_error: RuntimeError | None = None
            for future in as_completed(futures):
                index, segment = futures[future]
                try:
                    graph_units, trace = future.result()
                except Exception as exc:
                    if first_error is None:
                        first_error = RuntimeError(
                            f"Graph-unit extraction failed for {segment.segment_id}: {exc}"
                        )
                        first_error.__cause__ = exc
                    continue
                indexed_results[index] = graph_units
                indexed_traces[index] = {
                    "segment_id": segment.segment_id,
                    "trace": trace,
                }
                _write_task_cache(cache_dir, segment.segment_id, graph_units, trace)
                report(
                    "graph_unit_segment_done",
                    segment_id=segment.segment_id,
                    index=index + 1,
                    total=len(segments),
                    graph_unit_count=len(graph_units.graph_units),
                )
        if first_error is not None:
            raise first_error

        ordered_results = [indexed_results[index] for index in range(len(segments))]
        ordered_traces = [indexed_traces[index] for index in range(len(segments))]
        document_graph_units = DocumentGraphUnits(segments=ordered_results)
        graph_unit_count = sum(len(item.graph_units) for item in ordered_results)
        report(
            "graph_unit_extraction_completed",
            segment_count=len(ordered_results),
            graph_unit_count=graph_unit_count,
        )

        return document_graph_units, {
            "segments": ordered_traces,
            "concurrent_tasks": worker_count,
        }

    def select_primary_frames(
        self,
        graph_units: DocumentGraphUnits,
        *,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
        cache_dir: str | Path | None = None,
    ) -> tuple[DocumentPrimaryFrames, dict[str, Any]]:
        def report(event: str, **payload: Any) -> None:
            if progress:
                progress(event, payload)

        segments = list(graph_units.segments)
        unit_total = sum(len(item.graph_units) for item in segments)
        worker_count = self._worker_count(unit_total)
        report(
            "primary_frame_selection_started",
            segment_count=len(segments),
            unit_count=unit_total,
            concurrent_tasks=worker_count,
        )

        indexed_results: dict[tuple[int, int], Any] = {}
        indexed_traces: dict[tuple[int, int], dict[str, Any]] = {}
        pending_units = []
        for segment_index, segment in enumerate(segments):
            for unit_index, unit in enumerate(segment.graph_units):
                cached = _read_task_cache(cache_dir, unit.graph_unit_id, GraphUnitPrimaryFrame)
                if cached is None:
                    pending_units.append((segment_index, unit_index, segment, unit))
                    continue
                selection, trace = cached
                key = (segment_index, unit_index)
                indexed_results[key] = selection
                indexed_traces[key] = {"graph_unit_id": unit.graph_unit_id, "trace": trace}
                report(
                    "primary_frame_selection_unit_done",
                    segment_id=segment.segment_id,
                    graph_unit_id=unit.graph_unit_id,
                    completed=len(indexed_results),
                    total=unit_total,
                    primary_frame=str(selection.primary_frame),
                    has_boundary_warning=selection.boundary_warning is not None,
                    cached=True,
                )

        with ThreadPoolExecutor(max_workers=self._worker_count(len(pending_units))) as executor:
            futures = {
                executor.submit(self.primary_frame_selector.select_unit, unit): (
                    segment_index,
                    unit_index,
                    segment,
                    unit,
                )
                for segment_index, unit_index, segment, unit in pending_units
            }
            first_error: RuntimeError | None = None
            for future in as_completed(futures):
                segment_index, unit_index, segment, unit = futures[future]
                try:
                    selection, trace = future.result()
                except Exception as exc:
                    if first_error is None:
                        first_error = RuntimeError(
                            f"Primary-frame selection failed for {unit.graph_unit_id}: {exc}"
                        )
                        first_error.__cause__ = exc
                    continue
                key = (segment_index, unit_index)
                indexed_results[key] = selection
                indexed_traces[key] = {
                    "graph_unit_id": unit.graph_unit_id,
                    "trace": trace,
                }
                _write_task_cache(cache_dir, unit.graph_unit_id, selection, trace)
                report(
                    "primary_frame_selection_unit_done",
                    segment_id=segment.segment_id,
                    graph_unit_id=unit.graph_unit_id,
                    completed=len(indexed_results),
                    total=unit_total,
                    primary_frame=str(selection.primary_frame),
                    has_boundary_warning=selection.boundary_warning is not None,
                )
        if first_error is not None:
            raise first_error

        ordered_results = [
            SegmentPrimaryFrames(
                segment_id=segment.segment_id,
                units=[
                    indexed_results[(segment_index, unit_index)]
                    for unit_index in range(len(segment.graph_units))
                ],
            )
            for segment_index, segment in enumerate(segments)
        ]
        ordered_traces = [
            {
                "segment_id": segment.segment_id,
                "units": [
                    indexed_traces[(segment_index, unit_index)]
                    for unit_index in range(len(segment.graph_units))
                ],
            }
            for segment_index, segment in enumerate(segments)
        ]
        document_primary_frames = DocumentPrimaryFrames(segments=ordered_results)
        boundary_warning_count = sum(
            unit.boundary_warning is not None for item in ordered_results for unit in item.units
        )
        report(
            "primary_frame_selection_completed",
            segment_count=len(ordered_results),
            unit_count=unit_total,
            boundary_warning_count=boundary_warning_count,
        )

        return document_primary_frames, {
            "segments": ordered_traces,
            "concurrent_tasks": worker_count,
        }

    def extract_clinical_propositions(
        self,
        graph_units: DocumentGraphUnits,
        primary_frames: DocumentPrimaryFrames,
        *,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
        cache_dir: str | Path | None = None,
    ) -> tuple[DocumentClinicalPropositions, dict[str, Any]]:
        def report(event: str, **payload: Any) -> None:
            if progress:
                progress(event, payload)

        primary_frame_units = [
            unit for segment in primary_frames.segments for unit in segment.units
        ]
        primary_frame_by_unit = {unit.graph_unit_id: unit for unit in primary_frame_units}
        if len(primary_frame_by_unit) != len(primary_frame_units):
            raise ValueError("Primary-frame selections contain duplicate graph_unit_id values")
        segments = list(graph_units.segments)
        unit_total = sum(len(item.graph_units) for item in segments)
        graph_unit_id_list = [
            unit.graph_unit_id for segment in segments for unit in segment.graph_units
        ]
        graph_unit_ids = set(graph_unit_id_list)
        if len(graph_unit_ids) != len(graph_unit_id_list):
            raise ValueError("Graph units contain duplicate graph_unit_id values")
        if set(primary_frame_by_unit) != graph_unit_ids:
            missing = sorted(graph_unit_ids - set(primary_frame_by_unit))
            extra = sorted(set(primary_frame_by_unit) - graph_unit_ids)
            raise ValueError(
                f"Primary-frame and graph-unit IDs do not align; missing={missing}, extra={extra}"
            )

        worker_count = self._worker_count(unit_total)
        report(
            "clinical_proposition_extraction_started",
            segment_count=len(segments),
            unit_count=unit_total,
            concurrent_tasks=worker_count,
        )

        indexed_results: dict[tuple[int, int], Any] = {}
        indexed_traces: dict[tuple[int, int], dict[str, Any]] = {}
        pending_units = []
        for segment_index, segment in enumerate(segments):
            for unit_index, unit in enumerate(segment.graph_units):
                cached = _read_task_cache(
                    cache_dir,
                    unit.graph_unit_id,
                    GraphUnitClinicalPropositions,
                )
                if cached is None:
                    pending_units.append((segment_index, unit_index, segment, unit))
                    continue
                propositions, trace = cached
                key = (segment_index, unit_index)
                indexed_results[key] = propositions
                indexed_traces[key] = {"graph_unit_id": unit.graph_unit_id, "trace": trace}
                report(
                    "clinical_proposition_extraction_unit_done",
                    segment_id=segment.segment_id,
                    graph_unit_id=unit.graph_unit_id,
                    completed=len(indexed_results),
                    total=unit_total,
                    proposition_count=len(propositions.propositions),
                    modifier_count=len(propositions.event_modifiers)
                    + sum(len(item.modifiers) for item in propositions.propositions),
                    cached=True,
                )

        with ThreadPoolExecutor(max_workers=self._worker_count(len(pending_units))) as executor:
            futures = {
                executor.submit(
                    self.clinical_proposition_extractor.extract_unit,
                    unit,
                    primary_frame_by_unit[unit.graph_unit_id],
                    None if cache_dir is None else Path(cache_dir) / "chunks",
                ): (segment_index, unit_index, segment, unit)
                for segment_index, unit_index, segment, unit in pending_units
            }
            first_error: RuntimeError | None = None
            for future in as_completed(futures):
                segment_index, unit_index, segment, unit = futures[future]
                try:
                    propositions, trace = future.result()
                except Exception as exc:
                    if first_error is None:
                        first_error = RuntimeError(
                            f"Clinical-proposition extraction failed for {unit.graph_unit_id}: {exc}"
                        )
                        first_error.__cause__ = exc
                    continue
                key = (segment_index, unit_index)
                indexed_results[key] = propositions
                indexed_traces[key] = {
                    "graph_unit_id": unit.graph_unit_id,
                    "trace": trace,
                }
                _write_task_cache(cache_dir, unit.graph_unit_id, propositions, trace)
                report(
                    "clinical_proposition_extraction_unit_done",
                    segment_id=segment.segment_id,
                    graph_unit_id=unit.graph_unit_id,
                    completed=len(indexed_results),
                    total=unit_total,
                    proposition_count=len(propositions.propositions),
                    modifier_count=len(propositions.event_modifiers)
                    + sum(len(item.modifiers) for item in propositions.propositions),
                )
        if first_error is not None:
            raise first_error

        ordered_results = [
            SegmentClinicalPropositions(
                segment_id=segment.segment_id,
                units=[
                    indexed_results[(segment_index, unit_index)]
                    for unit_index in range(len(segment.graph_units))
                ],
            )
            for segment_index, segment in enumerate(segments)
        ]
        ordered_traces = [
            {
                "segment_id": segment.segment_id,
                "units": [
                    indexed_traces[(segment_index, unit_index)]
                    for unit_index in range(len(segment.graph_units))
                ],
            }
            for segment_index, segment in enumerate(segments)
        ]
        document_propositions = DocumentClinicalPropositions(segments=ordered_results)
        proposition_count = sum(
            len(unit.propositions) for segment in ordered_results for unit in segment.units
        )
        modifier_count = sum(
            len(unit.event_modifiers)
            + sum(len(proposition.modifiers) for proposition in unit.propositions)
            for segment in ordered_results
            for unit in segment.units
        )
        report(
            "clinical_proposition_extraction_completed",
            segment_count=len(ordered_results),
            unit_count=unit_total,
            proposition_count=proposition_count,
            modifier_count=modifier_count,
        )

        return document_propositions, {
            "segments": ordered_traces,
            "concurrent_tasks": worker_count,
        }

    def validate_clinical_propositions(
        self,
        graph_units: DocumentGraphUnits,
        primary_frames: DocumentPrimaryFrames,
        clinical_propositions: DocumentClinicalPropositions,
        *,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> DocumentPropositionValidation:
        def report(event: str, **payload: Any) -> None:
            if progress:
                progress(event, payload)

        report("clinical_proposition_validation_started")
        validation = self.clinical_proposition_validator.validate_document(
            graph_units,
            primary_frames,
            clinical_propositions,
        )
        report(
            "clinical_proposition_validation_completed",
            unit_count=validation.summary.unit_count,
            graph_ready_unit_count=validation.summary.graph_ready_unit_count,
            error_count=validation.summary.error_count,
            warning_count=validation.summary.warning_count,
            info_count=validation.summary.info_count,
        )
        return validation


def _read_task_cache(
    cache_dir: str | Path | None,
    task_id: str,
    model: type,
) -> tuple[Any, dict[str, Any]] | None:
    if cache_dir is None:
        return None
    path = Path(cache_dir) / f"{task_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return model.model_validate(data["result"]), data.get("trace", {"cached": True})


def _write_task_cache(
    cache_dir: str | Path | None,
    task_id: str,
    result: Any,
    trace: dict[str, Any],
) -> None:
    if cache_dir is None:
        return
    path = Path(cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{task_id}.json").write_text(
        json.dumps(
            {"result": result.model_dump(), "trace": trace},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
