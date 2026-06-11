"""Deterministic validation results for extracted clinical propositions."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class PropositionValidationIssue(BaseModel):
    """One actionable validation finding tied to a graph-unit structure."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    severity: ValidationSeverity
    message: str = Field(min_length=1)
    proposition_id: str | None = None
    modifier_id: str | None = None


class PropositionValidationMetrics(BaseModel):
    """Useful unit-level counts and evidence coverage for manual review."""

    model_config = ConfigDict(extra="forbid")

    proposition_count: int = Field(ge=0)
    event_modifier_count: int = Field(ge=0)
    proposition_modifier_count: int = Field(ge=0)
    attributed_proposition_count: int = Field(ge=0)
    evidence_coverage: float = Field(ge=0.0, le=1.0)


class GraphUnitPropositionValidation(BaseModel):
    """Validation result for one graph unit's extracted propositions."""

    model_config = ConfigDict(extra="forbid")

    graph_unit_id: str
    is_graph_ready: bool
    metrics: PropositionValidationMetrics
    issues: list[PropositionValidationIssue] = Field(default_factory=list)


class SegmentPropositionValidation(BaseModel):
    """Validation results for graph units inside one segment."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str
    units: list[GraphUnitPropositionValidation] = Field(default_factory=list)


class PropositionValidationSummary(BaseModel):
    """Document-level validation counts."""

    model_config = ConfigDict(extra="forbid")

    segment_count: int = Field(ge=0)
    unit_count: int = Field(ge=0)
    graph_ready_unit_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    info_count: int = Field(ge=0)


class DocumentPropositionValidation(BaseModel):
    """Document-level proposition validation report."""

    model_config = ConfigDict(extra="forbid")

    is_graph_ready: bool
    summary: PropositionValidationSummary
    segments: list[SegmentPropositionValidation] = Field(default_factory=list)
