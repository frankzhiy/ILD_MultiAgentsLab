"""Compact, verbatim specialty input for LLM prompts."""

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.semantic_graphing.clinical_proposition import EvidenceBlock
from src.schemas.semantic_graphing.document import DiscourseUnitType, SourceType
from src.schemas.semantic_graphing.graph_unit import (
    GraphUnitCertainty,
    GraphUnitStatus,
    MdtSpecialty,
    SpecialistTarget,
)
from src.schemas.specialty_agent_input import (
    EvidenceRole,
    LocatorStatus,
    SpecialtyCaseInput,
    SpecialtyCaseSummary,
)


class WorkingSegmentSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    text: str
    unit_type: DiscourseUnitType
    clinical_frame: str
    temporal_anchor: str | None = None


class WorkingGraphUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_unit_id: str
    segment_id: str
    text: str
    source_type: SourceType
    mdt_specialty: list[MdtSpecialty]
    temporal_anchor: str | None = None
    clinical_context: str | None = None
    status: GraphUnitStatus
    certainty: GraphUnitCertainty


class WorkingUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_role: EvidenceRole
    may_support_diagnostic_claim: bool
    allowed_uses: list[str]
    locator_status: LocatorStatus
    graph_unit: WorkingGraphUnit
    evidence_blocks: list[EvidenceBlock] = Field(min_length=1)


class WorkingSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment: WorkingSegmentSource
    units: list[WorkingUnit] = Field(default_factory=list)


class SpecialtyWorkingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "specialty_working_input.v1"
    case_id: str
    target_specialty: SpecialistTarget
    segments: list[WorkingSegment]
    summary: SpecialtyCaseSummary


def build_specialty_working_input(case_input: SpecialtyCaseInput) -> SpecialtyWorkingInput:
    return SpecialtyWorkingInput(
        case_id=case_input.case_id,
        target_specialty=case_input.target_specialty,
        segments=[
            WorkingSegment(
                segment=WorkingSegmentSource(
                    segment_id=segment.segment.segment_id,
                    text=segment.segment.text,
                    unit_type=segment.segment.unit_type,
                    clinical_frame=segment.segment.clinical_frame,
                    temporal_anchor=segment.segment.temporal_anchor,
                ),
                units=[
                    WorkingUnit(
                        evidence_role=unit.evidence_role,
                        may_support_diagnostic_claim=unit.may_support_diagnostic_claim,
                        allowed_uses=unit.allowed_uses,
                        locator_status=unit.locator_status,
                        graph_unit=WorkingGraphUnit(
                            graph_unit_id=unit.graph_unit.graph_unit_id,
                            segment_id=unit.graph_unit.segment_id,
                            text=unit.graph_unit.text,
                            source_type=unit.graph_unit.source_type,
                            mdt_specialty=unit.graph_unit.mdt_specialty,
                            temporal_anchor=unit.graph_unit.temporal_anchor,
                            clinical_context=unit.graph_unit.clinical_context,
                            status=unit.graph_unit.status,
                            certainty=unit.graph_unit.certainty,
                        ),
                        evidence_blocks=unit.clinical_propositions.evidence_blocks,
                    )
                    for unit in segment.units
                ],
            )
            for segment in case_input.segments
        ],
        summary=case_input.summary,
    )


def build_specialty_evidence_prompt_input(case_input: SpecialtyCaseInput) -> dict:
    """Evidence lookup for later stages; blocks preserve all graph-unit source text."""

    units = [
        {
            "graph_unit_id": unit.graph_unit.graph_unit_id,
            "source_type": unit.graph_unit.source_type,
            "temporal_anchor": unit.graph_unit.temporal_anchor,
            "evidence_role": unit.evidence_role,
            "may_support_diagnostic_claim": unit.may_support_diagnostic_claim,
            "allowed_uses": unit.allowed_uses,
            "evidence_blocks": unit.clinical_propositions.evidence_blocks,
        }
        for segment in case_input.segments
        for unit in segment.units
    ]
    return {
        "case_id": case_input.case_id,
        "target_specialty": case_input.target_specialty,
        "diagnostic_evidence_units": [
            unit for unit in units if unit["may_support_diagnostic_claim"]
        ],
        "context_only_evidence_units": [
            unit for unit in units if not unit["may_support_diagnostic_claim"]
        ],
    }
