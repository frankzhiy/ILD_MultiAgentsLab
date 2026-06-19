"""Build local evidence graphs without adding semantics beyond extracted propositions."""

from collections import defaultdict

from src.schemas.semantic_graphing.clinical_proposition import (
    DocumentClinicalPropositions,
    EvidenceReference,
)
from src.schemas.semantic_graphing.graph_unit import DocumentGraphUnits
from src.schemas.semantic_graphing.local_graph import (
    DocumentLocalGraphs,
    GraphEdgeType,
    GraphNodeType,
    GraphUnitLocalGraph,
    LocalGraphBuildIssue,
    LocalGraphBuildStatus,
    LocalGraphEdge,
    LocalGraphNode,
    LocalGraphSummary,
    SegmentLocalGraphs,
)
from src.schemas.semantic_graphing.primary_frame import DocumentPrimaryFrames
from src.schemas.semantic_graphing.proposition_validation import (
    DocumentPropositionValidation,
)


class LocalGraphBuilder:
    """Deterministically compile validated unit propositions into local graphs."""

    def build_document(
        self,
        graph_units: DocumentGraphUnits,
        primary_frames: DocumentPrimaryFrames,
        clinical_propositions: DocumentClinicalPropositions,
        proposition_validation: DocumentPropositionValidation,
    ) -> DocumentLocalGraphs:
        frames = _index_units(primary_frames.segments)
        propositions = _index_units(clinical_propositions.segments)
        validations = _index_units(proposition_validation.segments)

        segments: list[SegmentLocalGraphs] = []
        for segment in graph_units.segments:
            local_graphs = []
            for unit in segment.graph_units:
                unit_id = unit.graph_unit_id
                try:
                    frame = frames[unit_id]
                    unit_propositions = propositions[unit_id]
                    validation = validations[unit_id]
                    if not validation.is_graph_ready:
                        issues = [
                            LocalGraphBuildIssue(code=issue.code, message=issue.message)
                            for issue in validation.issues
                            if str(issue.severity) == "error"
                        ]
                        local_graphs.append(
                            _blocked_graph(
                                unit_id,
                                segment.segment_id,
                                frame.primary_frame,
                                unit_propositions.evidence_blocks,
                                issues or [
                                    LocalGraphBuildIssue(
                                        code="proposition_validation_blocked",
                                        message="Proposition validation marked this unit as not graph-ready.",
                                    )
                                ],
                            )
                        )
                        continue
                    local_graphs.append(
                        self.build_unit(unit, frame, unit_propositions)
                    )
                except Exception as exc:
                    frame = frames.get(unit_id)
                    unit_propositions = propositions.get(unit_id)
                    local_graphs.append(
                        _blocked_graph(
                            unit_id,
                            segment.segment_id,
                            frame.primary_frame if frame is not None else "background_context",
                            (
                                unit_propositions.evidence_blocks
                                if unit_propositions is not None
                                else []
                            ),
                            [
                                LocalGraphBuildIssue(
                                    code="local_graph_build_failed",
                                    message=str(exc),
                                )
                            ],
                        )
                    )
            segments.append(SegmentLocalGraphs(segment_id=segment.segment_id, units=local_graphs))

        all_graphs = [graph for segment in segments for graph in segment.units]
        built = [graph for graph in all_graphs if graph.build_status == LocalGraphBuildStatus.BUILT]
        return DocumentLocalGraphs(
            summary=LocalGraphSummary(
                segment_count=len(segments),
                unit_count=len(all_graphs),
                built_graph_count=len(built),
                blocked_graph_count=len(all_graphs) - len(built),
                node_count=sum(len(graph.nodes) for graph in built),
                edge_count=sum(len(graph.edges) for graph in built),
            ),
            segments=segments,
        )

    def build_unit(self, unit, frame, propositions) -> GraphUnitLocalGraph:
        prefix = unit.graph_unit_id
        full_evidence = EvidenceReference(
            evidence_ids=[block.evidence_id for block in propositions.evidence_blocks],
            quote=unit.text,
        )
        graph_unit_id = f"{prefix}::graph_unit"
        event_id = f"{prefix}::event"
        nodes = [
            LocalGraphNode(
                node_id=graph_unit_id,
                node_type=GraphNodeType.GRAPH_UNIT,
                semantic_type=str(unit.source_type),
                label=unit.graph_unit_id,
                status=unit.status,
                certainty=unit.certainty,
                evidence=full_evidence,
                metadata={
                    "mdt_specialty": [str(item) for item in unit.mdt_specialty],
                    "temporal_anchor": unit.temporal_anchor,
                    "clinical_context": unit.clinical_context,
                },
            ),
            LocalGraphNode(
                node_id=event_id,
                node_type=GraphNodeType.EVENT,
                semantic_type=str(frame.primary_frame),
                label=str(frame.primary_frame),
                status=unit.status,
                certainty=unit.certainty,
                evidence=full_evidence,
                metadata={"rationale": frame.rationale},
            ),
        ]
        edges: list[LocalGraphEdge] = []
        edge_number = 1

        def add_edge(edge_type: GraphEdgeType, source: str, target: str) -> None:
            nonlocal edge_number
            edges.append(
                LocalGraphEdge(
                    edge_id=f"{prefix}::edge_{edge_number:03d}",
                    edge_type=edge_type,
                    source_node_id=source,
                    target_node_id=target,
                )
            )
            edge_number += 1

        add_edge(GraphEdgeType.ORGANIZES_AS, graph_unit_id, event_id)

        for modifier in propositions.event_modifiers:
            modifier_id = f"{prefix}::{modifier.modifier_id}"
            nodes.append(_modifier_node(modifier_id, modifier))
            add_edge(GraphEdgeType.HAS_EVENT_MODIFIER, event_id, modifier_id)

        actor_ids: dict[tuple, str] = {}
        actor_counts: defaultdict[str, int] = defaultdict(int)
        for proposition in propositions.propositions:
            proposition_id = f"{prefix}::{proposition.proposition_id}"
            nodes.append(
                LocalGraphNode(
                    node_id=proposition_id,
                    node_type=GraphNodeType.PROPOSITION,
                    semantic_type=str(proposition.proposition_type),
                    label=proposition.concept_text,
                    status=proposition.status,
                    certainty=proposition.certainty,
                    evidence=proposition.evidence,
                    metadata={"rationale": proposition.rationale},
                )
            )
            add_edge(GraphEdgeType.CONTAINS_PROPOSITION, event_id, proposition_id)

            for modifier in proposition.modifiers:
                modifier_id = f"{prefix}::{modifier.modifier_id}"
                nodes.append(_modifier_node(modifier_id, modifier))
                add_edge(GraphEdgeType.HAS_MODIFIER, proposition_id, modifier_id)

            attribution = proposition.attribution
            if attribution is not None:
                key = (
                    str(attribution.attribution_type),
                    attribution.actor_text,
                    tuple(attribution.evidence.evidence_ids),
                    attribution.evidence.quote,
                )
                actor_id = actor_ids.get(key)
                if actor_id is None:
                    actor_counts[str(attribution.attribution_type)] += 1
                    actor_id = (
                        f"{prefix}::source_actor_{str(attribution.attribution_type)}_"
                        f"{actor_counts[str(attribution.attribution_type)]:03d}"
                    )
                    actor_ids[key] = actor_id
                    nodes.append(
                        LocalGraphNode(
                            node_id=actor_id,
                            node_type=GraphNodeType.SOURCE_ACTOR,
                            semantic_type=str(attribution.attribution_type),
                            label=attribution.actor_text,
                            evidence=attribution.evidence,
                        )
                    )
                add_edge(GraphEdgeType.ATTRIBUTED_TO, proposition_id, actor_id)

        _require_valid_graph(nodes, edges, graph_unit_id)
        return GraphUnitLocalGraph(
            graph_unit_id=unit.graph_unit_id,
            segment_id=unit.segment_id,
            primary_frame=frame.primary_frame,
            build_status=LocalGraphBuildStatus.BUILT,
            root_node_id=graph_unit_id,
            evidence_blocks=propositions.evidence_blocks,
            nodes=nodes,
            edges=edges,
        )


