"""Structured pathology state and staged outputs for ILD consultation."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from src.guidelines.models import GuidelineEvidencePointer
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget
from src.schemas.specialty_agent_input import SpecialtyCaseInput


PathologyConfidence = Literal["very_high", "high", "moderate", "low", "unknown"]


class PathologyDomain(StrEnum):
    SOURCE_AND_MATERIAL = "source_and_material_evaluability"
    SPECIMEN_AND_SAMPLING = "specimen_and_sampling"
    TISSUE_ARCHITECTURE = "tissue_compartment_and_architecture"
    PRIMARY_PATTERN = "primary_histopathologic_pattern"
    COEXISTING_AND_ACUTE = "coexisting_pattern_and_acute_overlay"
    ETIOLOGIC_CLUES = "etiologic_and_alternative_clues"
    ANCILLARY_STUDIES = "ancillary_studies"
    PATHOLOGY_FORMULATION = "pathology_formulation"
    SPECIALIST_INTEGRATION = "specialist_integration"
    DECISION_RELEVANT_GAPS = "decision_relevant_gaps"


class HistopathologicPattern(StrEnum):
    UIP = "UIP"
    NSIP = "NSIP"
    BIP = "BIP"
    DAD = "DAD"
    PPFE = "PPFE"
    LIP = "LIP"
    OP = "OP"
    RB_ILD = "RB-ILD"
    AMP = "AMP"
    RARE_ALVEOLAR_FILLING = "rare_alveolar_filling_pattern"
    COMBINED = "combined_pattern"
    UNCLASSIFIABLE = "unclassifiable_pattern"
    OTHER = "other"


INITIAL_DOMAINS = tuple(PathologyDomain)
INITIAL_REVIEW_STATUSES = {
    "assessed",
    "partially_assessable",
    "not_assessable",
    "deferred_to_specialist",
    "not_applicable",
}
DISCUSSION_REVIEW_STATUSES = {
    "updated",
    "reviewed_unchanged",
    "still_not_assessable",
    "still_deferred",
    "resolved",
    "not_applicable",
}


class EvidencePointer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_ids: list[str] = Field(
        min_length=1,
        description=(
            "一个 EvidencePointer 表示一个 Graph Unit，可填写该图内一个或多个 "
            "evidence block ID。"
        ),
    )
    segment_id: SkipJsonSchema[str] = ""
    graph_unit_id: SkipJsonSchema[str] = ""
    node_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)
    quote: SkipJsonSchema[str] = ""


class PathologyAssessmentItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment: str = Field(min_length=1)
    confidence: PathologyConfidence
    reasoning_summary: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    conflicting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class SourceAssessment(PathologyAssessmentItem):
    material_status: Literal[
        "pathology_material_available",
        "pathology_report_only",
        "pathology_mentioned_without_report",
        "no_pathology_material",
        "uncertain_availability",
    ]
    review_basis: Literal[
        "formal_pathology_report",
        "report_excerpt",
        "clinician_paraphrase",
        "diagnostic_label_only",
        "no_material",
        "uncertain",
    ]
    direct_slides_reviewed: Literal[False] = False


class SpecimenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specimen_id: str = Field(min_length=1)
    procedure: Literal[
        "surgical_lung_biopsy",
        "transbronchial_cryobiopsy",
        "transbronchial_forceps_biopsy",
        "transthoracic_needle_biopsy",
        "lung_resection",
        "bal_or_cytology",
        "other",
        "unknown",
    ]
    site: str = Field(min_length=1)
    temporal_anchor: str | None = None
    description: str = Field(min_length=1)
    source_authority: Literal[
        "formal_pathology_report",
        "report_excerpt",
        "clinician_paraphrase",
        "diagnostic_label_only",
        "unknown",
    ]
    direct_slides_reviewed: Literal[False] = False
    adequacy: Literal["adequate", "limited", "inadequate", "not_assessable"]
    representativeness: Literal[
        "representative",
        "possibly_representative",
        "nonrepresentative",
        "not_assessable",
    ]
    limitations: list[str] = Field(default_factory=list)
    supporting_evidence: list[EvidencePointer] = Field(min_length=1)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)


class MorphologicFeature(PathologyAssessmentItem):
    compartment: Literal[
        "interstitial",
        "alveolar_filling",
        "bronchiolocentric",
        "pleural_subpleural",
        "vascular",
        "lymphatic",
        "mixed",
        "other",
        "not_assessable",
    ]
    feature: str = Field(min_length=1)
    status: Literal["present", "absent", "possible", "not_assessable"]
    diagnostic_significance: str = Field(min_length=1)


class HistopathologicPatternAssessment(PathologyAssessmentItem):
    pattern: HistopathologicPattern
    role: Literal["dominant", "coexisting", "acute_overlay", "alternative"]
    status: Literal[
        "supported",
        "probable",
        "indeterminate",
        "not_supported",
        "not_assessable",
    ]
    fibrotic_status: Literal[
        "fibrotic", "nonfibrotic", "mixed", "not_applicable", "not_assessable"
    ]
    ipf_histopathology_category: Literal[
        "UIP",
        "probable_UIP",
        "indeterminate_for_UIP",
        "alternative_diagnosis",
        "not_applicable",
        "not_assessable",
    ] = "not_applicable"


class EtiologicAssociation(PathologyAssessmentItem):
    association: str = Field(min_length=1)
    strength: Literal[
        "strongly_favors",
        "supports",
        "possible",
        "does_not_support",
        "not_assessable",
    ]
    requires_multidisciplinary_confirmation: Literal[True] = True


class AncillaryStudyAssessment(PathologyAssessmentItem):
    study: str = Field(min_length=1)
    reported_result: str = Field(min_length=1)
    interpretation: Literal[
        "diagnostic",
        "supportive",
        "exclusionary",
        "nonspecific",
        "pending",
        "not_assessable",
    ]


class PathologyFormulation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification_status: Literal[
        "pattern_supported",
        "provisional_pattern",
        "unclassifiable_pattern",
        "insufficient_material",
        "no_pathology_material",
    ]
    primary_pattern: HistopathologicPattern | None = None
    formulation: str = Field(min_length=1)
    confidence: PathologyConfidence
    reasoning_summary: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    conflicting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_pattern_requirement(self):
        needs_pattern = self.classification_status in {
            "pattern_supported",
            "provisional_pattern",
            "unclassifiable_pattern",
        }
        if needs_pattern and self.primary_pattern is None:
            raise ValueError("This pathology classification status requires primary_pattern")
        if self.classification_status in {"insufficient_material", "no_pathology_material"}:
            if self.primary_pattern is not None:
                raise ValueError("Insufficient or absent pathology material cannot have primary_pattern")
        return self


class DataGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_type: Literal[
        "not_provided",
        "insufficient_detail",
        "nonrepresentative_sampling",
        "not_performed",
        "uncertain_availability",
    ]
    available_information: str = Field(min_length=1)
    missing_information: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    decision_unlocked: str = Field(min_length=1)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)


class SpecialistQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialty: SpecialistTarget
    question: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)


class ReferenceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation: str = Field(min_length=1)
    why_confirmation_is_needed: str = Field(min_length=1)
    related_evidence: list[EvidencePointer] = Field(min_length=1)


class DomainReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: PathologyDomain
    status: Literal[
        "assessed",
        "partially_assessable",
        "not_assessable",
        "deferred_to_specialist",
        "not_applicable",
        "updated",
        "reviewed_unchanged",
        "still_not_assessable",
        "still_deferred",
        "resolved",
    ]
    rationale: str = Field(min_length=1)


class InitialDomainReview(DomainReview):
    status: Literal[
        "assessed",
        "partially_assessable",
        "not_assessable",
        "deferred_to_specialist",
        "not_applicable",
    ]


class DiscussionDomainReview(DomainReview):
    status: Literal[
        "updated",
        "reviewed_unchanged",
        "still_not_assessable",
        "still_deferred",
        "resolved",
        "not_applicable",
    ]


class InitialSpecimenReview(InitialDomainReview):
    domain: Literal[
        PathologyDomain.SOURCE_AND_MATERIAL,
        PathologyDomain.SPECIMEN_AND_SAMPLING,
    ]


class InitialMorphologyReview(InitialDomainReview):
    domain: Literal[
        PathologyDomain.TISSUE_ARCHITECTURE,
        PathologyDomain.PRIMARY_PATTERN,
        PathologyDomain.COEXISTING_AND_ACUTE,
        PathologyDomain.ETIOLOGIC_CLUES,
        PathologyDomain.ANCILLARY_STUDIES,
    ]


class InitialFormulationReview(InitialDomainReview):
    domain: Literal[
        PathologyDomain.PATHOLOGY_FORMULATION,
        PathologyDomain.SPECIALIST_INTEGRATION,
        PathologyDomain.DECISION_RELEVANT_GAPS,
    ]


class PathologyClinicalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["pathology.v1"] = "pathology.v1"
    case_id: SkipJsonSchema[str] = ""
    phase: Literal["initial_assessment", "discussion_update"]
    domain_reviews: list[DomainReview] = Field(min_length=10, max_length=10)
    source_assessment: SourceAssessment | None = None
    specimens: list[SpecimenRecord] = Field(default_factory=list)
    morphologic_features: list[MorphologicFeature] = Field(default_factory=list)
    pattern_assessments: list[HistopathologicPatternAssessment] = Field(default_factory=list)
    etiologic_associations: list[EtiologicAssociation] = Field(default_factory=list)
    ancillary_studies: list[AncillaryStudyAssessment] = Field(default_factory=list)
    pathology_formulation: PathologyFormulation | None = None
    specialist_dependencies: list[SpecialistQuestion] = Field(default_factory=list)
    reference_observations: list[ReferenceObservation] = Field(default_factory=list)
    missing_data: list[DataGap] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_domain_protocol(self):
        domains = [item.domain for item in self.domain_reviews]
        if len(domains) != len(set(domains)) or set(domains) != set(INITIAL_DOMAINS):
            raise ValueError("domain_reviews must process each pathology domain exactly once")
        allowed = (
            INITIAL_REVIEW_STATUSES
            if self.phase == "initial_assessment"
            else DISCUSSION_REVIEW_STATUSES
        )
        invalid = [item.status for item in self.domain_reviews if item.status not in allowed]
        if invalid:
            raise ValueError(f"Invalid {self.phase} pathology statuses: {invalid}")
        return self


class PathologyInitialAssessment(PathologyClinicalState):
    phase: Literal["initial_assessment"] = "initial_assessment"
    domain_reviews: list[InitialDomainReview] = Field(min_length=10, max_length=10)


class InitialSpecimenReconstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_reviews: list[InitialSpecimenReview] = Field(min_length=2, max_length=2)
    source_assessment: SourceAssessment | None = None
    specimens: list[SpecimenRecord] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_domains(self):
        _require_stage_domains(
            self.domain_reviews,
            {PathologyDomain.SOURCE_AND_MATERIAL, PathologyDomain.SPECIMEN_AND_SAMPLING},
            INITIAL_REVIEW_STATUSES,
        )
        return self


class InitialMorphologicAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_reviews: list[InitialMorphologyReview] = Field(min_length=5, max_length=5)
    morphologic_features: list[MorphologicFeature] = Field(default_factory=list)
    pattern_assessments: list[HistopathologicPatternAssessment] = Field(default_factory=list)
    etiologic_associations: list[EtiologicAssociation] = Field(default_factory=list)
    ancillary_studies: list[AncillaryStudyAssessment] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_domains(self):
        _require_stage_domains(
            self.domain_reviews,
            {
                PathologyDomain.TISSUE_ARCHITECTURE,
                PathologyDomain.PRIMARY_PATTERN,
                PathologyDomain.COEXISTING_AND_ACUTE,
                PathologyDomain.ETIOLOGIC_CLUES,
                PathologyDomain.ANCILLARY_STUDIES,
            },
            INITIAL_REVIEW_STATUSES,
        )
        return self


class InitialConsultFormulation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_reviews: list[InitialFormulationReview] = Field(min_length=3, max_length=3)
    pathology_formulation: PathologyFormulation | None = None
    specialist_dependencies: list[SpecialistQuestion] = Field(default_factory=list)
    reference_observations: list[ReferenceObservation] = Field(default_factory=list)
    missing_data: list[DataGap] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_domains(self):
        _require_stage_domains(
            self.domain_reviews,
            {
                PathologyDomain.PATHOLOGY_FORMULATION,
                PathologyDomain.SPECIALIST_INTEGRATION,
                PathologyDomain.DECISION_RELEVANT_GAPS,
            },
            INITIAL_REVIEW_STATUSES,
        )
        return self


class SpecialistClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str = Field(min_length=1)
    evidence: list[EvidencePointer] = Field(default_factory=list)


class SpecialistOpinion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    specialty: SpecialistTarget
    opinion_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    claims: list[SpecialistClaim] = Field(default_factory=list)
    confidence: PathologyConfidence
    unresolved_questions: list[str] = Field(default_factory=list)


class ChairQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)


class PathologyDiscussionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_input: SpecialtyCaseInput
    initial_assessment: PathologyInitialAssessment
    specialist_opinions: list[SpecialistOpinion] = Field(default_factory=list)
    chair_questions: list[ChairQuestion] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_string_questions(cls, value):
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        migrated["chair_questions"] = [
            {"question_id": f"chair_q_{index:03d}", "question": item}
            if isinstance(item, str)
            else item
            for index, item in enumerate(value.get("chair_questions") or [], start=1)
        ]
        return migrated


class MappedSpecialistFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opinion_id: str = Field(min_length=1)
    relationship: Literal["concordant", "supplementary", "conflicting", "unresolved"]
    affected_domains: list[PathologyDomain] = Field(min_length=1)
    pathology_effect: str = Field(min_length=1)
    evidence: list[EvidencePointer] = Field(default_factory=list)


class DiscussionEvidenceMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    specialist_opinions_used: list[str] = Field(default_factory=list)
    mapped_findings: list[MappedSpecialistFinding] = Field(default_factory=list)
    unresolved_conflicts: list[PathologyAssessmentItem] = Field(default_factory=list)


class DomainChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: PathologyDomain
    change_status: Literal[
        "updated",
        "reviewed_unchanged",
        "still_not_assessable",
        "still_deferred",
        "resolved",
        "not_applicable",
    ]
    initial_view: str = Field(min_length=1)
    updated_view: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class PathologyDiscussionState(PathologyClinicalState):
    phase: Literal["discussion_update"] = "discussion_update"
    domain_reviews: list[DiscussionDomainReview] = Field(min_length=10, max_length=10)


class DiscussionStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    updated_state: PathologyDiscussionState
    domain_changes: list[DomainChange] = Field(min_length=10, max_length=10)

    @model_validator(mode="after")
    def validate_changes(self):
        domains = [item.domain for item in self.domain_changes]
        if len(domains) != len(set(domains)) or set(domains) != set(INITIAL_DOMAINS):
            raise ValueError("domain_changes must cover each pathology domain exactly once")
        reviews = {item.domain: item.status for item in self.updated_state.domain_reviews}
        if any(reviews[item.domain] != item.change_status for item in self.domain_changes):
            raise ValueError("domain change status must match updated state domain review")
        return self


class ChairAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(min_length=1)
    answerability: Literal["answered", "partially_answered", "not_assessable"]
    answer: str = Field(min_length=1)
    confidence: PathologyConfidence
    reasoning_summary: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    conflicting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class DiscussionConsultOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chair_answers: list[ChairAnswer] = Field(default_factory=list)
    unresolved_conflicts: list[PathologyAssessmentItem] = Field(default_factory=list)
    diagnostic_recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class PathologyDiscussionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["pathology.v1"] = "pathology.v1"
    case_id: SkipJsonSchema[str] = ""
    phase: SkipJsonSchema[str] = "discussion_response"
    updated_state: PathologyDiscussionState
    domain_changes: list[DomainChange] = Field(min_length=10, max_length=10)
    specialist_opinions_used: list[str] = Field(default_factory=list)
    mapped_findings: list[MappedSpecialistFinding] = Field(default_factory=list)
    chair_answers: list[ChairAnswer] = Field(default_factory=list)
    unresolved_conflicts: list[PathologyAssessmentItem] = Field(default_factory=list)
    diagnostic_recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


def _require_stage_domains(
    reviews: list[DomainReview],
    expected: set[PathologyDomain],
    allowed_statuses: set[str],
) -> None:
    domains = [item.domain for item in reviews]
    if len(domains) != len(set(domains)) or set(domains) != expected:
        raise ValueError(f"Stage must review exactly these pathology domains: {sorted(expected)}")
    invalid = [item.status for item in reviews if item.status not in allowed_statuses]
    if invalid:
        raise ValueError(f"Invalid pathology stage review statuses: {invalid}")
