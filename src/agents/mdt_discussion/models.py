from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from src.agents.common.initial_output import EvidenceGap, InterspecialtyQuestion
from src.guidelines.models import GuidelineEvidencePointer


Specialty = Literal[
    "pulmonology",
    "thoracic_radiology",
    "rheumatology",
    "pathology",
]
IssueType = Literal["question", "conflict"]
EvidenceEffect = Literal["supporting", "weakening", "discriminating", "background"]
ReviewOutcome = Literal[
    "accept_answer",
    "accept_boundary",
    "request_clarification",
    "request_corroboration",
    "identify_conflict",
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


class MDTFinalReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: SkipJsonSchema[str] = ""
    consensus_status: Literal[
        "consensus_reached",
        "consensus_with_boundaries",
        "unresolved_after_max_rounds",
    ]
    discussion_rounds: SkipJsonSchema[int] = 0
    primary_conclusion: str = Field(min_length=1)
    diagnostic_confidence: str = Field(min_length=1)
    integrated_summary: str = Field(min_length=1)
    evidence_basis: list[str] = Field(default_factory=list)
    assessment_boundaries: list[str] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)
    evidence_needs: list[str] = Field(default_factory=list)
    discussion_summary: str = Field(min_length=1)


class MDTDiscussionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mdt_discussion.v2"] = "mdt_discussion.v2"
    case_id: str
    baseline_sha256: str
    status: Literal["running", "completed", "failed"]
    max_rounds: int = 3
    rounds: list[DiscussionRound] = Field(default_factory=list)
    active_round: dict[str, Any] | None = None
    report_status: Literal["waiting", "running", "completed", "failed"] = "waiting"
    latest_chair_result: dict[str, Any]
    stop_reason: str = ""
    final_report: MDTFinalReport | None = None
    error: str = ""
