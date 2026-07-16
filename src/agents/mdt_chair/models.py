"""Structured first-pass MDT chair synthesis."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema


Specialty = Literal[
    "pulmonology",
    "thoracic_radiology",
    "rheumatology",
    "pathology",
]
Confidence = Literal["very_high", "high", "moderate", "low", "unknown"]


class SpecialtySourceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str
    specialty: Specialty
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


class CitedChairStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_refs: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    source_citations: SkipJsonSchema[list[SpecialtySourceCitation]] = Field(
        default_factory=list
    )
    case_evidence: SkipJsonSchema[list[CaseEvidenceCitation]] = Field(
        default_factory=list
    )


class EvaluationScope(CitedChairStatement):
    summary: str = Field(min_length=1)
    assessability: Literal["assessable", "partially_assessable", "not_assessable"]
    confidence: Confidence


class CoreConclusion(CitedChairStatement):
    conclusion: str = Field(min_length=1)
    confidence: Confidence


class SpecialtySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialty: Specialty
    evaluation_scope: EvaluationScope
    core_conclusions: list[CoreConclusion] = Field(min_length=1)


class ConflictPosition(CitedChairStatement):
    specialty: Specialty
    position: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class CrossSpecialtyConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    conflict_nature: Literal[
        "true_conclusion_conflict",
        "source_conflict",
        "evidence_boundary_conflict",
    ]
    positions: list[ConflictPosition] = Field(min_length=2)
    analysis: str = Field(min_length=1)
    status: Literal["unresolved"] = "unresolved"


Party = Literal[
    "pulmonology",
    "thoracic_radiology",
    "rheumatology",
    "pathology",
    "chair",
    "case_data",
]


class OpenIssue(CitedChairStatement):
    issue_id: str = Field(min_length=1)
    issue_type: Literal[
        "interspecialty_question",
        "specialty_self_issue",
        "missing_case_material",
        "chair_identified_question",
    ]
    question: str = Field(min_length=1)
    raised_by: Party
    responsible_parties: list[Party] = Field(min_length=1)
    current_barrier: str = Field(min_length=1)
    required_information_or_answer: str = Field(min_length=1)
    potential_mdt_impact: str = Field(min_length=1)
    related_conflict_ids: list[str] = Field(default_factory=list)


class MDTChairSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mdt_chair.v1"] = "mdt_chair.v1"
    case_id: SkipJsonSchema[str] = ""
    phase: Literal["initial_synthesis"] = "initial_synthesis"
    specialty_summaries: list[SpecialtySummary] = Field(min_length=4, max_length=4)
    conflicts: list[CrossSpecialtyConflict] = Field(default_factory=list)
    open_issues: list[OpenIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identifiers(self):
        specialties = [item.specialty for item in self.specialty_summaries]
        expected = {
            "pulmonology",
            "thoracic_radiology",
            "rheumatology",
            "pathology",
        }
        if set(specialties) != expected or len(specialties) != len(set(specialties)):
            raise ValueError("specialty_summaries must contain each specialty exactly once")
        conflict_ids = [item.conflict_id for item in self.conflicts]
        issue_ids = [item.issue_id for item in self.open_issues]
        if len(conflict_ids) != len(set(conflict_ids)):
            raise ValueError("conflict_id values must be unique")
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("issue_id values must be unique")
        unknown = {
            conflict_id
            for issue in self.open_issues
            for conflict_id in issue.related_conflict_ids
            if conflict_id not in set(conflict_ids)
        }
        if unknown:
            raise ValueError(f"Open issues reference unknown conflicts: {sorted(unknown)}")
        return self
