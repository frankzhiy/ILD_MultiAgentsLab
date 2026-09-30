"""Structured state and staged outputs for rheumatology ILD consultation."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from src.guidelines.models import GuidelineEvidencePointer
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget


ClinicalConfidence = Literal["very_high", "high", "moderate", "low", "unknown"]


class RheumatologyDomain(StrEnum):
    SOURCE_AND_EVALUABILITY = "source_and_evaluability"
    AUTOIMMUNE_PHENOTYPE = "autoimmune_phenotype"
    SEROLOGIC_ASSESSMENT = "serologic_assessment"
    RHEUMATIC_DISEASE_FORMULATION = "rheumatic_disease_formulation"
    ILD_ATTRIBUTION = "ild_attribution"
    ACTIVITY_AND_RISK = "activity_and_risk"
    SPECIALIST_INTEGRATION_AND_GAPS = "specialist_integration_and_gaps"


INITIAL_DOMAINS = tuple(RheumatologyDomain)
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


class ClinicalAssessmentItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment: str = Field(min_length=1)
    confidence: ClinicalConfidence
    reasoning_summary: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    conflicting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class AutoimmuneManifestation(ClinicalAssessmentItem):
    domain: Literal[
        "joint", "skin", "muscle", "vascular", "glandular", "serosal", "renal", "hematologic", "other"
    ]
    status: Literal["present", "absent", "possible", "not_assessable"]
    temporal_relationship: str = Field(min_length=1)


class SerologicFinding(ClinicalAssessmentItem):
    test_name: str = Field(min_length=1)
    interpretation: Literal[
        "supports", "weakly_supports", "does_not_support", "nonspecific", "not_assessable"
    ]
    reported_result: str = Field(min_length=1)


class DifferentialDiagnosis(ClinicalAssessmentItem):
    rank: int = Field(ge=1)
    diagnosis: str = Field(min_length=1)


class RheumaticDiseaseFormulation(ClinicalAssessmentItem):
    classification_status: Literal[
        "established_rheumatic_disease",
        "provisional_rheumatic_disease",
        "overlap_rheumatic_disease",
        "undifferentiated_autoimmune_state",
        "ipaf_classification_possible",
        "autoimmune_features_insufficient",
        "insufficient_data",
    ]
    leading_diagnosis: str | None = Field(
        default=None,
        description=(
            "classification_status 为 established_rheumatic_disease、"
            "provisional_rheumatic_disease、overlap_rheumatic_disease、"
            "undifferentiated_autoimmune_state 或 ipaf_classification_possible 时，"
            "必须填写非空工作诊断；仅 autoimmune_features_insufficient 和 "
            "insufficient_data 可以为 null。"
        ),
    )
    differential_diagnoses: list[DifferentialDiagnosis] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_formulation(self):
        ranks = [item.rank for item in self.differential_diagnoses]
        if len(ranks) != len(set(ranks)) or (ranks and sorted(ranks) != list(range(1, len(ranks) + 1))):
            raise ValueError("Differential diagnosis ranks must be consecutive from 1")
        needs_leading = self.classification_status not in {
            "autoimmune_features_insufficient", "insufficient_data"
        }
        if needs_leading and not self.leading_diagnosis:
            raise ValueError("This classification status requires a leading diagnosis")
        return self


class IldAttributionAssessment(ClinicalAssessmentItem):
    attribution_strength: Literal[
        "strongly_supported",
        "moderately_supported",
        "possible",
        "not_supported",
        "alternative_explanation_preferred",
        "not_assessable",
    ]
    alternative_explanations: list[str] = Field(default_factory=list)


class ActivityAndRiskAssessment(ClinicalAssessmentItem):
    disease_activity: Literal["active", "inactive", "uncertain", "not_assessable"]
    ild_risk: Literal["high", "intermediate", "low", "not_assessable"]
    urgent_features: list[str] = Field(default_factory=list)


class DataGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_type: Literal[
        "not_provided", "insufficient_detail", "no_longitudinal_comparator", "not_performed", "uncertain_availability"
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

    domain: RheumatologyDomain
    status: Literal[
        "assessed", "partially_assessable", "not_assessable", "deferred_to_specialist", "not_applicable",
        "updated", "reviewed_unchanged", "still_not_assessable", "still_deferred", "resolved",
    ]
    rationale: str = Field(min_length=1)


class InitialDomainReview(DomainReview):
    status: Literal[
        "assessed", "partially_assessable", "not_assessable", "deferred_to_specialist", "not_applicable"
    ]




class InitialCaseDomainReview(InitialDomainReview):
    domain: Literal["source_and_evaluability", "autoimmune_phenotype"]


class InitialAutoimmuneDomainReview(InitialDomainReview):
    domain: Literal[
        "serologic_assessment",
        "rheumatic_disease_formulation",
        "activity_and_risk",
    ]


class InitialConsultDomainReview(InitialDomainReview):
    domain: Literal["ild_attribution", "specialist_integration_and_gaps"]


class RheumatologyClinicalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["rheumatology.v1"] = "rheumatology.v1"
    case_id: SkipJsonSchema[str] = ""
    phase: Literal["initial_assessment", "discussion_update"]
    domain_reviews: list[DomainReview] = Field(min_length=7, max_length=7)
    case_orientation: ClinicalAssessmentItem | None = None
    autoimmune_manifestations: list[AutoimmuneManifestation] = Field(default_factory=list)
    serologic_findings: list[SerologicFinding] = Field(default_factory=list)
    rheumatic_disease_formulation: RheumaticDiseaseFormulation | None = None
    ild_attribution: IldAttributionAssessment | None = None
    activity_and_risk: ActivityAndRiskAssessment | None = None
    specialist_dependencies: list[SpecialistQuestion] = Field(default_factory=list)
    reference_observations: list[ReferenceObservation] = Field(default_factory=list)
    missing_data: list[DataGap] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_domain_protocol(self):
        domains = [item.domain for item in self.domain_reviews]
        if len(domains) != len(set(domains)) or set(domains) != set(INITIAL_DOMAINS):
            raise ValueError("domain_reviews must process each rheumatology domain exactly once")
        allowed = INITIAL_REVIEW_STATUSES if self.phase == "initial_assessment" else DISCUSSION_REVIEW_STATUSES
        if invalid := [item.status for item in self.domain_reviews if item.status not in allowed]:
            raise ValueError(f"Invalid {self.phase} statuses: {invalid}")
        return self


class RheumatologyInitialAssessment(RheumatologyClinicalState):
    phase: Literal["initial_assessment"] = "initial_assessment"
    domain_reviews: list[InitialDomainReview] = Field(min_length=7, max_length=7)

    @model_validator(mode="before")
    @classmethod
    def coerce_legacy_domain_reviews(cls, value):
        if not isinstance(value, dict) or not value.get("domain_reviews"):
            return value
        migrated = dict(value)
        migrated["domain_reviews"] = [
            item.model_dump(mode="python") if isinstance(item, DomainReview) else item
            for item in migrated["domain_reviews"]
        ]
        return migrated


class InitialCaseReconstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain_reviews: list[InitialCaseDomainReview] = Field(min_length=2, max_length=2)
    case_orientation: ClinicalAssessmentItem | None = None
    autoimmune_manifestations: list[AutoimmuneManifestation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_domains(self):
        _require_stage_domains(self.domain_reviews, {RheumatologyDomain.SOURCE_AND_EVALUABILITY, RheumatologyDomain.AUTOIMMUNE_PHENOTYPE}, INITIAL_REVIEW_STATUSES)
        return self


class InitialAutoimmuneAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain_reviews: list[InitialAutoimmuneDomainReview] = Field(min_length=3, max_length=3)
    serologic_findings: list[SerologicFinding] = Field(default_factory=list)
    rheumatic_disease_formulation: RheumaticDiseaseFormulation | None = None
    activity_and_risk: ActivityAndRiskAssessment | None = None
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_domains(self):
        _require_stage_domains(self.domain_reviews, {RheumatologyDomain.SEROLOGIC_ASSESSMENT, RheumatologyDomain.RHEUMATIC_DISEASE_FORMULATION, RheumatologyDomain.ACTIVITY_AND_RISK}, INITIAL_REVIEW_STATUSES)
        return self


class InitialConsultFormulation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain_reviews: list[InitialConsultDomainReview] = Field(min_length=2, max_length=2)
    ild_attribution: IldAttributionAssessment | None = None
    specialist_dependencies: list[SpecialistQuestion] = Field(default_factory=list)
    reference_observations: list[ReferenceObservation] = Field(default_factory=list)
    missing_data: list[DataGap] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_domains(self):
        _require_stage_domains(self.domain_reviews, {RheumatologyDomain.ILD_ATTRIBUTION, RheumatologyDomain.SPECIALIST_INTEGRATION_AND_GAPS}, INITIAL_REVIEW_STATUSES)
        return self


























def _require_stage_domains(reviews, expected, allowed_statuses) -> None:
    domains = [item.domain for item in reviews]
    if len(domains) != len(set(domains)) or set(domains) != expected:
        raise ValueError(f"Stage must review exactly these domains: {sorted(expected)}")
    if invalid := [item.status for item in reviews if item.status not in allowed_statuses]:
        raise ValueError(f"Invalid stage review statuses: {invalid}")
