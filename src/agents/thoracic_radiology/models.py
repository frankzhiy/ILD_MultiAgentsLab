"""Structured state and staged outputs for the text-based thoracic radiology agent."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import SpecialtyCaseInput


ImagingConfidence = Literal["very_high", "high", "moderate", "low", "unknown"]
AssessabilityStatus = Literal[
    "assessed",
    "partially_assessable",
    "not_assessable",
    "requires_direct_image_review",
    "requires_comparator",
    "not_applicable",
]


class ThoracicRadiologyDomain(StrEnum):
    SOURCE_AND_EVALUABILITY = "source_and_evaluability"
    IMAGING_PHENOTYPE = "imaging_phenotype"
    NATURE_AND_BURDEN = "nature_and_burden"
    MORPHOLOGIC_PATTERN = "morphologic_pattern"
    DISEASE_ASSOCIATION = "disease_association_and_differential"
    LONGITUDINAL_CHANGE = "longitudinal_change_and_acute_overlay"
    MDT_DECISION_GAPS = "mdt_decision_impact_and_gaps"


ALL_DOMAINS = tuple(ThoracicRadiologyDomain)
SOURCE_DOMAINS = {ThoracicRadiologyDomain.SOURCE_AND_EVALUABILITY}
MORPHOLOGY_DOMAINS = {
    ThoracicRadiologyDomain.IMAGING_PHENOTYPE,
    ThoracicRadiologyDomain.NATURE_AND_BURDEN,
    ThoracicRadiologyDomain.LONGITUDINAL_CHANGE,
}
FORMULATION_DOMAINS = {
    ThoracicRadiologyDomain.MORPHOLOGIC_PATTERN,
    ThoracicRadiologyDomain.DISEASE_ASSOCIATION,
    ThoracicRadiologyDomain.MDT_DECISION_GAPS,
}


class EvidencePointer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_ids: list[str] = Field(
        min_length=1,
        description=("来自同一个 graph unit 的 evidence block ID；其余定位信息由程序补全。"),
    )
    segment_id: SkipJsonSchema[str] = ""
    graph_unit_id: SkipJsonSchema[str] = ""
    node_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)
    quote: SkipJsonSchema[str] = ""


class DomainReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: ThoracicRadiologyDomain
    status: AssessabilityStatus
    rationale: str = Field(min_length=1)


class ImagingAssessmentItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment: str = Field(min_length=1)
    confidence: ImagingConfidence
    reasoning_summary: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    conflicting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class ImagingExamination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exam_id: str = Field(min_length=1)
    temporal_anchor: str = Field(min_length=1)
    modality: Literal["hrct", "ct", "chest_radiograph", "other", "unknown"]
    source_authority: Literal[
        "formal_imaging_report",
        "report_excerpt",
        "clinician_paraphrase",
        "diagnostic_label_only",
        "unknown",
    ]
    description_sufficiency: Literal["sufficient", "partial", "insufficient", "unknown"]
    technical_quality_status: Literal[
        "reported_adequate",
        "reported_limited",
        "not_assessable_from_text",
    ]
    comparison_status: Literal[
        "explicit_comparator_available",
        "comparison_mentioned_without_detail",
        "no_comparator_reported",
        "unknown",
    ]
    assessment: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)


class ImagingSourceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_mode: Literal["text_descriptions_only"] = "text_descriptions_only"
    direct_images_reviewed: Literal[False] = False
    overall_evaluability: Literal[
        "sufficient_for_pattern_assessment",
        "partially_sufficient",
        "insufficient_for_pattern_assessment",
    ]
    examinations: list[ImagingExamination] = Field(default_factory=list)
    reasoning_summary: str = Field(min_length=1)


class ImagingObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding: str = Field(min_length=1)
    category: Literal[
        "parenchymal",
        "airway",
        "pleural",
        "vascular",
        "mediastinal",
        "volume",
        "other",
    ]
    status: Literal["reported_present", "reported_absent", "possible"]
    craniocaudal_distribution: str = Field(min_length=1)
    axial_distribution: str = Field(min_length=1)
    anatomic_distribution: str = Field(min_length=1)
    confidence: ImagingConfidence
    supporting_evidence: list[EvidencePointer] = Field(min_length=1)


class DescriptionDerivedObservationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: list[ImagingObservation] = Field(default_factory=list)
    interstitial_or_alveolar: ImagingAssessmentItem | None = None
    fibrosis_assessment: ImagingAssessmentItem | None = None
    extent_and_burden: ImagingAssessmentItem | None = None
    ancillary_findings: list[ImagingAssessmentItem] = Field(default_factory=list)
    acute_overlay: ImagingAssessmentItem | None = None
    explicit_comparisons: list[ImagingAssessmentItem] = Field(default_factory=list)
    reasoning_summary: str = Field(min_length=1)


class MorphologicPatternAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework: str = Field(min_length=1)
    classification_status: Literal[
        "confident_pattern",
        "provisional_pattern",
        "unclassifiable_pattern",
        "not_assessable",
    ]
    primary_pattern: str | None = None
    coexisting_patterns: list[str] = Field(default_factory=list)
    confidence: ImagingConfidence
    reasoning_summary: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    conflicting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class ConditionalImagingClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["ipf_hrct_2022"]
    rule_source: str = Field(min_length=1)
    applicability: Literal["applicable", "not_applicable", "not_assessable"]
    applicability_basis: str = Field(min_length=1)
    category: str | None = None
    confidence: ImagingConfidence
    reasoning_summary: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class DiseaseAssociation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    disease_or_context: str = Field(min_length=1)
    relationship: Literal["supported", "possible", "less_likely", "not_assessable"]
    confidence: ImagingConfidence
    reasoning_summary: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    conflicting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class LongitudinalImagingAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_source: str = Field(min_length=1)
    status: Literal[
        "radiologic_progression",
        "stable",
        "improved",
        "mixed_change",
        "not_assessable",
        "requires_comparator",
    ]
    comparison_window: str = Field(min_length=1)
    progression_features: list[str] = Field(default_factory=list)
    acute_overlay_status: Literal["present", "absent", "possible", "not_assessable"]
    confidence: ImagingConfidence
    reasoning_summary: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    conflicting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class ImagingInterpretationState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    morphologic_pattern: MorphologicPatternAssessment | None = None
    conditional_classifications: list[ConditionalImagingClassification] = Field(
        default_factory=list
    )
    disease_associations: list[DiseaseAssociation] = Field(default_factory=list)
    longitudinal_assessment: LongitudinalImagingAssessment | None = None
    discordances: list[ImagingAssessmentItem] = Field(default_factory=list)


class ImagingDataGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_type: Literal[
        "insufficient_description",
        "uncertain_source",
        "no_longitudinal_comparator",
        "technical_quality_not_assessable",
        "missing_clinical_context",
        "uncertain_availability",
    ]
    available_information: str = Field(min_length=1)
    missing_information: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    decision_unlocked: str = Field(min_length=1)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)


class DirectReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    decision_unlocked: str = Field(min_length=1)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)


class SpecialistQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialty: MdtSpecialty
    question: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)


class ThoracicRadiologyClinicalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["thoracic_radiology.v1"] = "thoracic_radiology.v1"
    case_id: SkipJsonSchema[str] = ""
    phase: SkipJsonSchema[Literal["initial_assessment", "discussion_update"]] = "initial_assessment"
    domain_reviews: list[DomainReview] = Field(min_length=7, max_length=7)
    source_state: ImagingSourceState
    observation_state: DescriptionDerivedObservationState
    interpretation_state: ImagingInterpretationState
    specialist_dependencies: list[SpecialistQuestion] = Field(default_factory=list)
    direct_review_requests: list[DirectReviewRequest] = Field(default_factory=list)
    missing_data: list[ImagingDataGap] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_domain_coverage(self):
        _require_stage_domains(self.domain_reviews, set(ALL_DOMAINS))
        return self


class ThoracicRadiologyInitialAssessment(ThoracicRadiologyClinicalState):
    phase: Literal["initial_assessment"] = "initial_assessment"


class InitialSourceReconstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_reviews: list[DomainReview] = Field(min_length=1, max_length=1)
    source_state: ImagingSourceState
    direct_review_requests: list[DirectReviewRequest] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_stage_domains(self):
        _require_stage_domains(self.domain_reviews, SOURCE_DOMAINS)
        return self


class InitialMorphologicAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_reviews: list[DomainReview] = Field(min_length=3, max_length=3)
    observation_state: DescriptionDerivedObservationState
    longitudinal_assessment: LongitudinalImagingAssessment | None = None
    direct_review_requests: list[DirectReviewRequest] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_stage_domains(self):
        _require_stage_domains(self.domain_reviews, MORPHOLOGY_DOMAINS)
        return self


class InitialImagingFormulation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_reviews: list[DomainReview] = Field(min_length=3, max_length=3)
    morphologic_pattern: MorphologicPatternAssessment | None = None
    conditional_classifications: list[ConditionalImagingClassification] = Field(
        default_factory=list
    )
    disease_associations: list[DiseaseAssociation] = Field(default_factory=list)
    discordances: list[ImagingAssessmentItem] = Field(default_factory=list)
    specialist_dependencies: list[SpecialistQuestion] = Field(default_factory=list)
    direct_review_requests: list[DirectReviewRequest] = Field(default_factory=list)
    missing_data: list[ImagingDataGap] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_stage_domains(self):
        _require_stage_domains(self.domain_reviews, FORMULATION_DOMAINS)
        return self


class SpecialistClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1)
    evidence: list[EvidencePointer] = Field(default_factory=list)


class SpecialistOpinion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialty: MdtSpecialty
    opinion_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    claims: list[SpecialistClaim] = Field(default_factory=list)
    confidence: ImagingConfidence
    unresolved_questions: list[str] = Field(default_factory=list)


class ChairQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)


class ThoracicRadiologyDiscussionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_input: SpecialtyCaseInput
    initial_assessment: ThoracicRadiologyInitialAssessment
    specialist_opinions: list[SpecialistOpinion] = Field(default_factory=list)
    chair_questions: list[ChairQuestion] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_string_questions(cls, value):
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        migrated["chair_questions"] = [
            (
                {"question_id": f"chair_q_{index:03d}", "question": item}
                if isinstance(item, str)
                else item
            )
            for index, item in enumerate(value.get("chair_questions") or [], start=1)
        ]
        return migrated


class MappedSpecialistFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opinion_id: str = Field(min_length=1)
    relationship: Literal["concordant", "supplementary", "conflicting", "unresolved"]
    target_layer: Literal["source", "observation", "interpretation", "decision_gaps"]
    affected_domains: list[ThoracicRadiologyDomain] = Field(min_length=1)
    imaging_effect: str = Field(min_length=1)
    evidence: list[EvidencePointer] = Field(default_factory=list)


class DiscussionEvidenceMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialist_opinions_used: list[str] = Field(default_factory=list)
    mapped_findings: list[MappedSpecialistFinding] = Field(default_factory=list)
    unresolved_conflicts: list[ImagingAssessmentItem] = Field(default_factory=list)


class RadiologyDomainChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: ThoracicRadiologyDomain
    observation_delta: Literal["updated", "unchanged", "not_applicable"]
    interpretation_delta: Literal["updated", "unchanged", "not_applicable"]
    assessability_delta: Literal["improved", "worsened", "resolved", "unchanged", "not_applicable"]
    initial_view: str = Field(min_length=1)
    updated_view: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class DiscussionStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updated_state: ThoracicRadiologyClinicalState
    domain_changes: list[RadiologyDomainChange] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def validate_changes(self):
        if self.updated_state.phase != "discussion_update":
            raise ValueError("updated_state.phase must be discussion_update")
        _require_exact_domains([item.domain for item in self.domain_changes], set(ALL_DOMAINS))
        return self


class ChairAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    confidence: ImagingConfidence
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class DiscussionConsultOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chair_answers: list[ChairAnswer] = Field(default_factory=list)
    unresolved_conflicts: list[ImagingAssessmentItem] = Field(default_factory=list)
    imaging_recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ThoracicRadiologyDiscussionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["thoracic_radiology.v1"] = "thoracic_radiology.v1"
    case_id: SkipJsonSchema[str] = ""
    phase: SkipJsonSchema[Literal["discussion_response"]] = "discussion_response"
    updated_state: ThoracicRadiologyClinicalState
    domain_changes: list[RadiologyDomainChange] = Field(min_length=7, max_length=7)
    specialist_opinions_used: list[str] = Field(default_factory=list)
    mapped_findings: list[MappedSpecialistFinding] = Field(default_factory=list)
    chair_answers: list[ChairAnswer] = Field(default_factory=list)
    unresolved_conflicts: list[ImagingAssessmentItem] = Field(default_factory=list)
    imaging_recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_changes(self):
        _require_exact_domains([item.domain for item in self.domain_changes], set(ALL_DOMAINS))
        return self


def _require_stage_domains(
    reviews: list[DomainReview], expected: set[ThoracicRadiologyDomain]
) -> None:
    _require_exact_domains([item.domain for item in reviews], expected)


def _require_exact_domains(
    actual: list[ThoracicRadiologyDomain], expected: set[ThoracicRadiologyDomain]
) -> None:
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError(f"Must review exactly these domains: {sorted(expected)}")
