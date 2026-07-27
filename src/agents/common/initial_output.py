"""Shared formal output for a specialty's one-pass initial consultation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from src.guidelines.models import GuidelineEvidencePointer
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget


AssessmentStatus = Literal[
    "supported",
    "favored",
    "possible",
    "unclassifiable",
    "not_assessable",
    "not_applicable",
]
Assessability = Literal["assessable", "partially_assessable", "not_assessable"]
AssessmentRole = Literal[
    "primary",
    "important_alternative",
    "cannot_safely_ignore",
    "scope_or_evaluability",
]
AssessmentType = Literal[
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


class SpecialtyAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("assessment_id", "conclusion_id"),
    )
    role: AssessmentRole
    assessment_type: AssessmentType = Field(
        validation_alias=AliasChoices("assessment_type", "conclusion_type")
    )
    statement: str = Field(min_length=1)
    status: AssessmentStatus
    medical_basis: str = Field(min_length=1)
    decision_impact: str = Field(min_length=1)
    evidence: EvidenceBundle
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class InterspecialtyQuestion(BaseModel):
    """A request for another specialty to explain an existing professional view."""

    model_config = ConfigDict(extra="forbid")

    target_specialty: SpecialistTarget
    question: str = Field(
        min_length=1,
        description="请其他专科解释、澄清或限定其专业观点；不得用于索取新病例资料。",
    )
    why_it_matters: str = Field(min_length=1)
    decision_unlocked: str = Field(min_length=1)
    related_assessment_ids: list[str] = Field(default_factory=list)
    related_evidence: list[CaseEvidencePointer] = Field(default_factory=list)


class EvidenceGap(BaseModel):
    """Missing case material or information needed for a decision."""

    model_config = ConfigDict(extra="forbid")

    available_information: str = Field(min_length=1)
    missing_information: str = Field(
        min_length=1,
        description="仍需补充的影像、报告、标本、检查、病史或其他病例资料。",
    )
    why_it_matters: str = Field(min_length=1)
    decision_unlocked: str = Field(min_length=1)
    related_assessment_ids: list[str] = Field(default_factory=list)
    related_evidence: list[CaseEvidencePointer] = Field(default_factory=list)


class SpecialtyAssessments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialty_question: str = Field(min_length=1)
    assessability: Assessability
    assessments: list[SpecialtyAssessment] = Field(
        min_length=1,
        validation_alias=AliasChoices("assessments", "conclusions"),
    )
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    boundaries: list[str] = Field(min_length=1)


class InterspecialtyQuestions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[InterspecialtyQuestion] = Field(default_factory=list)


class SpecialtyInitialOutput(BaseModel):
    """The only formal first-pass specialty output exposed to consumers."""

    model_config = ConfigDict(extra="forbid")

    specialty_assessments: SpecialtyAssessments
    interspecialty_questions: InterspecialtyQuestions

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_output(cls, value):
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        legacy = migrated.pop("professional_conclusions", None)
        migrated.pop("clinical_reasoning", None)
        if "specialty_assessments" not in migrated and isinstance(legacy, dict):
            legacy = dict(legacy)
            questions = legacy.pop("interspecialty_questions", [])
            migrated["specialty_assessments"] = legacy
            migrated.setdefault("interspecialty_questions", {"questions": questions})
        elif isinstance(migrated.get("specialty_assessments"), dict):
            assessments = dict(migrated["specialty_assessments"])
            questions = assessments.pop("interspecialty_questions", None)
            migrated["specialty_assessments"] = assessments
            if questions is not None:
                migrated.setdefault("interspecialty_questions", {"questions": questions})
        return migrated

@dataclass(frozen=True, slots=True)
class SpecialtyInitialConsultResult:
    internal_state: BaseModel
    formal_output: SpecialtyInitialOutput
    trace: dict
