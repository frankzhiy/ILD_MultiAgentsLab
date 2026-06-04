from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


Certainty = Literal["high", "moderate", "low", "unknown"]
Status = Literal["present", "absent", "possible", "historical", "not_mentioned", "unknown"]
Temporality = Literal["current", "past", "progressive", "stable", "improving", "unknown"]


class EvidenceSpan(BaseModel):
    text: str
    source_type: str
    segment_id: str | None = None
    start_char: int | None = None
    end_char: int | None = None


class GraphNode(BaseModel):
    id: str
    type: str
    name: str
    canonical_name: str | None = None
    status: Status = "unknown"
    certainty: Certainty = "unknown"
    temporality: Temporality = "unknown"
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceSpan] = Field(default_factory=list)


class GraphEdge(BaseModel):
    id: str
    source: str
    relation: str
    target: str
    certainty: Certainty = "unknown"
    polarity: Literal["supports", "argues_against", "neutral", "unknown"] = "unknown"
    attributes: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceSpan] = Field(default_factory=list)


class MissingInformation(BaseModel):
    id: str
    name: str
    source_type: str | None = None
    importance: Literal["high", "moderate", "low"] = "moderate"
    reason: str
    suggested_follow_up: str | None = None


class GraphConflict(BaseModel):
    id: str
    description: str
    involved_node_ids: list[str] = Field(default_factory=list)
    involved_edge_ids: list[str] = Field(default_factory=list)
    severity: Literal["high", "moderate", "low"] = "moderate"
    resolution: str = "requires_human_review"


class SemanticSubgraph(BaseModel):
    segment_id: str
    source_type: str
    construction_schema: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    missing_information: list[MissingInformation] = Field(default_factory=list)
    conflicts: list[GraphConflict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_edge_endpoints(self) -> "SemanticSubgraph":
        node_ids = {node.id for node in self.nodes}
        dangling = [
            edge.id for edge in self.edges if edge.source not in node_ids or edge.target not in node_ids
        ]
        if dangling:
            raise ValueError(f"Subgraph has dangling edge endpoints: {dangling}")
        return self

