"""Merged semantic-graph input for one ILD specialty agent."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.semantic_graphing.clinical_proposition import (
    GraphUnitClinicalPropositions,
)
from src.schemas.semantic_graphing.document import ClassifiedSegment
from src.schemas.semantic_graphing.graph_unit import (
    GraphUnit,
    MdtSpecialty,
    SpecialistTarget,
)
from src.schemas.semantic_graphing.local_graph import GraphUnitLocalGraph
from src.schemas.semantic_graphing.primary_frame import GraphUnitPrimaryFrame
from src.schemas.semantic_graphing.proposition_validation import (
    GraphUnitPropositionValidation,
)


class EvidenceRole(StrEnum):
    OWNED = "owned"
    SHARED_CONTEXT = "shared_context"
    COLLABORATIVE_CONTEXT = "collaborative_context"
    REFERENCE_ONLY = "reference_only"


LocatorStatus = Literal["available", "degraded"]


class SpecialtyUnitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_index: int = Field(ge=1)
    unit_index: int = Field(ge=1)
    evidence_role: EvidenceRole
    may_support_diagnostic_claim: bool
    allowed_uses: list[str] = Field(min_length=1)
    locator_status: LocatorStatus
    graph_unit: GraphUnit
    primary_frame: GraphUnitPrimaryFrame
    clinical_propositions: GraphUnitClinicalPropositions
    proposition_validation: GraphUnitPropositionValidation
    local_graph: GraphUnitLocalGraph


class SpecialtySegmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_index: int = Field(ge=1)
    segment: ClassifiedSegment
    units: list[SpecialtyUnitInput] = Field(default_factory=list)


class SpecialtyCaseSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_count: int = Field(ge=0)
    unit_count: int = Field(ge=0)
    owned_unit_count: int = Field(ge=0)
    shared_context_unit_count: int = Field(ge=0)
    collaborative_context_unit_count: int = Field(default=0, ge=0)
    reference_only_unit_count: int = Field(ge=0)
    available_locator_count: int = Field(ge=0)
    degraded_locator_count: int = Field(ge=0)


class SpecialtyCaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    target_specialty: SpecialistTarget
    source_run_dir: str = Field(min_length=1)
    segments: list[SpecialtySegmentInput] = Field(default_factory=list)
    summary: SpecialtyCaseSummary
