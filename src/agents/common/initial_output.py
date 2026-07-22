"""Shared formal output for a specialty's one-pass initial consultation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from src.guidelines.models import GuidelineEvidencePointer
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget


ConclusionStatus = Literal[
    "supported",
    "favored",
    "possible",
    "unclassifiable",
    "not_assessable",
    "not_applicable",
]
Assessability = Literal["assessable", "partially_assessable", "not_assessable"]
CandidateRole = Literal[
    "leading",
    "important_alternative",
    "cannot_safely_ignore",
    "not_currently_assessable",
]
ConsistencyStatus = Literal[
    "consistent",
    "partially_consistent",
    "inconsistent",
    "not_assessable",
    "not_applicable",
]
ConclusionRole = Literal[
    "primary",
    "important_alternative",
    "cannot_safely_ignore",
    "scope_or_evaluability",
]
ConclusionType = Literal[
    "working_diagnosis",
    "morphologic_pattern",
    "etiologic_attribution",
    "severity_or_risk",
    "material_evaluability",
    "imaging_interpretation",
    "rheumatic_disease",
    "ild_attribution",
    "progression",
    "assessability",
    "etiologic_association",
    "other",
]
EvidenceEffect = Literal["supports", "weakens", "discriminates", "background"]
ConsistencyDimension = Literal["mechanism", "time", "mechanism_and_time"]
BoundaryType = Literal[
    "specialty_scope",
    "missing_is_not_negative",
    "pattern_is_not_disease",
    "association_is_not_causation",
    "evidence_sufficiency",
    "material_representativeness",
    "other",
]


class CaseEvidencePointer(BaseModel):
    """LLM selects one evidence block; source location is resolved locally."""

    model_config = ConfigDict(extra="forbid")

    evidence_ids: list[str] = Field(
        min_length=1,
        max_length=1,
        description="只填写一个病例 evidence block ID；多个证据使用多个指针。",
    )
    segment_id: SkipJsonSchema[str] = ""
    graph_unit_id: SkipJsonSchema[str] = ""
    node_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)
    quote: SkipJsonSchema[str] = ""


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supporting: list[CaseEvidencePointer] = Field(default_factory=list)
    weakening: list[CaseEvidencePointer] = Field(default_factory=list)
    discriminating: list[CaseEvidencePointer] = Field(default_factory=list)
    background: list[CaseEvidencePointer] = Field(default_factory=list)


class ProfessionalConclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusion_id: str = Field(min_length=1)
    role: ConclusionRole
    conclusion_type: ConclusionType
    statement: str = Field(min_length=1)
    status: ConclusionStatus
    medical_basis: str = Field(min_length=1)
    decision_impact: str = Field(min_length=1)
    evidence: EvidenceBundle
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class InterspecialtyQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_specialty: SpecialistTarget
    question: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    decision_unlocked: str = Field(min_length=1)
    related_evidence: list[CaseEvidencePointer] = Field(default_factory=list)


class EvidenceGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available_information: str = Field(min_length=1)
    missing_information: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    decision_unlocked: str = Field(min_length=1)
    related_evidence: list[CaseEvidencePointer] = Field(default_factory=list)


class ProfessionalConclusions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialty_question: str = Field(min_length=1)
    assessability: Assessability
    conclusions: list[ProfessionalConclusion] = Field(min_length=1)
    interspecialty_questions: list[InterspecialtyQuestion] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    boundaries: list[str] = Field(min_length=1)


class CandidateExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    role: CandidateRole
    fit_summary: str = Field(min_length=1)
    evidence: EvidenceBundle
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    remaining_uncertainty: str = Field(min_length=1)


class EvidenceComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison_id: str = Field(min_length=1)
    effect: EvidenceEffect
    candidate_ids: list[str] = Field(min_length=1)
    interpretation: str = Field(min_length=1)
    evidence: EvidenceBundle


class ConsistencyCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(min_length=1)
    dimension: ConsistencyDimension
    status: ConsistencyStatus
    finding: str = Field(min_length=1)
    implication: str = Field(min_length=1)
    evidence: EvidenceBundle


class BoundaryReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(min_length=1)
    boundary_type: BoundaryType
    finding: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    evidence: EvidenceBundle


class ClinicalReasoning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_representation: str = Field(min_length=1)
    candidate_explanations: list[CandidateExplanation] = Field(default_factory=list)
    evidence_comparisons: list[EvidenceComparison] = Field(default_factory=list)
    consistency_checks: list[ConsistencyCheck] = Field(default_factory=list)
    boundary_reviews: list[BoundaryReview] = Field(min_length=1)
    synthesis: str = Field(min_length=1)


class SpecialtyInitialOutput(BaseModel):
    """The only formal first-pass specialty output exposed to consumers."""

    model_config = ConfigDict(extra="forbid")

    professional_conclusions: ProfessionalConclusions
    clinical_reasoning: ClinicalReasoning


@dataclass(frozen=True, slots=True)
class SpecialtyInitialConsultResult:
    internal_state: BaseModel
    formal_output: SpecialtyInitialOutput
    trace: dict
