"""Structured cross-specialty integration produced by the MDT chair."""

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from src.guidelines.models import GuidelineEvidencePointer


Specialty = Literal[
    "pulmonology",
    "thoracic_radiology",
    "rheumatology",
    "pathology",
]
SourceType = Literal[
    "specialty_assessment",
    "interspecialty_question",
    "assessment_evidence_need",
    # Read-only compatibility for existing run artifacts.
    "native_conclusion",
    "native_question",
    "evidence_gap",
]
EpistemicStatus = Literal[
    "affirms",
    "denies",
    "possible",
    "indeterminate",
    "not_assessable",
    "not_applicable",
]
ProfessionalLevel = Literal[
    "observation",
    "morphologic_pattern",
    "disease_diagnosis",
    "etiologic_attribution",
    "severity_or_trajectory",
    "assessability",
]
PositionRole = Literal["preferred", "alternative", "tentative", "boundary"]
ConflictNature = Literal[
    "direct_contradiction",
    "decision_relevant_discordance",
]


class SpecialtySourceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str
    specialty: Specialty
    source_type: SourceType
    source_path: str
    quote: str


class CaseEvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_ref: str
    segment_id: str = ""
    graph_unit_id: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    proposition_ids: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)
    quote: str = ""


class ChairEvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supporting: list[CaseEvidenceCitation] = Field(default_factory=list)
    weakening: list[CaseEvidenceCitation] = Field(default_factory=list)
    discriminating: list[CaseEvidenceCitation] = Field(default_factory=list)
    background: list[CaseEvidenceCitation] = Field(default_factory=list)


class CitedChairStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_refs: list[str] = Field(min_length=1)
    source_citations: SkipJsonSchema[list[SpecialtySourceCitation]] = Field(
        default_factory=list
    )
    evidence: SkipJsonSchema[ChairEvidenceBundle] = Field(
        default_factory=ChairEvidenceBundle
    )
    guideline_evidence: SkipJsonSchema[list[GuidelineEvidencePointer]] = Field(
        default_factory=list
    )


class LedgerAtomicClaim(BaseModel):
    """One minimal proposition extracted from a specialty assessment."""

    model_config = ConfigDict(extra="forbid")

    claim_id: SkipJsonSchema[str] = ""
    source_ref: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    dimension: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    evidence_scope: str = Field(min_length=1)
    professional_level: ProfessionalLevel
    position_role: PositionRole
    epistemic_status: EpistemicStatus


class LedgerClaimGroup(BaseModel):
    """A semantic topic used by the public synthesis step."""

    model_config = ConfigDict(extra="forbid")

    topic_id: SkipJsonSchema[str] = ""
    label: str = Field(min_length=1)
    disposition: Literal["integrated", "boundary", "conflict", "follow_up"]
    conflict_nature: ConflictNature | None = None
    comparison_target: str = ""
    comparison_conditions: str = ""
    why_incompatible: str = ""
    decision_impact: str = ""
    claims: list[LedgerAtomicClaim] = Field(min_length=1)


class LedgerAnswerLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialty: SkipJsonSchema[Specialty] = "pulmonology"
    source_refs: list[str] = Field(min_length=1)
    relation: Literal["direct_answer", "partial_answer", "evidence_boundary"]


class LedgerQuestionRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: SkipJsonSchema[str] = ""
    source_refs: list[str] = Field(min_length=1)
    route: Literal["question", "evidence_need", "mixed"]
    normalized_question: str = ""
    evidence_requirement: str = ""
    target_specialties: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    answer_links: list[LedgerAnswerLink] = Field(default_factory=list)


class LedgerEvidenceNeedGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: SkipJsonSchema[str] = ""
    source_refs: list[str] = Field(min_length=1)
    required_information: str = Field(min_length=1)
    coverage_source_refs: list[str] = Field(default_factory=list)


class ChairSemanticLedger(BaseModel):
    """Auditable, non-public semantic normalization produced before synthesis."""

    model_config = ConfigDict(extra="forbid")

    claim_groups: list[LedgerClaimGroup] = Field(default_factory=list)
    question_routes: list[LedgerQuestionRoute] = Field(default_factory=list)
    evidence_need_groups: list[LedgerEvidenceNeedGroup] = Field(default_factory=list)


