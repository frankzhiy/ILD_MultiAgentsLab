"""Problem-oriented v2 models for text-based thoracic-radiology consultation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from src.guidelines.models import GuidelineEvidencePointer
from src.schemas.semantic_graphing.graph_unit import MdtSpecialty
from src.schemas.specialty_agent_input import SpecialtyCaseInput


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
        "legacy_import",
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

    specialty: MdtSpecialty
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
        validation_alias=AliasChoices("review_coverage", "guide_coverage"),
    )
    specialist_questions: list[SpecialistQuestion] = Field(default_factory=list)
    action_items: list[RadiologyActionItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ThoracicRadiologyInitialAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["thoracic_radiology.v2"] = "thoracic_radiology.v2"
    legacy_import: SkipJsonSchema[bool] = Field(default=False, exclude=True)
    case_id: str = Field(min_length=1)
    phase: Literal["initial_assessment"] = "initial_assessment"
    reconstruction: InitialCaseReconstruction
    task_assessments: list[RadiologyTaskAssessment] = Field(min_length=1)
    core_answer: CoreConsultAnswer
    review_coverage: list[ReviewDomainCoverage] = Field(
        default_factory=list,
        validation_alias=AliasChoices("review_coverage", "guide_coverage"),
    )
    specialist_questions: list[SpecialistQuestion] = Field(default_factory=list)
    action_items: list[RadiologyActionItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_v1(cls, value):
        if isinstance(value, dict) and value.get("schema_version") == "thoracic_radiology.v1":
            return _migrate_legacy_initial(value)
        return value

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

    specialty: MdtSpecialty
    opinion_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    claims: list[SpecialistClaim] = Field(default_factory=list)
    confidence: ImagingConfidence
    unresolved_questions: list[str] = Field(default_factory=list)


class ChairQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)


class ThoracicRadiologyDiscussionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_input: SpecialtyCaseInput
    initial_assessment: ThoracicRadiologyInitialAssessment
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
    target_layer: Literal["reported_content", "interpretation", "decision_gap"]
    affected_tasks: list[RadiologyTask] = Field(min_length=1)
    imaging_effect: str = Field(min_length=1)
    evidence: list[EvidencePointer] = Field(default_factory=list)


class RadiologyConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    confidence: ImagingConfidence = "unknown"
    evidence: list[EvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class DiscussionEvidenceMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specialist_opinions_used: list[str] = Field(default_factory=list)
    mapped_findings: list[MappedSpecialistFinding] = Field(default_factory=list)
    unresolved_conflicts: list[RadiologyConflict] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: RadiologyTask
    change: Literal["updated", "unchanged", "resolved", "newly_activated"]
    previous_summary: str = Field(min_length=1)
    updated_assessment: RadiologyTaskAssessment
    reason: str = Field(min_length=1)
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class ChairAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    confidence: ImagingConfidence
    reasoning_summary: str = "回答依据见支持证据和相关指南。"
    supporting_evidence: list[EvidencePointer] = Field(default_factory=list)
    guideline_evidence: list[GuidelineEvidencePointer] = Field(default_factory=list)
    specialist_opinion_ids: list[str] = Field(default_factory=list)


class DiscussionUpdateAndConsult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    added_examinations: list[ImagingExamination] = Field(default_factory=list)
    added_reported_statements: list[ReportedImagingStatement] = Field(default_factory=list)
    reported_content_opinion_ids: list[str] = Field(default_factory=list)
    task_updates: list[TaskUpdate] = Field(default_factory=list)
    updated_core_answer: CoreConsultAnswer
    review_coverage: list[ReviewDomainCoverage] = Field(
        default_factory=list,
        validation_alias=AliasChoices("review_coverage", "guide_coverage"),
    )
    specialist_questions: list[SpecialistQuestion] = Field(default_factory=list)
    action_items: list[RadiologyActionItem] = Field(default_factory=list)
    chair_answers: list[ChairAnswer] = Field(default_factory=list)
    unresolved_conflicts: list[RadiologyConflict] = Field(default_factory=list)
    imaging_recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ThoracicRadiologyDiscussionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["thoracic_radiology.v2"] = "thoracic_radiology.v2"
    case_id: str = Field(min_length=1)
    phase: Literal["discussion_response"] = "discussion_response"
    updated_assessment: ThoracicRadiologyInitialAssessment
    task_changes: list[TaskUpdate] = Field(default_factory=list)
    specialist_opinions_used: list[str] = Field(default_factory=list)
    mapped_findings: list[MappedSpecialistFinding] = Field(default_factory=list)
    chair_answers: list[ChairAnswer] = Field(default_factory=list)
    unresolved_conflicts: list[RadiologyConflict] = Field(default_factory=list)
    imaging_recommendations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


def _require_unique(values: list[str], label: str) -> None:
    duplicates = sorted({item for item in values if values.count(item) > 1})
    if duplicates:
        raise ValueError(f"Duplicate {label} values: {duplicates}")


def _legacy_pointer(value: Any) -> list[dict]:
    if not value:
        return []
    return [
        {
            "graph_unit_id": item.get("graph_unit_id", ""),
            "evidence_ids": item.get("evidence_ids") or [],
        }
        for item in value
        if item.get("evidence_ids")
    ]


def _migrate_legacy_initial(value: dict) -> dict:
    """Best-effort migration so existing v1 initial JSON can enter v2 discussion."""

    source = value.get("source_state") or {}
    observations = (value.get("observation_state") or {}).get("observations") or []
    interpretation = value.get("interpretation_state") or {}
    examinations = []
    authority_map = {
        "formal_imaging_report": "formal_report",
        "report_excerpt": "report_excerpt",
        "clinician_paraphrase": "clinician_paraphrase",
        "diagnostic_label_only": "label_only",
        "unknown": "unknown",
    }
    level_map = {
        "sufficient": "feature_level",
        "partial": "impression_level",
        "insufficient": "label_only",
        "unknown": "uncertain",
    }
    for index, item in enumerate(source.get("examinations") or [], start=1):
        evidence = _legacy_pointer(item.get("supporting_evidence"))
        if not evidence:
            continue
        examinations.append(
            {
                "exam_id": item.get("exam_id") or f"legacy_exam_{index:03d}",
                "temporal_anchor": item.get("temporal_anchor") or "时间未提供",
                "modality": item.get("modality") or "unknown",
                "purpose": "unknown",
                "body_scope": "thoracic",
                "source_authority": authority_map.get(
                    item.get("source_authority"), "unknown"
                ),
                "evidence_level": level_map.get(
                    item.get("description_sufficiency"), "uncertain"
                ),
                "description": item.get("assessment") or "由v1状态迁移的检查记录。",
                "source_evidence": evidence,
                "direct_images_reviewed": False,
            }
        )
    if not examinations:
        fallback = next(
            (
                _legacy_pointer(item.get("supporting_evidence"))
                for item in observations
                if item.get("supporting_evidence")
            ),
            [],
        )
        if fallback:
            examinations.append(
                {
                    "exam_id": "legacy_exam_001",
                    "temporal_anchor": "时间未提供",
                    "modality": "unknown",
                    "purpose": "unknown",
                    "body_scope": "uncertain",
                    "source_authority": "unknown",
                    "evidence_level": "uncertain",
                    "description": "由v1观察状态迁移的检查记录。",
                    "source_evidence": fallback,
                }
            )
    exam_id = examinations[0]["exam_id"] if examinations else "legacy_exam_001"
    exam_by_unit = {
        pointer["graph_unit_id"]: exam["exam_id"]
        for exam in examinations
        for pointer in exam["source_evidence"]
    }
    statements = []
    for index, item in enumerate(observations, start=1):
        evidence = _legacy_pointer(item.get("supporting_evidence"))
        if not evidence:
            continue
        status_map = {
            "reported_present": "reported_present",
            "reported_absent": "reported_absent",
            "possible": "reported_possible",
        }
        statements.append(
            {
                "statement_id": f"legacy_statement_{index:03d}",
                "exam_id": exam_by_unit.get(
                    evidence[0].get("graph_unit_id"), exam_id
                ),
                "statement_type": "finding",
                "origin": "legacy_import",
                "text": item.get("finding") or "v1影像观察",
                "assertion_status": status_map.get(
                    item.get("status"), "reported_unknown"
                ),
                "certainty": item.get("confidence") or "unknown",
                "evidence": evidence,
            }
        )
    pattern = interpretation.get("morphologic_pattern") or {}
    longitudinal = interpretation.get("longitudinal_assessment") or {}
    tasks = [
        {
            "task": "ild_morphologic_pattern",
            "priority": "secondary",
            "activation": "active",
            "rationale": "由v1形态模式状态迁移。",
        }
    ]
    assessments = [
        {
            "task": "ild_morphologic_pattern",
            "priority": "secondary",
            "answerability": (
                "not_answerable"
                if pattern.get("classification_status") == "not_assessable"
                else "partially_answered"
            ),
            "conclusion": pattern.get("primary_pattern") or "现有文字不能可靠完成形态分型。",
            "confidence": pattern.get("confidence") or "unknown",
            "reasoning_summary": pattern.get("reasoning_summary")
            or "由v1状态迁移，建议在新问题框架下复核。",
            "reported_statement_ids": [item["statement_id"] for item in statements],
            "supporting_evidence": _legacy_pointer(pattern.get("supporting_evidence")),
            "related_evidence": _legacy_pointer(pattern.get("related_evidence")),
            "decision_impact": "保留旧版模式判断供会中参考，不能替代重新归一影像文字。",
        }
    ]
    if longitudinal:
        tasks.append(
            {
                "task": "longitudinal_change",
                "priority": "conditional",
                "activation": "active",
                "rationale": "由v1纵向状态迁移。",
            }
        )
        assessments.append(
            {
                "task": "longitudinal_change",
                "priority": "conditional",
                "answerability": (
                    "requires_comparator"
                    if longitudinal.get("status") == "requires_comparator"
                    else "partially_answered"
                ),
                "conclusion": longitudinal.get("status") or "纵向状态不可评价",
                "confidence": longitudinal.get("confidence") or "unknown",
                "reasoning_summary": longitudinal.get("reasoning_summary")
                or "由v1状态迁移。",
                "reported_statement_ids": [],
                "supporting_evidence": _legacy_pointer(
                    longitudinal.get("supporting_evidence")
                ),
                "related_evidence": _legacy_pointer(longitudinal.get("related_evidence")),
                "decision_impact": "比较资料不足时不能确认影像进展。",
            }
        )
    orientation_summary = source.get("reasoning_summary") or "由v1状态迁移。"
    coverage = [
        {
            "domain": item.get("domain"),
            "status": (
                "not_assessable"
                if item.get("status") in {"not_assessable", "requires_direct_image_review"}
                else "addressed_by_active_task"
            ),
            "rationale": item.get("rationale") or "由v1状态迁移。",
        }
        for item in value.get("domain_reviews") or []
    ]
    return {
        "schema_version": "thoracic_radiology.v2",
        "legacy_import": True,
        "case_id": value.get("case_id") or "legacy_case",
        "phase": "initial_assessment",
        "reconstruction": {
            "orientation": {
                "clinical_trigger": "由v1首轮状态迁移，原始临床触发需复核。",
                "primary_imaging_question": "现有影像文字能够支持什么结论？",
                "secondary_imaging_questions": [],
                "urgency": "unknown",
                "relevant_clinical_context": [],
                "context_evidence": [],
            },
            "examinations": examinations,
            "reported_statements": statements,
            "task_plan": tasks,
            "excluded_candidate_notes": [],
            "limitations": [orientation_summary],
        },
        "task_assessments": assessments,
        "core_answer": {
            "primary_question": "现有影像文字能够支持什么结论？",
            "answer": pattern.get("reasoning_summary") or orientation_summary,
            "confidence": pattern.get("confidence") or "unknown",
            "decision_impact": "这是v1兼容迁移结果，后续应按v2问题驱动模型复核。",
            "decisive_next_step": None,
        },
        "review_coverage": coverage,
        "specialist_questions": [
            {
                "specialty": item.get("specialty"),
                "question": item.get("question"),
                "why_it_matters": item.get("why_it_matters"),
                "related_evidence": _legacy_pointer(item.get("related_evidence")),
            }
            for item in value.get("specialist_dependencies") or []
        ],
        "action_items": [
            {
                "action": item.get("request"),
                "reason": item.get("reason"),
                "decision_unlocked": item.get("decision_unlocked"),
                "priority": "routine",
                "related_evidence": _legacy_pointer(item.get("related_evidence")),
            }
            for item in value.get("direct_review_requests") or []
        ],
        "limitations": value.get("limitations") or [],
    }
