"""Shared formal output for a specialty's one-pass initial consultation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from src.guidelines.models import GuidelineEvidencePointer
from src.agents.common.team_synthesis import ConditionalContribution, JudgmentTimeframe
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget
from src.agents.common.clinical_concepts import (
    Assessability, Confidence, JudgmentDirection, ClinicalRole,
    DIRECTION_DESCRIPTION, CONFIDENCE_DESCRIPTION, validate_judgment_dimensions,
)


AssessmentStatus = Literal[
    "supported",
    "favored",
    "possible",
    "unclassifiable",
    "not_assessable",
    "not_applicable",
]
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
    direction: EvidenceDirection = Field(description="本条证据相对claim.statement的方向；支持一条否定陈述不等于支持疾病存在。与专科判断针对conditions.subject的direction分开。")
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



class SpecialtyAtomicClaim(BaseModel):
    """One program-addressable proposition within a specialty assessment."""

    model_config = ConfigDict(extra="forbid")

    claim_id: SkipJsonSchema[str] = ""
    statement: str = Field(min_length=1)
    evidence_relations: list[EvidenceRelation] = Field(
        default_factory=list,
        description="仅引用实际支持、削弱或限定本条判断的病例证据；无直接证据时为空列表。",
    )


ProfessionalLevel = Literal[
    "observation",
    "morphologic_pattern",
    "disease_diagnosis",
    "etiologic_attribution",
    "severity_or_trajectory",
    "assessability",
]


class JudgmentEvidenceScope(BaseModel):
    """Program-resolved evidence scope; the model cannot invent evidence IDs."""

    model_config = ConfigDict(extra="forbid")

    evidence_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)
    source_types: SkipJsonSchema[list[str]] = Field(default_factory=list)
    scope_limitations: list[str] = Field(default_factory=list)


class JudgmentConditions(BaseModel):
    """Conditions that must travel with a specialty judgment across MDT rounds."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(default="", description="中性的判断对象，例如CTD相关ILD或主要网格病变；不把‘不支持某病’作为方向判断的对象。")
    professional_level: ProfessionalLevel = "observation"
    timeframe: JudgmentTimeframe = Field(default_factory=JudgmentTimeframe)
    evidence_scope: JudgmentEvidenceScope = Field(default_factory=JudgmentEvidenceScope)
    applicability_conditions: list[str] = Field(default_factory=list)


class SpecialtyAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_id: str = Field(
        min_length=1,
    )
    role: AssessmentRole
    assessment_type: AssessmentType
    statement: str = Field(min_length=1, description="直接给出本专业最支持的判断；不附加职责声明、层级自述或泛化免责句。")
    # Read historical records verbatim; new model output uses the explicit dimensions below.
    status: SkipJsonSchema[AssessmentStatus | None] = Field(default=None, exclude_if=lambda value: value is None)
    assessability: Assessability
    direction: JudgmentDirection = Field(description=DIRECTION_DESCRIPTION)
    confidence: Confidence = Field(description=CONFIDENCE_DESCRIPTION)
    clinical_role: ClinicalRole
    medical_basis: str = Field(min_length=1)
    decision_impact: str = Field(min_length=1)
    claims: list[SpecialtyAtomicClaim] = Field(min_length=1)
    evidence: SkipJsonSchema[EvidenceBundle] = Field(default_factory=EvidenceBundle)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list, description="只列会改变当前判断的具体限制，可为空；不重复诊断正文和通用规则。")
    conditions: JudgmentConditions = Field(default_factory=JudgmentConditions)

    @model_validator(mode="after")
    def consistent_assessability(self):
        return validate_judgment_dimensions(self)


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


def synchronize_claim_evidence(assessment: SpecialtyAssessment, *, replace_evidence=False) -> None:
    """Keep the existing assessment evidence interface bound to atomic claims."""

    for claim in assessment.claims:
        for relation in claim.evidence_relations:
            relation.target_claim_id = claim.claim_id
    if replace_evidence or any(claim.evidence_relations for claim in assessment.claims):
        assessment.evidence = EvidenceBundle(evidence_relations=[
            relation.model_copy(deep=True)
            for claim in assessment.claims
            for relation in claim.evidence_relations
        ])


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
    )
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    boundaries: list[str] = Field(min_length=1)
    conditional_contributions: list[ConditionalContribution] = Field(default_factory=list)


class InterspecialtyQuestions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[InterspecialtyQuestion] = Field(default_factory=list)


class SpecialtyInitialOutput(BaseModel):
    """The only formal first-pass specialty output exposed to consumers."""

    model_config = ConfigDict(extra="forbid")

    specialty_assessments: SpecialtyAssessments
    interspecialty_questions: InterspecialtyQuestions


@dataclass(frozen=True, slots=True)
class SpecialtyInitialConsultResult:
    internal_state: BaseModel
    formal_output: SpecialtyInitialOutput
    trace: dict