class IntegratedConclusion(CitedChairStatement):
    conclusion_id: SkipJsonSchema[str] = ""
    statement: str = Field(min_length=1)
    medical_basis: str = Field(min_length=1)
    decision_impact: str = Field(min_length=1)
    role: Literal["primary", "important_alternative", "cannot_safely_ignore"]
    conclusion_type: Literal[
        "working_diagnosis",
        "morphologic_pattern",
        "etiologic_attribution",
        "severity_or_risk",
        "imaging_interpretation",
        "rheumatic_disease",
        "ild_attribution",
        "progression",
        "etiologic_association",
        "other",
    ]
    status: Literal["supported", "favored", "possible"]
    supporting_specialties: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class AssessmentBoundary(CitedChairStatement):
    boundary_id: SkipJsonSchema[str] = ""
    topic: str = Field(min_length=1)
    scope: Literal[
        "clinical",
        "imaging",
        "pathology",
        "rheumatology",
        "progression",
        "etiology",
        "other",
    ]
    status: Literal[
        "indeterminate", "not_assessable", "unclassifiable", "not_applicable"
    ]
    statement: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    decision_impact: str = Field(min_length=1)
    related_evidence_need_source_refs: list[str] = Field(default_factory=list)
    related_evidence_need_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)
    specialties: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    assessment_source_refs: SkipJsonSchema[list[str]] = Field(default_factory=list)
    question_source_refs: SkipJsonSchema[list[str]] = Field(default_factory=list)


class QuestionAnswer(CitedChairStatement):
    specialty: SkipJsonSchema[Specialty] = "pulmonology"
    relation: Literal["direct_answer", "partial_answer", "evidence_boundary"]
    answer: str = Field(min_length=1)


class IntegratedQuestion(CitedChairStatement):
    question_id: SkipJsonSchema[str] = ""
    question: str = Field(min_length=1)
    raised_by: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    target_specialties: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    responded_by: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    awaiting_specialties: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    answers: list[QuestionAnswer] = Field(default_factory=list)
    response_status: SkipJsonSchema[
        Literal["none_responded", "partially_responded", "all_responded"]
    ] = "none_responded"
    answer_status: Literal[
        "answered",
        "partially_answered",
        "unanswered",
        "boundary_answered",
        "blocked_by_evidence",
    ] = Field(validation_alias=AliasChoices("answer_status", "resolution_status"))
    review_status: SkipJsonSchema[
        Literal[
            "not_reviewed",
            "awaiting_review",
            "accepted",
            "accepted_boundary",
            "clarification_requested",
            "corroboration_requested",
            "incompatibility_flagged",
            "converted_to_evidence_need",
        ]
    ] = "not_reviewed"
    discussion_status: SkipJsonSchema[
        Literal[
            "not_started",
            "awaiting_answer",
            "awaiting_requester_review",
            "clarification_in_progress",
            "awaiting_corroboration",
            "awaiting_conflict_assessment",
            "closed_this_round",
            "waiting_for_new_evidence",
        ]
    ] = "awaiting_answer"
    closure_type: SkipJsonSchema[
        Literal[
            "explicit_answer",
            "boundary_answer",
            "clarified_answer",
            "corroborated_answer",
            "converted_to_evidence_need",
            "merged_into_existing_question",
        ]
        | None
    ] = None
    reviewed_by: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    awaiting_review_specialties: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    answer_summary: str = Field(min_length=1)
    remaining_clarification: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    decision_unlocked: str = Field(min_length=1)
    related_evidence_need_source_refs: list[str] = Field(default_factory=list)
    related_evidence_need_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_statuses(cls, value):
        if not isinstance(value, dict) or "answer_status" in value:
            return value
        legacy = value.get("resolution_status")
        mapping = {
            "resolved": "answered",
            "partially_resolved": "partially_answered",
            "unresolved": "unanswered",
            "blocked_by_evidence": "blocked_by_evidence",
            "disputed": "answered",
        }
        if legacy in mapping:
            value = dict(value)
            value["answer_status"] = mapping[legacy]
            value.pop("resolution_status", None)
            if legacy == "disputed":
                value.setdefault("review_status", "incompatibility_flagged")
                value.setdefault("discussion_status", "awaiting_conflict_assessment")
        return value


