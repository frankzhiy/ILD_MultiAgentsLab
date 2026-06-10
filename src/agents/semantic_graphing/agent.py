from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.agents.semantic_graphing.document_classifier import DocumentClassifier
from src.agents.semantic_graphing.frame_triage import FrameTriager
from src.agents.semantic_graphing.graph_unit_extractor import SegmentGraphUnitExtractor

from src.llm.base import LLMClient
from src.schemas.semantic_graphing import (
    DocumentClassification,
    DocumentFrameTriage,
    DocumentGraphUnits,
    SegmentFrameTriage,
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
        frame_triager: FrameTriager,

    ) -> None:
        self.classifier = classifier
        self.graph_unit_extractor = graph_unit_extractor
        self.frame_triager = frame_triager


    @classmethod
    def from_config(cls, config_path: str | Path, llm: LLMClient) -> "SemanticGraphingAgent":
        config = load_yaml(config_path)
        temperature = float(config.get("temperature", 0.1))
        max_tokens = int(config.get("max_tokens", 6000))
        return cls(
            classifier=DocumentClassifier(
                llm,
                config["classification_prompt"],
                temperature=temperature,
                max_tokens=max_tokens,
            ),
            graph_unit_extractor=SegmentGraphUnitExtractor(
                llm,
                config.get(
                    "graph_unit_prompt",
                    "src/prompts/semantic_graphing/graph_unit_extraction.md",
                ),
                temperature=temperature,
                max_tokens=max_tokens,
            ),
            frame_triager=FrameTriager(
                llm,
                config.get(
                    "frame_triage_prompt",
                    "src/prompts/semantic_graphing/frame_triage.md",
                ),
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )

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
    ) -> tuple[DocumentGraphUnits, dict[str, Any]]:
        def report(event: str, **payload: Any) -> None:
            if progress:
                progress(event, payload)

        segments = list(classification.segments)
        worker_count = max(1, len(segments))
        report(
            "graph_unit_extraction_started",
            segment_count=len(segments),
            concurrent_tasks=len(segments),
        )

        indexed_results: dict[int, Any] = {}
        indexed_traces: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(self.graph_unit_extractor.extract, segment): (index, segment)
                for index, segment in enumerate(segments)
            }
            for future in as_completed(futures):
                index, segment = futures[future]
                try:
                    graph_units, trace = future.result()
                except Exception as exc:
                    raise RuntimeError(
                        f"Graph-unit extraction failed for {segment.segment_id}: {exc}"
                    ) from exc
                indexed_results[index] = graph_units
                indexed_traces[index] = {
                    "segment_id": segment.segment_id,
                    "trace": trace,
                }
                report(
                    "graph_unit_segment_done",
                    segment_id=segment.segment_id,
                    index=index + 1,
                    total=len(segments),
                    graph_unit_count=len(graph_units.graph_units),
                )

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
            "concurrent_tasks": len(segments),
        }

    def triage_frames(
        self,
        graph_units: DocumentGraphUnits,
        *,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> tuple[DocumentFrameTriage, dict[str, Any]]:
        def report(event: str, **payload: Any) -> None:
            if progress:
                progress(event, payload)

        segments = list(graph_units.segments)
        unit_total = sum(len(item.graph_units) for item in segments)
        worker_count = max(1, unit_total)
        report(
            "frame_triage_started",
            segment_count=len(segments),
            unit_count=unit_total,
            concurrent_tasks=unit_total,
        )

        indexed_results: dict[tuple[int, int], Any] = {}
        indexed_traces: dict[tuple[int, int], dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(self.frame_triager.triage_unit, unit): (
                    segment_index,
                    unit_index,
                    segment,
                    unit,
                )
                for segment_index, segment in enumerate(segments)
                for unit_index, unit in enumerate(segment.graph_units)
            }
            for future in as_completed(futures):
                segment_index, unit_index, segment, unit = futures[future]
                try:
                    triage, trace = future.result()
                except Exception as exc:
                    raise RuntimeError(
                        f"Frame triage failed for {unit.graph_unit_id}: {exc}"
                    ) from exc
                key = (segment_index, unit_index)
                indexed_results[key] = triage
                indexed_traces[key] = {
                    "graph_unit_id": unit.graph_unit_id,
                    "trace": trace,
                }
                report(
                    "frame_triage_unit_done",
                    segment_id=segment.segment_id,
                    graph_unit_id=unit.graph_unit_id,
                    completed=len(indexed_results),
                    total=unit_total,
                    triggered_frame_count=len(triage.triggered_frames),
                )

        ordered_results = [
            SegmentFrameTriage(
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
        document_triage = DocumentFrameTriage(segments=ordered_results)
        triggered_frame_count = sum(
            len(unit.triggered_frames)
            for item in ordered_results
            for unit in item.units
        )
        report(
            "frame_triage_completed",
            segment_count=len(ordered_results),
            unit_count=unit_total,
            triggered_frame_count=triggered_frame_count,
        )

        return document_triage, {
            "segments": ordered_traces,
            "concurrent_tasks": unit_total,
        }
