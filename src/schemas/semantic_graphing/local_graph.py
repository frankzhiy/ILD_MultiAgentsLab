"""Deterministic local evidence graphs built from validated clinical propositions."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.semantic_graphing.clinical_proposition import EvidenceBlock, EvidenceReference
from src.schemas.semantic_graphing.graph_unit import GraphUnitCertainty, GraphUnitStatus
from src.schemas.semantic_graphing.primary_frame import PrimaryFrame


class GraphNodeType(StrEnum):
    GRAPH_UNIT = "graph_unit"
    EVENT = "event"
    PROPOSITION = "proposition"
    MODIFIER = "modifier"
    SOURCE_ACTOR = "source_actor"


class GraphEdgeType(StrEnum):
    ORGANIZES_AS = "organizes_as"
    CONTAINS_PROPOSITION = "contains_proposition"
    HAS_EVENT_MODIFIER = "has_event_modifier"
    HAS_MODIFIER = "has_modifier"
    ATTRIBUTED_TO = "attributed_to"


class LocalGraphBuildStatus(StrEnum):
    BUILT = "built"
    BLOCKED = "blocked"


class LocalGraphBuildIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class LocalGraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    node_type: GraphNodeType
    semantic_type: str = Field(min_length=1)
    label: str = Field(min_length=1)
    status: GraphUnitStatus | None = None
    certainty: GraphUnitCertainty | None = None
    evidence: EvidenceReference
    metadata: dict[str, Any] = Field(default_factory=dict)


class LocalGraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str = Field(min_length=1)
    edge_type: GraphEdgeType
    source_node_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphUnitLocalGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_unit_id: str
    segment_id: str
    primary_frame: PrimaryFrame
    build_status: LocalGraphBuildStatus
    root_node_id: str | None = None
    evidence_blocks: list[EvidenceBlock] = Field(default_factory=list)
    nodes: list[LocalGraphNode] = Field(default_factory=list)
    edges: list[LocalGraphEdge] = Field(default_factory=list)
    build_issues: list[LocalGraphBuildIssue] = Field(default_factory=list)


class SegmentLocalGraphs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    units: list[GraphUnitLocalGraph] = Field(default_factory=list)


class LocalGraphSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_count: int = Field(ge=0)
    unit_count: int = Field(ge=0)
    built_graph_count: int = Field(ge=0)
    blocked_graph_count: int = Field(ge=0)
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)


class DocumentLocalGraphs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: LocalGraphSummary
    segments: list[SegmentLocalGraphs] = Field(default_factory=list)