def _modifier_node(node_id, modifier) -> LocalGraphNode:
    return LocalGraphNode(
        node_id=node_id,
        node_type=GraphNodeType.MODIFIER,
        semantic_type=str(modifier.modifier_type),
        label=modifier.value_text,
        evidence=modifier.evidence,
    )


def _blocked_graph(
    graph_unit_id,
    segment_id,
    primary_frame,
    evidence_blocks,
    issues,
) -> GraphUnitLocalGraph:
    return GraphUnitLocalGraph(
        graph_unit_id=graph_unit_id,
        segment_id=segment_id,
        primary_frame=primary_frame,
        build_status=LocalGraphBuildStatus.BLOCKED,
        evidence_blocks=evidence_blocks,
        build_issues=issues,
    )


def _index_units(segments) -> dict[str, object]:
    indexed = {}
    for segment in segments:
        for unit in segment.units:
            indexed[unit.graph_unit_id] = unit
    return indexed


def _require_valid_graph(nodes: list[LocalGraphNode], edges: list[LocalGraphEdge], root: str) -> None:
    node_ids = [node.node_id for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("Local graph contains duplicate node IDs.")
    edge_ids = [edge.edge_id for edge in edges]
    if len(edge_ids) != len(set(edge_ids)):
        raise ValueError("Local graph contains duplicate edge IDs.")
    known_nodes = set(node_ids)
    if root not in known_nodes:
        raise ValueError("Local graph root node is missing.")
    for edge in edges:
        if edge.source_node_id not in known_nodes or edge.target_node_id not in known_nodes:
            raise ValueError(f"Edge {edge.edge_id} references a missing node.")
