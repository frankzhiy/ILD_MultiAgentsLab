"""Structured clinical state and staged outputs for the pulmonology agent."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import SpecialtyCaseInput


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
            "Evidence block IDs from exactly one graph unit. When a judgment uses evidence "
            "from multiple graph units, create one EvidencePointer per unit. Locators are "
            "resolved by code."
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
        description="Concise clinical rationale, not hidden chain-of-thought.",
    )
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(
        default_factory=list,
        description=(
            "Context explaining a limitation, deferral, or question; it does not support the "
            "clinical conclusion and may include non-authoritative specialty material."
        ),
    )
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class SecondaryCauseAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause: str = Field(min_length=1)
    status: Literal["present", "absent", "possible", "not_assessable"]
    confidence: ClinicalConfidence
    reasoning_summary: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
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
        description="Diagnostic decision this information could change; prevents test shopping.",
    )
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
        description="Why this processing status is appropriate; do not invent a clinical result.",
    )


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
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class ProgressionComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: Literal["symptoms", "physiology", "radiology"]
    status: Literal["worsened", "stable", "improved", "not_assessable"]
    assessment: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
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
    rule_source: str = Field(min_length=1)
    assessment_window: str = Field(min_length=1)
    components: list[ProgressionComponent] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    reasoning_summary: str = Field(min_length=1)

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
    leading_diagnosis: str | None = None
    confidence: ClinicalConfidence
    morphologic_pattern: ClinicalAssessmentItem | None = Field(
        default=None,
        description="Imaging/pathology pattern, explicitly distinct from the disease diagnosis.",
    )
    reasoning_summary: str = Field(min_length=1)
    differential_diagnoses: list[DifferentialDiagnosis] = Field(default_factory=list)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    conflicting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_differential_ranks(self):
        ranks = [item.rank for item in self.differential_diagnoses]
        if len(ranks) != len(set(ranks)):
            raise ValueError("Differential diagnosis ranks must be unique")
        if ranks and sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("Differential diagnosis ranks must be consecutive from 1")
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

    @model_validator(mode="before")
    @classmethod
    def migrate_v1_saved_output(cls, value):
        if not isinstance(value, dict) or value.get("domain_reviews"):
            return value
        migrated = dict(value)
        migrated["schema_version"] = "pulmonology.v2"
        migrated["phase"] = "initial_assessment"
        old_differential = migrated.pop("working_differential", [])
        old_progression = migrated.pop("progression_components", [])
        old_questions = migrated.pop("questions_for_specialists", [])
        migrated["missing_data"] = [
            item.model_dump(mode="python") if isinstance(item, BaseModel) else dict(item)
            for item in migrated.get("missing_data") or []
        ]
        for gap in migrated["missing_data"]:
            gap.setdefault("gap_type", "uncertain_availability")
            gap.setdefault(
                "available_information",
                "旧版输出未明确区分已有资料与真正缺口。",
            )
            gap.setdefault("decision_unlocked", gap.get("why_it_matters", "影响诊断判断。"))
        old_questions = [
            item.model_dump(mode="python") if isinstance(item, BaseModel) else dict(item)
            for item in old_questions
        ]
        old_progression = [
            item.model_dump(mode="python") if isinstance(item, BaseModel) else dict(item)
            for item in old_progression
        ]
        old_differential = [
            item.model_dump(mode="python") if isinstance(item, BaseModel) else dict(item)
            for item in old_differential
        ]
        for question in old_questions:
            question.setdefault("why_it_matters", "需要正式专科意见以完成呼吸科诊断判断。")
        migrated["specialist_dependencies"] = old_questions
        migrated["progression_assessment"] = (
            {
                "recent_worsening": "not_assessable",
                "acute_exacerbation_status": "not_assessable",
                "ppf_status": "not_assessable",
                "rule_source": "legacy output: rule source not recorded",
                "assessment_window": "legacy output: assessment window not recorded",
                "components": [
                    {
                        "component": (
                            "symptoms"
                            if index == 0
                            else "physiology"
                            if index == 1
                            else "radiology"
                        ),
                        "status": "not_assessable",
                        "assessment": item["assessment"],
                        "supporting_evidence": item.get("supporting_evidence", []),
                        "specialist_opinion_ids": item.get("specialist_opinion_ids", []),
                    }
                    for index, item in enumerate(old_progression[:3])
                ],
                "reasoning_summary": "由旧版非结构化进展条目迁移，不能据此判定 PPF。",
            }
            if old_progression
            else None
        )
        migrated["diagnostic_formulation"] = (
            {
                "classification_status": "provisional_diagnosis",
                "leading_diagnosis": old_differential[0]["diagnosis"],
                "confidence": old_differential[0].get("confidence", "unknown"),
                "reasoning_summary": "由旧版鉴别诊断列表迁移；分类状态需在新版流程复核。",
                "differential_diagnoses": old_differential,
            }
            if old_differential
            else None
        )
        presence = {
            PulmonologyDomain.CLINICAL_PHENOTYPE: bool(migrated.get("clinical_phenotype")),
            PulmonologyDomain.SECONDARY_CAUSES: bool(migrated.get("secondary_cause_assessment")),
            PulmonologyDomain.PULMONARY_SEVERITY: bool(migrated.get("pulmonary_severity")),
            PulmonologyDomain.RESPIRATORY_TESTS: bool(
                migrated.get("respiratory_test_interpretation")
            ),
            PulmonologyDomain.SPECIALIST_INTEGRATION: bool(old_questions),
            PulmonologyDomain.PROGRESSION: bool(old_progression),
            PulmonologyDomain.DIAGNOSTIC_FORMULATION: bool(old_differential),
            PulmonologyDomain.DECISION_RELEVANT_GAPS: bool(migrated.get("missing_data")),
        }
        migrated["domain_reviews"] = [
            {
                "domain": domain,
                "status": "partially_assessable" if presence[domain] else "not_assessable",
                "rationale": "旧版输出迁移：仅确认存在相关产物，未记录完整覆盖状态。",
            }
            for domain in INITIAL_DOMAINS
        ]
        return migrated


class InitialFoundation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_reviews: list[DomainReview] = Field(min_length=2, max_length=2)
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

    domain_reviews: list[DomainReview] = Field(min_length=3, max_length=3)
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

    domain_reviews: list[DomainReview] = Field(min_length=3, max_length=3)
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


class PulmonologyDiscussionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_input: SpecialtyCaseInput
    initial_assessment: PulmonologyInitialAssessment
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
    affected_domains: list[PulmonologyDomain] = Field(min_length=1)
    clinical_effect: str = Field(min_length=1)
    evidence: list[EvidencePointer] = Field(default_factory=list)


class DiscussionEvidenceMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialist_opinions_used: list[str] = Field(default_factory=list)
    mapped_findings: list[MappedSpecialistFinding] = Field(default_factory=list)
    unresolved_conflicts: list[ClinicalAssessmentItem] = Field(default_factory=list)


class DomainChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: PulmonologyDomain
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
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class DiscussionStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updated_state: PulmonologyClinicalState
    domain_changes: list[DomainChange] = Field(min_length=8, max_length=8)

    @model_validator(mode="after")
    def validate_changes(self):
        if self.updated_state.phase != "discussion_update":
            raise ValueError("updated_state.phase must be discussion_update")
        domains = [item.domain for item in self.domain_changes]
        if len(domains) != len(set(domains)) or set(domains) != set(INITIAL_DOMAINS):
            raise ValueError("domain_changes must cover each of the eight domains exactly once")
        reviews = {item.domain: item.status for item in self.updated_state.domain_reviews}
        for change in self.domain_changes:
            if reviews[change.domain] != change.change_status:
                raise ValueError("domain change status must match updated state domain review")
        return self


class ChairAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    confidence: ClinicalConfidence
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class DiscussionConsultOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chair_answers: list[ChairAnswer] = Field(default_factory=list)
    unresolved_conflicts: list[ClinicalAssessmentItem] = Field(default_factory=list)
    diagnostic_recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class PulmonologyDiscussionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["pulmonology.v2"] = "pulmonology.v2"
    case_id: SkipJsonSchema[str] = ""
    phase: SkipJsonSchema[str] = "discussion_response"
    updated_state: PulmonologyClinicalState
    domain_changes: list[DomainChange] = Field(min_length=8, max_length=8)
    specialist_opinions_used: list[str] = Field(default_factory=list)
    mapped_findings: list[MappedSpecialistFinding] = Field(default_factory=list)
    chair_answers: list[ChairAnswer] = Field(default_factory=list)
    unresolved_conflicts: list[ClinicalAssessmentItem] = Field(default_factory=list)
    diagnostic_recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


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
