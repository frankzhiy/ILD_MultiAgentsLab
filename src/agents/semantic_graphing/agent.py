from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.agents.semantic_graphing.document_classifier import DocumentClassifier
from src.agents.semantic_graphing.schema_registry import ModalitySchemaRegistry
from src.agents.semantic_graphing.subgraph_builder import SubgraphBuilder
from src.llm.base import LLMClient
from src.schemas.graph import SemanticSubgraph
from src.schemas.semantic_graphing import DocumentClassification, SourceType
from src.utils.config import load_yaml


@dataclass
class SemanticGraphingRunResult:
    case_id: str
    classification: DocumentClassification
    subgraphs: list[SemanticSubgraph]
    trace: dict[str, Any]


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
        subgraph_builder: SubgraphBuilder,
        schema_registry: ModalitySchemaRegistry,
    ) -> None:
        self.classifier = classifier
        self.subgraph_builder = subgraph_builder
        self.schema_registry = schema_registry

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
            subgraph_builder=SubgraphBuilder(
                llm,
                config["subgraph_prompt"],
                temperature=temperature,
                max_tokens=max_tokens,
            ),
            schema_registry=ModalitySchemaRegistry.from_dir(config["modality_schema_dir"]),
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
            source_types=[str(item) for item in classification.detected_source_types],
        )

        return ClassificationRunResult(
            case_id=case_id,
            classification=classification,
            trace={
                "case_id": case_id,
                "classification": classification_trace,
                "registered_source_types": self.schema_registry.source_types(),
            },
        )

    def build_subgraphs(
        self,
        classification: DocumentClassification,
        *,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> tuple[list[SemanticSubgraph], dict[str, Any]]:
        def report(event: str, **payload: Any) -> None:
            if progress:
                progress(event, payload)

        subgraphs: list[SemanticSubgraph] = []
        subgraph_traces: list[dict[str, Any]] = []
        skipped_segments: list[dict[str, str]] = []
        graphable_segments = [
            segment
            for segment in classification.segments
            if segment.source_type not in {SourceType.UNKNOWN, SourceType.MIXED}
        ]
        for index, segment in enumerate(classification.segments, start=1):
            if segment.source_type in {SourceType.UNKNOWN, SourceType.MIXED}:
                skipped_segments.append(
                    {
                        "segment_id": segment.segment_id,
                        "source_type": str(segment.source_type),
                        "reason": "No graph construction schema is applied to unknown or mixed segments.",
                    }
                )
                continue
            try:
                schema = self.schema_registry.get(segment.source_type)
            except KeyError as exc:
                skipped_segments.append(
                    {
                        "segment_id": segment.segment_id,
                        "source_type": str(segment.source_type),
                        "reason": str(exc),
                    }
                )
                continue

            graphable_index = sum(
                1
                for previous in classification.segments[:index]
                if previous.source_type not in {SourceType.UNKNOWN, SourceType.MIXED}
            )
            report(
                "subgraph_started",
                segment_id=segment.segment_id,
                source_type=str(segment.source_type),
                index=graphable_index,
                total=len(graphable_segments),
            )
            subgraph, subgraph_trace = self.subgraph_builder.build(segment, schema)
            subgraphs.append(subgraph)
            subgraph_traces.append(subgraph_trace)
            report(
                "subgraph_completed",
                segment_id=segment.segment_id,
                source_type=str(segment.source_type),
                node_count=len(subgraph.nodes),
                edge_count=len(subgraph.edges),
            )

        return subgraphs, {
            "subgraphs": subgraph_traces,
            "skipped_segments": skipped_segments,
            "registered_source_types": self.schema_registry.source_types(),
        }
