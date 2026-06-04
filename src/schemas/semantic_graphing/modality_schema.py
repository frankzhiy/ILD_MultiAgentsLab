from typing import Any

from pydantic import BaseModel, Field


class ModalityGraphSchema(BaseModel):
    name: str
    source_type: str
    graph_construction_strategy: str
    graph_center: str
    primary_questions: list[str] = Field(default_factory=list)
    allowed_node_types: list[str] = Field(default_factory=list)
    allowed_edge_types: list[str] = Field(default_factory=list)
    required_node_attributes: list[str] = Field(default_factory=list)
    required_edge_attributes: list[str] = Field(default_factory=list)
    key_missingness_checks: list[str] = Field(default_factory=list)
    construction_rules: list[str] = Field(default_factory=list)
    diagnostic_targets: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

