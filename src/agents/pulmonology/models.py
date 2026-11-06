"""Structured clinical state and staged outputs for the pulmonology agent."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from src.agents.common.clinical_concepts import normalize_differential_ranks
from src.guidelines.models import GuidelineEvidencePointer
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget


ClinicalConfidence = Literal["very_high", "high", "moderate", "low", "unknown"]


class PulmonologyDomain(StrEnum):
    CLINICAL_PHENOTYPE = "clinical_phenotype"
    SECONDARY_CAUSES = "secondary_causes"
    PULMONARY_SEVERITY = "pulmonary_severity"
    RESPIRATORY_TESTS = "respiratory_tests_and_bronchoscopy"
    SPECIALIST_INTEGRATION = "specialist_integration"
    PROGRESSION = "progression"
    DIAGNOSTIC_FORMULATION = "diagnostic_formulation"
    DECISION_RELEVANT_GAPS = "decision_relevant_gaps"


INITIAL_DOMAINS = tuple(PulmonologyDomain)
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
            "evidence block ID；其余定位信息由程序补全。"
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
    reasoning_summary: str = Field(
        min_length=1,
        description="简明、可审计的临床理由，不输出隐藏思维链。",
    )
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    conflicting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(
        default_factory=list,
        description=(
            "用于解释局限、等待专科或问题背景的相关证据；不直接支持临床结论，"
            "可以包含非本专业主责资料。"
        ),
    )
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class SecondaryCauseAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause: str = Field(min_length=1)
    status: Literal["present", "absent", "possible", "not_assessable"]
    confidence: ClinicalConfidence
    reasoning_summary: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class DifferentialDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    diagnosis: str = Field(min_length=1)
    confidence: ClinicalConfidence
    reasoning_summary: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    conflicting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class DataGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_type: Literal[
        "not_provided",
        "insufficient_detail",
        "no_longitudinal_comparator",
        "not_performed",
        "uncertain_availability",
    ]
    available_information: str = Field(min_length=1)
    missing_information: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    decision_unlocked: str = Field(
        min_length=1,
        description="该信息可能改变的诊断决策，用于避免无目的检查清单。",
    )
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

    domain: PulmonologyDomain
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
    rationale: str = Field(
        min_length=1,
        description="说明为何采用该处理状态，不得虚构临床结果。",
    )


class InitialDomainReview(DomainReview):
    """首轮完整状态使用的 review；schema 不暴露会中状态。"""

    status: Literal[
        "assessed",
        "partially_assessable",
        "not_assessable",
        "deferred_to_specialist",
        "not_applicable",
    ]




class InitialFoundationReview(InitialDomainReview):
    domain: Literal[
        PulmonologyDomain.CLINICAL_PHENOTYPE,
        PulmonologyDomain.SECONDARY_CAUSES,
    ]


class InitialPulmonaryReview(InitialDomainReview):
    domain: Literal[
        PulmonologyDomain.PULMONARY_SEVERITY,
        PulmonologyDomain.RESPIRATORY_TESTS,
        PulmonologyDomain.PROGRESSION,
    ]


class InitialDiagnosticReview(InitialDomainReview):
    domain: Literal[
        PulmonologyDomain.SPECIALIST_INTEGRATION,
        PulmonologyDomain.DIAGNOSTIC_FORMULATION,
        PulmonologyDomain.DECISION_RELEVANT_GAPS,
    ]


class BronchoscopyAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal[
        "performed_and_interpreted",
        "indicated",
        "not_indicated",
        "consider_if_needed",
        "unsafe_or_contraindicated",
        "insufficient_data",
    ]
    clinical_question: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    safety_considerations: list[str] = Field(default_factory=list)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class ProgressionComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: Literal["symptoms", "physiology", "radiology"]
    status: Literal["worsened", "stable", "improved", "not_assessable"]
    assessment: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class ProgressionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recent_worsening: Literal["present", "absent", "not_assessable"]
    acute_exacerbation_status: Literal[
        "suspected", "not_supported", "not_assessable", "not_applicable"
    ]
    ppf_status: Literal[
        "meets_criteria", "does_not_meet_criteria", "not_assessable", "not_applicable"
    ]
    assessment_window: str = Field(min_length=1)
    components: list[ProgressionComponent] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    reasoning_summary: str = Field(min_length=1)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_components(self):
        names = [item.component for item in self.components]
        if len(names) != len(set(names)):
            raise ValueError("Progression components must be unique")
        return self


class DiagnosticFormulation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification_status: Literal[
        "confident_diagnosis",
        "provisional_diagnosis",
        "unclassifiable_ild",
        "insufficient_data",
    ]
    leading_diagnosis: str | None = Field(
        default=None,
        description=(
            "现有证据下最可能的具体工作诊断；确定程度在confidence中表达。"
            "classification_status 不是 insufficient_data 时必须填写非空主导诊断；"
            "insufficient_data 时可以为 null。"
        ),
    )
    confidence: ClinicalConfidence
    morphologic_pattern: ClinicalAssessmentItem | None = Field(
        default=None,
        description="影像或病理形态学模式，必须与临床疾病诊断明确区分。",
    )
    reasoning_summary: str = Field(min_length=1)
    differential_diagnoses: list[DifferentialDiagnosis] = Field(default_factory=list)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    conflicting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_differential_ranks(self):
        normalize_differential_ranks(self.differential_diagnoses)
        if self.classification_status != "insufficient_data" and not self.leading_diagnosis:
            raise ValueError("A non-insufficient diagnostic formulation needs a leading diagnosis")
        return self


class PulmonologyClinicalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["pulmonology.v2"] = "pulmonology.v2"
    case_id: SkipJsonSchema[str] = ""
    phase: Literal["initial_assessment", "discussion_update"]
    domain_reviews: list[DomainReview] = Field(min_length=8, max_length=8)
    clinical_phenotype: ClinicalAssessmentItem | None = None
    secondary_cause_assessment: list[SecondaryCauseAssessment] = Field(default_factory=list)
    pulmonary_severity: ClinicalAssessmentItem | None = None
    respiratory_test_interpretation: list[ClinicalAssessmentItem] = Field(default_factory=list)
    bronchoscopy_assessment: BronchoscopyAssessment | None = None
    specialist_dependencies: list[SpecialistQuestion] = Field(default_factory=list)
    reference_observations: list[ReferenceObservation] = Field(default_factory=list)
    progression_assessment: ProgressionAssessment | None = None
    diagnostic_formulation: DiagnosticFormulation | None = None
    missing_data: list[DataGap] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_domain_protocol(self):
        domains = [item.domain for item in self.domain_reviews]
        if len(domains) != len(set(domains)) or set(domains) != set(INITIAL_DOMAINS):
            raise ValueError("domain_reviews must process each of the eight domains exactly once")
        allowed = (
            INITIAL_REVIEW_STATUSES
            if self.phase == "initial_assessment"
            else DISCUSSION_REVIEW_STATUSES
        )
        invalid = [item.status for item in self.domain_reviews if item.status not in allowed]
        if invalid:
            raise ValueError(f"Invalid {self.phase} domain review statuses: {invalid}")
        return self


class PulmonologyInitialAssessment(PulmonologyClinicalState):
    phase: Literal["initial_assessment"] = "initial_assessment"
    domain_reviews: list[InitialDomainReview] = Field(min_length=8, max_length=8)

    @model_validator(mode="before")
    @classmethod
    def coerce_domain_reviews(cls, value):
        if not isinstance(value, dict) or not value.get("domain_reviews"):
            return value
        return {**value, "domain_reviews": [
            item.model_dump(mode="python") if isinstance(item, DomainReview) else item
            for item in value["domain_reviews"]
        ]}


class InitialFoundation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_reviews: list[InitialFoundationReview] = Field(min_length=2, max_length=2)
    clinical_phenotype: ClinicalAssessmentItem | None = None
    secondary_cause_assessment: list[SecondaryCauseAssessment] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_domains(self):
        _require_stage_domains(
            self.domain_reviews,
            {PulmonologyDomain.CLINICAL_PHENOTYPE, PulmonologyDomain.SECONDARY_CAUSES},
            INITIAL_REVIEW_STATUSES,
        )
        return self


class InitialPulmonaryAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_reviews: list[InitialPulmonaryReview] = Field(min_length=3, max_length=3)
    pulmonary_severity: ClinicalAssessmentItem | None = None
    respiratory_test_interpretation: list[ClinicalAssessmentItem] = Field(default_factory=list)
    bronchoscopy_assessment: BronchoscopyAssessment | None = None
    progression_assessment: ProgressionAssessment | None = None
    specialist_dependencies: list[SpecialistQuestion] = Field(default_factory=list)
    reference_observations: list[ReferenceObservation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_domains(self):
        _require_stage_domains(
            self.domain_reviews,
            {
                PulmonologyDomain.PULMONARY_SEVERITY,
                PulmonologyDomain.RESPIRATORY_TESTS,
                PulmonologyDomain.PROGRESSION,
            },
            INITIAL_REVIEW_STATUSES,
        )
        return self


class InitialDiagnosticFormulation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_reviews: list[InitialDiagnosticReview] = Field(min_length=3, max_length=3)
    specialist_dependencies: list[SpecialistQuestion] = Field(default_factory=list)
    reference_observations: list[ReferenceObservation] = Field(default_factory=list)
    diagnostic_formulation: DiagnosticFormulation | None = None
    missing_data: list[DataGap] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_domains(self):
        _require_stage_domains(
            self.domain_reviews,
            {
                PulmonologyDomain.SPECIALIST_INTEGRATION,
                PulmonologyDomain.DIAGNOSTIC_FORMULATION,
                PulmonologyDomain.DECISION_RELEVANT_GAPS,
            },
            INITIAL_REVIEW_STATUSES,
        )
        return self


























def _require_stage_domains(
    reviews: list[DomainReview],
    expected: set[PulmonologyDomain],
    allowed_statuses: set[str],
) -> None:
    domains = [item.domain for item in reviews]
    if len(domains) != len(set(domains)) or set(domains) != expected:
        raise ValueError(f"Stage must review exactly these domains: {sorted(expected)}")
    invalid = [item.status for item in reviews if item.status not in allowed_statuses]
    if invalid:
        raise ValueError(f"Invalid stage review statuses: {invalid}")