class EvidenceNeed(CitedChairStatement):
    need_id: SkipJsonSchema[str] = ""
    status: Literal["available", "partially_available", "missing"]
    raised_by: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    required_information: str = Field(min_length=1)
    available_information: str = Field(min_length=1)
    remaining_information: str = Field(min_length=1)
    provided_by: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    why_it_matters: str = Field(min_length=1)
    decision_unlocked: str = Field(min_length=1)
    assessment_source_refs: SkipJsonSchema[list[str]] = Field(default_factory=list)
    question_source_refs: SkipJsonSchema[list[str]] = Field(default_factory=list)


class ConflictPosition(CitedChairStatement):
    specialty: SkipJsonSchema[Specialty] = "pulmonology"
    stance: Literal["affirms", "denies", "favors"]
    position: str = Field(min_length=1)


class CrossSpecialtyConflict(BaseModel):
    """An unresolved incompatibility between specialty assessments."""

    model_config = ConfigDict(extra="forbid")

    conflict_id: SkipJsonSchema[str] = ""
    topic: str = Field(min_length=1)
    conflict_nature: ConflictNature
    conflict_domain: Literal[
        "diagnostic_interpretation",
        "morphologic_interpretation",
        "etiologic_attribution",
        "severity_or_trajectory",
        "assessability_or_scope",
    ]
    status: SkipJsonSchema[
        Literal[
            "unresolved",
            "pending_clarification",
            "pending_evidence",
            "pending_clarification_and_evidence",
        ]
    ] = "unresolved"
    comparison_target: str = Field(
        min_length=1,
        validation_alias=AliasChoices("comparison_target", "shared_claim"),
    )
    comparison_conditions: str = Field(min_length=1)
    positions: list[ConflictPosition] = Field(min_length=2)
    why_incompatible: str = Field(min_length=1)
    decision_impact: str = Field(min_length=1)
    resolution_requirement: str = Field(min_length=1)
    related_question_source_refs: list[str] = Field(default_factory=list)
    related_evidence_need_source_refs: list[str] = Field(default_factory=list)
    related_question_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)
    related_evidence_need_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)
    specialties: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_conflict(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        value.setdefault("conflict_nature", "direct_contradiction")
        if "comparison_target" not in value and "shared_claim" in value:
            value["comparison_target"] = value.pop("shared_claim")
        return value

    @model_validator(mode="after")
    def validate_position_shape(self):
        stances = {position.stance for position in self.positions}
        if self.conflict_nature == "direct_contradiction":
            if stances != {"affirms", "denies"}:
                raise ValueError(
                    "direct_contradiction requires both affirms and denies positions"
                )
        elif stances != {"favors"}:
            raise ValueError(
                "decision_relevant_discordance requires only favors positions"
            )
        return self


class MDTChairIntegration(BaseModel):
    """The chair's public cross-specialty integration result."""

    model_config = ConfigDict(extra="forbid")

    schema_version: SkipJsonSchema[
        Literal["mdt_chair.v5", "mdt_chair.v6", "mdt_chair.v7", "mdt_chair.v8"]
    ] = "mdt_chair.v8"
    case_id: SkipJsonSchema[str] = ""
    integrated_conclusions: list[IntegratedConclusion] = Field(default_factory=list)
    assessment_boundaries: list[AssessmentBoundary] = Field(default_factory=list)
    conflicts: list[CrossSpecialtyConflict] = Field(default_factory=list)
    questions: list[IntegratedQuestion] = Field(default_factory=list)
    evidence_needs: list[EvidenceNeed] = Field(default_factory=list)


# Compatibility for callers importing the former class name.
MDTChairSynthesis = MDTChairIntegration
