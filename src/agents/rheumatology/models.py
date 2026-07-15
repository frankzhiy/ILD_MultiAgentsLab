"""Structured state and staged outputs for rheumatology ILD consultation."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import SpecialtyCaseInput


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
            "仅可包含同一个 graph unit 的 evidence block ID。"
            "一个判断需要多个 graph unit 时，必须创建多个 EvidencePointer。"
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
    leading_diagnosis: str | None = None
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

    specialty: MdtSpecialty
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


class InitialCaseDomainReview(DomainReview):
    domain: Literal["source_and_evaluability", "autoimmune_phenotype"]


class InitialAutoimmuneDomainReview(DomainReview):
    domain: Literal[
        "serologic_assessment",
        "rheumatic_disease_formulation",
        "activity_and_risk",
    ]


class InitialConsultDomainReview(DomainReview):
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
    confidence: ClinicalConfidence
    unresolved_questions: list[str] = Field(default_factory=list)


class ChairQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)


class RheumatologyDiscussionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_input: SpecialtyCaseInput
    initial_assessment: RheumatologyInitialAssessment
    specialist_opinions: list[SpecialistOpinion] = Field(default_factory=list)
    chair_questions: list[ChairQuestion] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_string_questions(cls, value):
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        migrated["chair_questions"] = [
            {"question_id": f"chair_q_{index:03d}", "question": item} if isinstance(item, str) else item
            for index, item in enumerate(value.get("chair_questions") or [], start=1)
        ]
        return migrated


class MappedSpecialistFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opinion_id: str = Field(min_length=1)
    relationship: Literal["concordant", "supplementary", "conflicting", "unresolved"]
    affected_domains: list[RheumatologyDomain] = Field(min_length=1)
    clinical_effect: str = Field(min_length=1)
    evidence: list[EvidencePointer] = Field(default_factory=list)


class DiscussionEvidenceMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    specialist_opinions_used: list[str] = Field(default_factory=list)
    mapped_findings: list[MappedSpecialistFinding] = Field(default_factory=list)
    unresolved_conflicts: list[ClinicalAssessmentItem] = Field(default_factory=list)


class DomainChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: RheumatologyDomain
    change_status: Literal["updated", "reviewed_unchanged", "still_not_assessable", "still_deferred", "resolved", "not_applicable"]
    initial_view: str = Field(min_length=1)
    updated_view: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class DiscussionStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    updated_state: RheumatologyClinicalState
    domain_changes: list[DomainChange] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def validate_changes(self):
        if self.updated_state.phase != "discussion_update":
            raise ValueError("updated_state.phase must be discussion_update")
        domains = [item.domain for item in self.domain_changes]
        if len(domains) != len(set(domains)) or set(domains) != set(INITIAL_DOMAINS):
            raise ValueError("domain_changes must cover each rheumatology domain exactly once")
        reviews = {item.domain: item.status for item in self.updated_state.domain_reviews}
        if any(reviews[change.domain] != change.change_status for change in self.domain_changes):
            raise ValueError("domain change status must match updated state domain review")
        return self


class ChairAnswer(ClinicalAssessmentItem):
    question_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    assessment: str = "主席问题回答"
    reasoning_summary: str = "回答依据见支持证据。"


class DiscussionConsultOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chair_answers: list[ChairAnswer] = Field(default_factory=list)
    unresolved_conflicts: list[ClinicalAssessmentItem] = Field(default_factory=list)
    diagnostic_recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class RheumatologyDiscussionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["rheumatology.v1"] = "rheumatology.v1"
    case_id: SkipJsonSchema[str] = ""
    phase: SkipJsonSchema[str] = "discussion_response"
    updated_state: RheumatologyClinicalState
    domain_changes: list[DomainChange] = Field(min_length=7, max_length=7)
    specialist_opinions_used: list[str] = Field(default_factory=list)
    mapped_findings: list[MappedSpecialistFinding] = Field(default_factory=list)
    chair_answers: list[ChairAnswer] = Field(default_factory=list)
    unresolved_conflicts: list[ClinicalAssessmentItem] = Field(default_factory=list)
    diagnostic_recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


def _require_stage_domains(reviews, expected, allowed_statuses) -> None:
    domains = [item.domain for item in reviews]
    if len(domains) != len(set(domains)) or set(domains) != expected:
        raise ValueError(f"Stage must review exactly these domains: {sorted(expected)}")
    if invalid := [item.status for item in reviews if item.status not in allowed_statuses]:
        raise ValueError(f"Invalid stage review statuses: {invalid}")
