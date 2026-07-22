"""Structured cross-specialty integration produced by the MDT chair."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from src.guidelines.models import GuidelineEvidencePointer


Specialty = Literal[
    "pulmonology",
    "thoracic_radiology",
    "rheumatology",
    "pathology",
]
SourceType = Literal["native_conclusion", "native_question", "evidence_gap"]


class SpecialtySourceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str
    specialty: Specialty
    source_type: SourceType
    source_path: str
    quote: str


class CaseEvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_ref: str
    segment_id: str = ""
    graph_unit_id: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    proposition_ids: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)
    quote: str = ""


class ChairEvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supporting: list[CaseEvidenceCitation] = Field(default_factory=list)
    weakening: list[CaseEvidenceCitation] = Field(default_factory=list)
    discriminating: list[CaseEvidenceCitation] = Field(default_factory=list)
    background: list[CaseEvidenceCitation] = Field(default_factory=list)


class CitedChairStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_refs: list[str] = Field(min_length=1)
    source_citations: SkipJsonSchema[list[SpecialtySourceCitation]] = Field(
        default_factory=list
    )
    evidence: SkipJsonSchema[ChairEvidenceBundle] = Field(
        default_factory=ChairEvidenceBundle
    )
    guideline_evidence: SkipJsonSchema[list[GuidelineEvidencePointer]] = Field(
        default_factory=list
    )


class IntegratedConclusion(CitedChairStatement):
    conclusion_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    medical_basis: str = Field(min_length=1)
    decision_impact: str = Field(min_length=1)
    role: Literal[
        "primary",
        "important_alternative",
        "cannot_safely_ignore",
        "scope_or_evaluability",
    ]
    conclusion_type: Literal[
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
    status: Literal[
        "supported",
        "favored",
        "possible",
        "unclassifiable",
        "not_assessable",
        "not_applicable",
    ]
    specialties: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class QuestionAnswer(CitedChairStatement):
    specialty: Specialty
    answer: str = Field(min_length=1)


class IntegratedQuestion(CitedChairStatement):
    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    raised_by: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    target_specialties: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    answers: list[QuestionAnswer] = Field(default_factory=list)
    status: Literal["answered", "partially_answered", "unanswered", "disputed"]
    answer_summary: str = Field(min_length=1)
    remaining_clarification: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    decision_unlocked: str = Field(min_length=1)


class EvidenceNeed(CitedChairStatement):
    need_id: str = Field(min_length=1)
    status: Literal["available", "partially_available", "missing"]
    raised_by: SkipJsonSchema[list[Specialty]] = Field(default_factory=list)
    required_information: str = Field(min_length=1)
    available_information: str = Field(min_length=1)
    remaining_information: str = Field(min_length=1)
    provided_by: list[Specialty] = Field(default_factory=list)
    why_it_matters: str = Field(min_length=1)
    decision_unlocked: str = Field(min_length=1)


class MDTChairIntegration(BaseModel):
    """The chair's only three public result sections."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mdt_chair.v2"] = "mdt_chair.v2"
    case_id: SkipJsonSchema[str] = ""
    integrated_conclusions: list[IntegratedConclusion] = Field(min_length=1)
    questions: list[IntegratedQuestion] = Field(default_factory=list)
    evidence_needs: list[EvidenceNeed] = Field(default_factory=list)


# Compatibility for callers importing the former class name.
MDTChairSynthesis = MDTChairIntegration
