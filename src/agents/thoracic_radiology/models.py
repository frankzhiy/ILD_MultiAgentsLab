"""Problem-oriented v2 models for text-based thoracic-radiology consultation."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from src.guidelines.models import GuidelineEvidencePointer
from src.schemas.semantic_graphing.graph_unit import SpecialistTarget


ImagingConfidence = Literal["very_high", "high", "moderate", "low", "unknown"]


class RadiologyTask(StrEnum):
    SOURCE_RECONCILIATION = "source_reconciliation"
    TARGETED_PULMONARY_VASCULAR = "targeted_pulmonary_vascular"
    ACUTE_PARENCHYMAL_OVERLAY = "acute_parenchymal_overlay"
    ILD_PHENOTYPE = "ild_phenotype"
    ILD_MORPHOLOGIC_PATTERN = "ild_morphologic_pattern"
    CONDITIONAL_IPF_HRCT = "conditional_ipf_hrct"
    LONGITUDINAL_CHANGE = "longitudinal_change"
    ACTIONABLE_ANCILLARY_FINDINGS = "actionable_ancillary_findings"
    OTHER = "other"


class ThoracicRadiologyDomain(StrEnum):
    SOURCE_AND_EVALUABILITY = "source_and_evaluability"
    IMAGING_PHENOTYPE = "imaging_phenotype"
    NATURE_AND_BURDEN = "nature_and_burden"
    MORPHOLOGIC_PATTERN = "morphologic_pattern"
    DISEASE_ASSOCIATION = "disease_association_and_differential"
    LONGITUDINAL_CHANGE = "longitudinal_change_and_acute_overlay"
    MDT_DECISION_GAPS = "mdt_decision_impact_and_gaps"


class ResolvedPropositionQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposition_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    quote: str


class EvidencePointer(BaseModel):
    """LLM supplies a unit and proposition IDs; all source details are resolved locally."""

    model_config = ConfigDict(extra="forbid")

    graph_unit_id: str = ""
    proposition_ids: list[str] = Field(default_factory=list)
    evidence_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)
    segment_id: SkipJsonSchema[str] = ""
    node_ids: SkipJsonSchema[list[str]] = Field(default_factory=list)
    resolved_quotes: SkipJsonSchema[list[ResolvedPropositionQuote]] = Field(
        default_factory=list
    )
    quote: SkipJsonSchema[str] = ""

    @model_validator(mode="after")
    def require_locator(self):
        if not self.proposition_ids and not self.evidence_ids:
            raise ValueError("Evidence pointer requires proposition_ids or evidence_ids")
        if len(self.proposition_ids) != len(set(self.proposition_ids)):
            raise ValueError("Evidence pointer contains duplicate proposition_ids")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Evidence pointer contains duplicate evidence_ids")
        return self


class CaseOrientation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clinical_trigger: str = Field(min_length=1)
    primary_imaging_question: str = Field(min_length=1)
    secondary_imaging_questions: list[str] = Field(default_factory=list)
    urgency: Literal["urgent", "expedited", "routine", "unknown"] = "unknown"
    relevant_clinical_context: list[str] = Field(default_factory=list)
    context_evidence: list[EvidencePointer] = Field(default_factory=list)


class ImagingExamination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exam_id: str = Field(min_length=1)
    temporal_anchor: str = Field(min_length=1)
    modality: Literal["hrct", "ct", "ctpa", "chest_radiograph", "other", "unknown"]
    purpose: Literal[
        "ild_characterization",
        "pulmonary_embolism",
        "acute_deterioration",
        "follow_up",
        "other",
        "unknown",
    ] = "unknown"
    body_scope: Literal["thoracic", "mixed", "uncertain"] = "thoracic"
    source_authority: Literal[
        "formal_report",
        "report_excerpt",
        "clinician_paraphrase",
        "label_only",
        "unknown",
    ]
    evidence_level: Literal[
        "feature_level", "impression_level", "label_only", "uncertain"
    ]
    description: str = Field(min_length=1)
    possible_same_exam_as: list[str] = Field(default_factory=list)
    relationship_note: str | None = None
    source_evidence: list[EvidencePointer] = Field(min_length=1)
    direct_images_reviewed: Literal[False] = False


class ReportedImagingStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement_id: str = Field(min_length=1)
    exam_id: str = Field(min_length=1)
    statement_type: Literal[
        "finding", "impression", "recommendation", "availability"
    ]
    origin: Literal[
        "formal_report",
        "report_excerpt",
        "clinician_paraphrase",
        "clinical_working_diagnosis",
    ]
    text: str = Field(min_length=1)
    assertion_status: Literal[
        "reported_present",
        "reported_absent",
        "reported_possible",
        "reported_historical",
        "reported_unknown",
    ]
    certainty: ImagingConfidence = "unknown"
    evidence: list[EvidencePointer] = Field(min_length=1)


class TaskPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: RadiologyTask
    priority: Literal["primary", "secondary", "conditional", "background"]
    activation: Literal["active", "conditional", "reviewed_not_applicable"]
    rationale: str = Field(min_length=1)


class InitialCaseReconstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orientation: CaseOrientation
    examinations: list[ImagingExamination] = Field(default_factory=list)
    reported_statements: list[ReportedImagingStatement] = Field(default_factory=list)
    task_plan: list[TaskPlanItem] = Field(min_length=1)
    excluded_candidate_notes: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identifiers(self):
        _require_unique([item.exam_id for item in self.examinations], "exam_id")
        _require_unique(
            [item.statement_id for item in self.reported_statements], "statement_id"
        )
        _require_unique([str(item.task) for item in self.task_plan], "task plan")
        exam_ids = {item.exam_id for item in self.examinations}
        missing = sorted(
            {item.exam_id for item in self.reported_statements} - exam_ids
        )
        if missing:
            raise ValueError(f"Reported statements reference unknown exams: {missing}")
        for exam in self.examinations:
            unknown_relations = set(exam.possible_same_exam_as) - exam_ids
            if unknown_relations:
                raise ValueError(
                    f"Exam {exam.exam_id} has unknown possible_same_exam_as values: "
                    f"{sorted(unknown_relations)}"
                )
        return self


class RadiologyTaskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: RadiologyTask
    priority: Literal["primary", "secondary", "conditional", "background"]
    answerability: Literal[
        "answered",
        "partially_answered",
        "not_answerable",
        "not_applicable",
        "requires_direct_review",
        "requires_comparator",
    ]
    conclusion: str = Field(min_length=1)
    confidence: ImagingConfidence
    reasoning_summary: str = Field(min_length=1)
    reported_statement_ids: list[str] = Field(default_factory=list)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    conflicting_evidence: list[EvidencePointer] = Field(default_factory=list)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    decision_impact: str = Field(min_length=1)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class CoreConsultAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    confidence: ImagingConfidence
    reasoning_summary: str = "依据各影像任务的推理摘要综合形成。"
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    decision_impact: str = Field(min_length=1)
    decisive_next_step: str | None = None
    supporting_evidence: SkipJsonSchema[list[EvidencePointer]] = Field(
        default_factory=list
    )
    related_evidence: SkipJsonSchema[list[EvidencePointer]] = Field(default_factory=list)


class ReviewDomainCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: ThoracicRadiologyDomain
    status: Literal[
        "addressed_by_active_task",
        "reviewed_not_applicable",
        "not_assessable",
    ]
    rationale: str = Field(min_length=1)


class SpecialistQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialty: SpecialistTarget
    question: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    related_evidence: list[EvidencePointer] = Field(default_factory=list)


class RadiologyActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    decision_unlocked: str = Field(min_length=1)
    priority: Literal["urgent", "high", "routine"] = "routine"
    related_evidence: list[EvidencePointer] = Field(default_factory=list)


class InitialConsultFormulation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_assessments: list[RadiologyTaskAssessment] = Field(min_length=1)
    core_answer: CoreConsultAnswer
    review_coverage: list[ReviewDomainCoverage] = Field(
        default_factory=list,
    )
    specialist_questions: list[SpecialistQuestion] = Field(default_factory=list)
    action_items: list[RadiologyActionItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ThoracicRadiologyInitialAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["thoracic_radiology.v2"] = "thoracic_radiology.v2"
    case_id: str = Field(min_length=1)
    phase: Literal["initial_assessment"] = "initial_assessment"
    reconstruction: InitialCaseReconstruction
    task_assessments: list[RadiologyTaskAssessment] = Field(min_length=1)
    core_answer: CoreConsultAnswer
    review_coverage: list[ReviewDomainCoverage] = Field(
        default_factory=list,
    )
    specialist_questions: list[SpecialistQuestion] = Field(default_factory=list)
    action_items: list[RadiologyActionItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tasks(self):
        _require_unique([str(item.task) for item in self.task_assessments], "task assessment")
        active = {
            item.task for item in self.reconstruction.task_plan if item.activation == "active"
        }
        assessed = {item.task for item in self.task_assessments}
        if not active.issubset(assessed):
            raise ValueError(
                f"Active tasks lack assessments: {sorted(str(item) for item in active-assessed)}"
            )
        statement_ids = {item.statement_id for item in self.reconstruction.reported_statements}
        unknown = {
            statement_id
            for item in self.task_assessments
            for statement_id in item.reported_statement_ids
            if statement_id not in statement_ids
        }
        if unknown:
            raise ValueError(f"Task assessments reference unknown statements: {sorted(unknown)}")
        return self


class SpecialistClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1)
    evidence: list[EvidencePointer] = Field(default_factory=list)


class SpecialistOpinion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialty: SpecialistTarget
    opinion_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    claims: list[SpecialistClaim] = Field(default_factory=list)
    confidence: ImagingConfidence
    unresolved_questions: list[str] = Field(default_factory=list)




















def _require_unique(values: list[str], label: str) -> None:
    duplicates = sorted({item for item in values if values.count(item) > 1})
    if duplicates:
        positions = {value: [index for index, item in enumerate(values) if item == value]
                     for value in duplicates}
        guidance = (
            " Tasks require one entry per task: merge distinct findings and rationale into "
            "the retained entry, then remove redundant entries. Changing priority or activation "
            "does not make a repeated task unique."
            if label in {"task plan", "task assessment"} else ""
        )
        raise ValueError(f"Duplicate {label} values: {duplicates}; array indexes={positions}.{guidance}")
