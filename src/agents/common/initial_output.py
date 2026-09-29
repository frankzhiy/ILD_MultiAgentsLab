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
EvidenceDirection = Literal["supports", "weakens", "neutral"]
EvidenceFunction = Literal[
    "foundational",
    "discriminating",
    "qualifying",
    "background",
]

LEGACY_EVIDENCE_ROLE_RELATIONS = {
    "supporting": ("supports", "foundational"),
    "weakening": ("weakens", "foundational"),
    "discriminating": ("neutral", "discriminating"),
    "qualifying": ("neutral", "qualifying"),
    "background": ("neutral", "background"),
}


def legacy_role_for_evidence_relation(
    direction: EvidenceDirection,
    function: EvidenceFunction,
) -> str:
    if function != "foundational":
        return function
    return "weakening" if direction == "weakens" else "supporting"


class CaseEvidencePointer(BaseModel):
    """LLM selects one evidence block; source location is resolved locally."""

    model_config = ConfigDict(extra="forbid")

    evidence_ids: list[str] = Field(
        min_length=1,
        description=(
            "填写同一 Graph Unit 内一个或多个病例 evidence block ID；"
            "同一证据图不要按 Evidence ID 拆成多个指针。"
        ),
    )
    segment_id: SkipJsonSchema[str] = ""
    graph_unit_id: SkipJsonSchema[str] = ""
    node_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)
    quote: SkipJsonSchema[str] = ""


class EvidenceRelation(CaseEvidencePointer):
    """One case-evidence locator with separate directional and functional meaning."""

    target_claim_id: SkipJsonSchema[str] = ""
    direction: EvidenceDirection
    function: EvidenceFunction

    @model_validator(mode="after")
    def validate_dimensions(self):
        if self.function == "background" and self.direction != "neutral":
            raise ValueError("background evidence must have direction='neutral'")
        if self.function == "foundational" and self.direction == "neutral":
            raise ValueError("foundational evidence must support or weaken the assessment")
        return self


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_relations: list[EvidenceRelation] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_role_lists(cls, value):
        if not isinstance(value, dict) or "evidence_relations" in value:
            return value
        migrated = dict(value)
        relations = []
        for role, (direction, function) in LEGACY_EVIDENCE_ROLE_RELATIONS.items():
            for pointer in migrated.pop(role, []) or []:
                relations.append(
                    {
                        **pointer,
                        "direction": direction,
                        "function": function,
                    }
                )
        migrated["evidence_relations"] = relations
        return migrated


class SpecialtyAtomicClaim(BaseModel):
    """One program-addressable proposition within a specialty assessment."""

    model_config = ConfigDict(extra="forbid")

    claim_id: SkipJsonSchema[str] = ""
    statement: str = Field(min_length=1)


ProfessionalLevel = Literal[
    "observation",
    "morphologic_pattern",
    "disease_diagnosis",
    "etiologic_attribution",
    "severity_or_trajectory",
    "assessability",
]


class JudgmentTimeframe(BaseModel):
    """The time interval for which a specialty judgment is intended to hold."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "current",
        "historical",
        "longitudinal",
        "specified_period",
        "unknown",
    ] = "current"
    start: str = ""
    end: str = ""
    description: str = "基于当前病例所提供的资料。"


class JudgmentEvidenceScope(BaseModel):
    """Program-resolved evidence scope; the model cannot invent evidence IDs."""

    model_config = ConfigDict(extra="forbid")

    evidence_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)
    source_types: SkipJsonSchema[list[str]] = Field(default_factory=list)
    scope_limitations: list[str] = Field(default_factory=list)


class JudgmentConditions(BaseModel):
    """Conditions that must travel with a specialty judgment across MDT rounds."""

    model_config = ConfigDict(extra="forbid")

    subject: str = ""
    professional_level: ProfessionalLevel = "observation"
    timeframe: JudgmentTimeframe = Field(default_factory=JudgmentTimeframe)
    evidence_scope: JudgmentEvidenceScope = Field(default_factory=JudgmentEvidenceScope)
    applicability_conditions: list[str] = Field(default_factory=list)


class SpecialtyAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def migrate_missing_atomic_claims(cls, value):
        if (
            isinstance(value, dict)
            and "claims" not in value
            and isinstance(value.get("statement"), str)
        ):
            return {
                **value,
                "claims": [{"statement": value["statement"]}],
            }
        return value

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
    claims: list[SpecialtyAtomicClaim] = Field(min_length=1)
    evidence: SkipJsonSchema[EvidenceBundle] = Field(default_factory=EvidenceBundle)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    conditions: JudgmentConditions = Field(default_factory=JudgmentConditions)


_PROFESSIONAL_LEVEL_BY_ASSESSMENT_TYPE: dict[str, ProfessionalLevel] = {
    "working_diagnosis": "disease_diagnosis",
    "morphologic_pattern": "morphologic_pattern",
    "etiologic_attribution": "etiologic_attribution",
    "severity_or_risk": "severity_or_trajectory",
    "material_evaluability": "assessability",
    "imaging_interpretation": "morphologic_pattern",
    "rheumatic_disease": "disease_diagnosis",
    "ild_attribution": "etiologic_attribution",
    "progression": "severity_or_trajectory",
    "assessability": "assessability",
    "etiologic_association": "etiologic_attribution",
    "other": "observation",
}


def synchronize_judgment_conditions(assessment: SpecialtyAssessment) -> None:
    """Bind a judgment's conditions to its validated content and evidence."""

    conditions = assessment.conditions
    conditions.subject = conditions.subject.strip() or assessment.statement
    conditions.professional_level = _PROFESSIONAL_LEVEL_BY_ASSESSMENT_TYPE[
        assessment.assessment_type
    ]
    if assessment.assessment_type == "progression" and conditions.timeframe.kind == "current":
        conditions.timeframe.kind = "longitudinal"
        if conditions.timeframe.description.strip() in {
            "",
            "基于当前病例所提供的资料。",
        }:
            conditions.timeframe.description = "基于当前病例提供的纵向病程资料。"
    elif not conditions.timeframe.description.strip():
        conditions.timeframe.description = "基于当前病例所提供的资料。"
    evidence_ids = [
        evidence_id
        for relation in assessment.evidence.evidence_relations
        for evidence_id in relation.evidence_ids
    ]
    conditions.evidence_scope.evidence_ids = list(dict.fromkeys(evidence_ids))
    conditions.evidence_scope.source_types = ["case_evidence"] if evidence_ids else []
    conditions.evidence_scope.scope_limitations = list(dict.fromkeys([
        *conditions.evidence_scope.scope_limitations,
        *assessment.limitations,
    ]))
    conditions.applicability_conditions = list(dict.fromkeys([
        *conditions.applicability_conditions,
        *assessment.limitations,
    ]))


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
