from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from src.schemas.semantic_graphing.document import SourceType


GraphUnitStatus = Literal[
    "present",
    "absent",
    "possible",
    "historical",
    "planned",
    "performed",
    "not_performed",
    "unknown",
]

GraphUnitCertainty = Literal["high", "moderate", "low", "unknown"]


class MdtSpecialty(StrEnum):
    """ILD multidisciplinary team specialties that may review a graph unit.

    Based on the core ILD MDD membership (pulmonology, thoracic radiology,
    pathology, rheumatology, occupational/environmental medicine), plus a
    shared_context tag for broadcast background that is not specialty evidence.
    """

    PULMONOLOGY = "pulmonology"
    THORACIC_RADIOLOGY = "thoracic_radiology"
    PATHOLOGY = "pathology"
    RHEUMATOLOGY = "rheumatology"
    OCCUPATIONAL_ENVIRONMENTAL = "occupational_environmental"
    SHARED_CONTEXT = "shared_context"
    OTHER = "other"


class GraphUnit(BaseModel):
    graph_unit_id: str = Field(description="Stable graph-unit id, such as seg_001_gu_001.")
    segment_id: str = Field(description="Parent segment id.")
    text: str = Field(description="Verbatim continuous substring from the parent segment.")
    source_type: SourceType = Field(
        description="Narrative role of this evidence block (what part of the clinical story it tells)."
    )
    mdt_specialty: list[MdtSpecialty] = Field(
        min_length=1,
        description="ILD MDT specialties that should review this unit; decided by reading the content.",
    )
    temporal_anchor: str | None = Field(default=None)
    clinical_context: str | None = Field(default=None)
    status: GraphUnitStatus = "unknown"
    certainty: GraphUnitCertainty = "unknown"
    start_char: int | None = Field(
        default=None,
        ge=0,
        description="0-based start offset in the raw input; filled by program validation.",
    )
    end_char: int | None = Field(
        default=None,
        ge=0,
        description="0-based exclusive end offset in the raw input; filled by program validation.",
    )
    segment_start_char: int | None = Field(
        default=None,
        ge=0,
        description="0-based start offset inside the parent segment; filled by program validation.",
    )
    segment_end_char: int | None = Field(
        default=None,
        ge=0,
        description="0-based exclusive end offset inside the parent segment; filled by program validation.",
    )
    rationale: str = Field(description="Short reason for the unit boundary and source type.")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_offsets(self) -> "GraphUnit":
        if self.segment_end_char is not None and self.segment_start_char is not None:
            if self.segment_end_char <= self.segment_start_char:
                raise ValueError("segment_end_char must be greater than segment_start_char")
        if self.end_char is not None and self.start_char is not None:
            if self.end_char <= self.start_char:
                raise ValueError("end_char must be greater than start_char")
        return self


class SegmentGraphUnits(BaseModel):
    segment_id: str
    graph_units: list[GraphUnit] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DocumentGraphUnits(BaseModel):
    segments: list[SegmentGraphUnits] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
