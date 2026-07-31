from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from src.agents.common.initial_output import EvidenceGap, InterspecialtyQuestion
from src.agents.mdt_chair.models import (
    AssessmentBoundary,
    ChairEvidenceBundle,
    CrossSpecialtyConflict,
    EvidenceNeed,
    SpecialtySourceCitation,
)
from src.guidelines.models import GuidelineEvidencePointer


Specialty = Literal[
    "pulmonology",
    "thoracic_radiology",
    "rheumatology",
    "pathology",
]
IssueType = Literal["question", "conflict"]
EvidenceEffect = Literal[
    "supporting",
    "weakening",
    "discriminating",
    "qualifying",
    "background",
]
ReviewOutcome = Literal[
    "accept_answer",
    "accept_boundary",
    "request_clarification",
    "request_corroboration",
    "flag_incompatibility",
    "convert_to_evidence_need",
]


class DiscussionProposition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposition_id: str
    concept_text: str
    status: str
    certainty: str
    modifiers: list[dict[str, Any]] = Field(default_factory=list)


class DiscussionEvidenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_ref: str
    segment_id: str = ""
    graph_unit_id: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    quote: str = ""
    evidence_fragments: list[dict[str, Any]] = Field(default_factory=list)
    propositions: list[DiscussionProposition] = Field(default_factory=list)
    graph_nodes: list[dict[str, Any]] = Field(default_factory=list)
    graph_edges: list[dict[str, Any]] = Field(default_factory=list)


class DiscussionTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    round_number: int = Field(ge=1, le=3)
    issue_type: IssueType
    issue_id: str
    specialty: Specialty
    prompt: str
    current_result: str = ""
    remaining_clarification: str = ""
    why_it_matters: str = ""
    prior_answers: list[dict[str, Any]] = Field(default_factory=list)
    specialty_context: list[dict[str, Any]] = Field(default_factory=list)
    evidence_candidates: list[DiscussionEvidenceCandidate] = Field(default_factory=list)


class DiscussionEvidenceUseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_ref: str
    proposition_ids: list[str] = Field(default_factory=list)
    effect: EvidenceEffect
    interpretation: str = Field(min_length=1)


class DiscussionEvidenceUse(DiscussionEvidenceUseDraft):
    evidence_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)
    segment_id: SkipJsonSchema[str] = ""
    graph_unit_id: SkipJsonSchema[str] = ""
    quote: SkipJsonSchema[str] = ""
    evidence_fragments: SkipJsonSchema[list[dict[str, Any]]] = Field(default_factory=list)
    propositions: SkipJsonSchema[list[DiscussionProposition]] = Field(default_factory=list)
    graph_nodes: SkipJsonSchema[list[dict[str, Any]]] = Field(default_factory=list)
    graph_edges: SkipJsonSchema[list[dict[str, Any]]] = Field(default_factory=list)


class DiscussionAnswerClaimDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    evidence_uses: list[DiscussionEvidenceUseDraft] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)


class DiscussionAnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    statement: str
    evidence_uses: list[DiscussionEvidenceUse] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)


class SpecialtyTaskAnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answerability: Literal["answered", "partially_answered", "not_assessable"]
    answer: str = Field(min_length=1)
    confidence: Literal["high", "moderate", "low", "unknown"]
    medical_basis: str = Field(min_length=1)
    answer_claims: list[DiscussionAnswerClaimDraft] = Field(min_length=1)
    evidence_uses: list[DiscussionEvidenceUseDraft] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    changed_from_previous: bool
    remaining_limitation: str = ""
    new_questions: list[InterspecialtyQuestion] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)


class SpecialtyTaskAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_id: str
    task_id: str
    issue_type: IssueType
    issue_id: str
    answerability: Literal["answered", "partially_answered", "not_assessable"]
    answer: str
    confidence: Literal["high", "moderate", "low", "unknown"]
    medical_basis: str
    answer_claims: list[DiscussionAnswerClaim] = Field(default_factory=list)
    evidence_uses: list[DiscussionEvidenceUse] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    changed_from_previous: bool
    remaining_limitation: str = ""
    new_questions: list[InterspecialtyQuestion] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)


class SpecialtyAnswerReviewDraft(BaseModel):
    """A question raiser's compact review of one specialty answer."""

    model_config = ConfigDict(extra="forbid")

    outcome: ReviewOutcome
    rationale: str = Field(min_length=1)
    follow_up_question: InterspecialtyQuestion | None = None
    evidence_gap: EvidenceGap | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_outcome(cls, value):
        if isinstance(value, dict) and value.get("outcome") == "identify_conflict":
            value = dict(value)
            value["outcome"] = "flag_incompatibility"
        return value


class SpecialtyAnswerReview(SpecialtyAnswerReviewDraft):
    review_id: str
    issue_id: str
    answer_id: str
    reviewer_specialty: Specialty


class SpecialtyRoundResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mdt_specialty_discussion.v1"] = "mdt_specialty_discussion.v1"
    case_id: str
    round_number: int = Field(ge=1, le=3)
    specialty: Specialty
    answers: list[SpecialtyTaskAnswer] = Field(default_factory=list)


class DiscussionRound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_number: int = Field(ge=1, le=3)
    tasks: list[DiscussionTask] = Field(default_factory=list)
    specialty_responses: list[SpecialtyRoundResponse] = Field(default_factory=list)
    answer_reviews: list[SpecialtyAnswerReview] = Field(default_factory=list)
    chair_result: dict[str, Any]
    round_decision: dict[str, Any] = Field(default_factory=dict)


DiagnosticDimension = Literal[
    "ild_presence",
    "radiologic_pattern",
    "histopathologic_pattern",
    "mdt_diagnosis",
    "etiologic_attribution",
    "disease_behavior",
    "acute_or_comorbid_factors",
]
DiagnosticStatus = Literal[
    "supported",
    "favored",
    "possible",
    "indeterminate",
    "unclassifiable",
    "not_assessable",
    "not_applicable",
]
DiagnosticConfidence = Literal["high", "moderate", "low", "unknown", "not_applicable"]
DiagnosticRole = Literal[
    "primary",
    "important_alternative",
    "cannot_safely_ignore",
    "boundary",
]


class ReportDiagnosticItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: DiagnosticDimension
    statement: str = Field(min_length=1)
    status: DiagnosticStatus
    confidence: DiagnosticConfidence
    role: DiagnosticRole
    medical_basis: str = Field(min_length=1)
    chair_item_ids: list[str] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def keep_uncertainty_semantics_distinct(self):
        if self.status == "not_assessable" and self.confidence != "unknown":
            raise ValueError("not_assessable requires unknown confidence")
        if self.status == "not_applicable" and self.confidence != "not_applicable":
            raise ValueError("not_applicable requires not_applicable confidence")
        if self.status in {"not_assessable", "not_applicable"} and self.role != "boundary":
            raise ValueError("non-assessable diagnostic items must use the boundary role")
        return self


class ReportDifferentialDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    diagnosis: str = Field(min_length=1)
    confidence: Literal["high", "moderate", "low", "unknown"]
    rationale: str = Field(min_length=1)
    chair_item_ids: list[str] = Field(min_length=1)


class ClinicalMDTReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_conclusion: str = Field(min_length=1)
    overall_confidence: DiagnosticConfidence
    integrated_summary: str = Field(min_length=1)
    diagnostic_matrix: list[ReportDiagnosticItem] = Field(min_length=7, max_length=7)
    differential_diagnoses: list[ReportDifferentialDiagnosis] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_each_diagnostic_dimension(self):
        dimensions = [item.dimension for item in self.diagnostic_matrix]
        expected = set(DiagnosticDimension.__args__)
        if len(set(dimensions)) != len(dimensions) or set(dimensions) != expected:
            raise ValueError("diagnostic_matrix must contain each diagnostic dimension exactly once")
        ranks = [item.rank for item in self.differential_diagnoses]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("differential diagnosis ranks must be consecutive from 1")
        return self


class ReportReasoningTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_statement: str
    chair_item_ids: list[str] = Field(default_factory=list)
    medical_basis: str
    source_citations: list[SpecialtySourceCitation] = Field(default_factory=list)
    evidence: ChairEvidenceBundle = Field(default_factory=ChairEvidenceBundle)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DiscussionAuditRound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_number: int = Field(ge=1, le=3)
    task_id: str
    specialty: Specialty
    prompt: str
    current_result: str = ""
    answer: str = ""
    answerability: str = ""
    confidence: str = ""
    changed_from_previous: bool = False
    reviews: list[dict[str, str]] = Field(default_factory=list)
    chair_result_after_round: str = ""
    closure: str = ""


class DiscussionDecisionAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    issue_type: IssueType
    question: str
    why_it_matters: str = ""
    baseline_result: str = ""
    rounds: list[DiscussionAuditRound] = Field(default_factory=list)
    final_status: str
    final_result: str = ""
    decision_impact: str = ""


class ConflictAuditItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    topic: str
    kind: Literal["formal_conflict", "flagged_incompatibility"]
    outcome: Literal["resolved", "unresolved", "not_confirmed_as_formal_conflict"]
    first_round: int = Field(ge=0, le=3)
    last_round: int = Field(ge=0, le=3)
    summary: str


class DiscussionAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[DiscussionDecisionAudit] = Field(default_factory=list)
    conflicts: list[ConflictAuditItem] = Field(default_factory=list)
    stop_reason: str = ""


class ResearchAuditMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnostic_claims: int = Field(default=0, ge=0)
    claims_with_specialty_citations: int = Field(default=0, ge=0)
    claims_with_patient_evidence: int = Field(default=0, ge=0)
    claims_with_guideline_citations: int = Field(default=0, ge=0)
    discussion_issues: int = Field(default=0, ge=0)
    closed_issues: int = Field(default=0, ge=0)
    formal_conflicts: int = Field(default=0, ge=0)
    resolved_formal_conflicts: int = Field(default=0, ge=0)
    unresolved_formal_conflicts: int = Field(default=0, ge=0)
    assessment_boundaries: int = Field(default=0, ge=0)


class MDTFinalReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: SkipJsonSchema[
        Literal["mdt_final_report.v2", "mdt_final_report.v3"]
    ] = "mdt_final_report.v3"
    case_id: SkipJsonSchema[str] = ""
    consensus_status: SkipJsonSchema[Literal[
        "consensus_reached",
        "consensus_with_boundaries",
        "unresolved_after_max_rounds",
        "unresolved_without_further_progress",
    ]] = "consensus_reached"
    report_scope: SkipJsonSchema[Literal["diagnostic_only"]] = "diagnostic_only"
    discussion_rounds: SkipJsonSchema[int] = 0
    clinical_report: ClinicalMDTReport
    reasoning_trace: SkipJsonSchema[list[ReportReasoningTrace]] = Field(default_factory=list)
    discussion_audit: SkipJsonSchema[DiscussionAudit] = Field(default_factory=DiscussionAudit)
    research_metrics: SkipJsonSchema[ResearchAuditMetrics] = Field(
        default_factory=ResearchAuditMetrics
    )
    assessment_boundaries: SkipJsonSchema[list[AssessmentBoundary]] = Field(default_factory=list)
    unresolved_conflicts: SkipJsonSchema[list[CrossSpecialtyConflict]] = Field(default_factory=list)
    evidence_needs: SkipJsonSchema[list[EvidenceNeed]] = Field(default_factory=list)
    legacy_source: SkipJsonSchema[bool] = False

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_report(cls, value):
        if not isinstance(value, dict) or "clinical_report" in value:
            return value
        if "primary_conclusion" not in value:
            return value
        migrated = dict(value)
        primary = migrated.pop("primary_conclusion")
        confidence_text = migrated.pop("diagnostic_confidence", "未知")
        integrated_summary = migrated.pop("integrated_summary", primary)
        discussion_summary = migrated.pop("discussion_summary", "")
        migrated.pop("evidence_basis", None)
        legacy_boundaries = migrated.pop("assessment_boundaries", [])
        migrated.pop("unresolved_conflicts", None)
        migrated.pop("evidence_needs", None)
        dimensions = list(DiagnosticDimension.__args__)
        migrated["clinical_report"] = {
            "overall_conclusion": primary,
            "overall_confidence": "unknown",
            "integrated_summary": integrated_summary,
            "diagnostic_matrix": [
                {
                    "dimension": dimension,
                    "statement": (
                        primary
                        if dimension == "mdt_diagnosis"
                        else f"旧版报告未单独记录 {dimension}。"
                    ),
                    "status": "indeterminate" if dimension == "mdt_diagnosis" else "not_assessable",
                    "confidence": "unknown",
                    "role": "primary" if dimension == "mdt_diagnosis" else "boundary",
                    "medical_basis": (
                        f"旧版信度表述：{confidence_text}"
                        if dimension == "mdt_diagnosis"
                        else "旧版报告未保留该诊断层级。"
                    ),
                    "chair_item_ids": ["LEGACY"],
                    "limitations": legacy_boundaries if dimension == "mdt_diagnosis" else [],
                }
                for dimension in dimensions
            ],
            "differential_diagnoses": [],
        }
        migrated["reasoning_trace"] = []
        migrated["discussion_audit"] = {
            "decisions": [],
            "conflicts": [],
            "stop_reason": discussion_summary,
        }
        migrated["assessment_boundaries"] = []
        migrated["unresolved_conflicts"] = []
        migrated["evidence_needs"] = []
        migrated["legacy_source"] = True
        migrated["schema_version"] = "mdt_final_report.v2"
        return migrated


class MDTDiscussionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mdt_discussion.v2"] = "mdt_discussion.v2"
    case_id: str
    baseline_sha256: str
    status: Literal["running", "completed", "failed"]
    max_rounds: int = Field(default=3, ge=1, le=3)
    rounds: list[DiscussionRound] = Field(default_factory=list)
    active_round: dict[str, Any] | None = None
    report_status: Literal["waiting", "running", "completed", "failed"] = "waiting"
    latest_chair_result: dict[str, Any]
    stop_reason: str = ""
    final_report: MDTFinalReport | None = None
    error: str = ""
