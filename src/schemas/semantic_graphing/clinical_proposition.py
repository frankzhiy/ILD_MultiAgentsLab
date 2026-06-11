"""Evidence-grounded clinical propositions extracted from one graph unit."""

from enum import StrEnum
from typing import Any, get_args

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.semantic_graphing.graph_unit import GraphUnitCertainty, GraphUnitStatus
from src.schemas.semantic_graphing.primary_frame import PrimaryFrame


class PropositionType(StrEnum):
    """Closed set of clinically meaningful statement types."""

    DEMOGRAPHIC = "demographic"
    EXPOSURE = "exposure"
    SYMPTOM = "symptom"
    SIGN = "sign"
    EXAMINATION = "examination"
    MEASUREMENT = "measurement"
    FINDING = "finding"
    DIAGNOSIS_ASSERTION = "diagnosis_assertion"
    TREATMENT = "treatment"
    PROCEDURE = "procedure"
    MEDICATION = "medication"
    OUTCOME = "outcome"
    DISPOSITION = "disposition"
    PLAN = "plan"
    BACKGROUND_CONDITION = "background_condition"
    INFORMATION_AVAILABILITY = "information_availability"
    OTHER = "other"


class ModifierType(StrEnum):
    """Closed set of clinically meaningful proposition or event modifiers."""

    TIME = "time"
    ONSET = "onset"
    DURATION = "duration"
    FREQUENCY = "frequency"
    SEVERITY = "severity"
    QUANTITY = "quantity"
    INTENSITY = "intensity"
    VALUE = "value"
    UNIT = "unit"
    RANGE = "range"
    TREND = "trend"
    COLOR = "color"
    CONSISTENCY = "consistency"
    QUALITY = "quality"
    TRIGGER = "trigger"
    CONTEXT = "context"
    ANATOMICAL_SITE = "anatomical_site"
    LATERALITY = "laterality"
    DOSE = "dose"
    ROUTE = "route"
    SCHEDULE = "schedule"
    RESPONSE = "response"
    PURPOSE = "purpose"
    METHOD = "method"
    OTHER = "other"


class AttributionType(StrEnum):
    """Who or what is explicitly responsible for a proposition."""

    PATIENT = "patient"
    CLINICIAN = "clinician"
    CARE_FACILITY = "care_facility"
    REPORT = "report"
    OTHER = "other"


class EvidenceBlock(BaseModel):
    """Stable, ordered source-text block addressable by graph nodes and agents."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(
        pattern=r"^.+_ev_\d{3,}$",
        description="Identifier unique across the document, derived from graph_unit_id and order.",
    )
    text: str = Field(
        min_length=1,
        description="Exact continuous block copied from the graph-unit text.",
    )


class EvidenceReference(BaseModel):
    """A precise quote grounded in one or more contiguous evidence blocks."""

    model_config = ConfigDict(extra="forbid")

    evidence_ids: list[str] = Field(
        min_length=1,
        description="Contiguous evidence blocks supporting this item, in source order.",
    )
    quote: str = Field(
        min_length=1,
        description="Exact continuous quote contained in the referenced evidence blocks.",
    )


class ClinicalAttribution(BaseModel):
    """Explicit source responsible for a clinical statement."""

    model_config = ConfigDict(extra="forbid")

    attribution_type: AttributionType = Field(
        description="Role of the explicitly stated source responsible for the proposition."
    )
    actor_text: str = Field(
        min_length=1,
        description="Verbatim or minimally scoped text naming the responsible source.",
    )
    evidence: EvidenceReference = Field(
        description="Evidence reference that explicitly establishes the attribution."
    )


class ClinicalModifier(BaseModel):
    """Clinically meaningful modifier owned by one proposition or event."""

    model_config = ConfigDict(extra="forbid")

    modifier_id: str = Field(
        pattern=r"^mod_\d{3,}$",
        description="Identifier unique across all modifiers in the graph unit.",
    )
    modifier_type: ModifierType = Field(
        description="Clinical role played by this modifier."
    )
    value_text: str = Field(
        min_length=1,
        description="Modifier value stated in the source text without unsupported normalization.",
    )
    evidence: EvidenceReference = Field(
        description="Minimal exact evidence reference expressing this modifier."
    )


class ClinicalProposition(BaseModel):
    """One independently referable clinical statement grounded in the unit."""

    model_config = ConfigDict(extra="forbid")

    proposition_id: str = Field(
        pattern=r"^prop_\d{3,}$",
        description="Identifier unique across propositions in the graph unit.",
    )
    proposition_type: PropositionType = Field(
        description="Clinical statement category represented by the proposition."
    )
    concept_text: str = Field(
        min_length=1,
        description="Clinical concept explicitly stated by the source text.",
    )
    status: GraphUnitStatus = Field(
        default="unknown",
        description="Assertion status explicitly supported by the source text.",
    )
    certainty: GraphUnitCertainty = Field(
        default="unknown",
        description="Certainty level explicitly supported by the source text.",
    )
    attribution: ClinicalAttribution | None = Field(
        default=None,
        description="Explicit source attribution when the text states who made the assertion.",
    )
    modifiers: list[ClinicalModifier] = Field(
        default_factory=list,
        description="Modifiers that belong specifically to this proposition.",
    )
    evidence: EvidenceReference = Field(
        description="Minimal sufficient evidence reference supporting the proposition."
    )
    rationale: str = Field(
        min_length=1,
        description="Short explanation of the proposition boundary and modifier ownership.",
    )


class GraphUnitClinicalPropositions(BaseModel):
    """Clinical propositions and event-level modifiers for one graph unit."""

    model_config = ConfigDict(extra="forbid")

    graph_unit_id: str = Field(description="Graph unit from which these propositions were extracted.")
    primary_frame: PrimaryFrame = Field(
        description="Selected event-nucleus organization template for the graph unit."
    )
    evidence_blocks: list[EvidenceBlock] = Field(
        min_length=1,
        description="Program-generated ordered evidence blocks reconstructing the graph-unit text.",
    )
    event_modifiers: list[ClinicalModifier] = Field(
        default_factory=list,
        description="Modifiers applying to the event nucleus as a whole, not to one proposition.",
    )
    propositions: list[ClinicalProposition] = Field(
        min_length=1,
        description="All independently referable clinical statements grounded in the unit.",
    )
    notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SegmentClinicalPropositions(BaseModel):
    """Clinical propositions for graph units inside one segment."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str
    units: list[GraphUnitClinicalPropositions] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DocumentClinicalPropositions(BaseModel):
    """Document-level clinical propositions parallel to graph units."""

    model_config = ConfigDict(extra="forbid")

    segments: list[SegmentClinicalPropositions] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def render_clinical_proposition_catalog() -> str:
    """Render controlled schema values for prompt injection from the source of truth."""

    def values(enum_type: type[StrEnum]) -> str:
        return "\n".join(f"- `{item}`" for item in enum_type)

    statuses = "\n".join(f"- `{item}`" for item in get_args(GraphUnitStatus))
    certainties = "\n".join(f"- `{item}`" for item in get_args(GraphUnitCertainty))
    return (
        "proposition_type:\n"
        f"{values(PropositionType)}\n\n"
        "modifier_type:\n"
        f"{values(ModifierType)}\n\n"
        "status:\n"
        f"{statuses}\n\n"
        "certainty:\n"
        f"{certainties}\n\n"
        "attribution_type:\n"
        f"{values(AttributionType)}"
    )
