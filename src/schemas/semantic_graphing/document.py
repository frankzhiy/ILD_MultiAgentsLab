from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class SourceType(StrEnum):
    """Narrative role of an evidence block: which part of the clinical story it tells.

    This axis is intentionally modality-free. It says what the text *is doing* in the
    narrative, not which machine produced it. Specialty routing is a separate axis
    (see MdtSpecialty) decided by reading the content.
    """

    DEMOGRAPHICS = "demographics"
    CHIEF_COMPLAINT = "chief_complaint"
    PRESENT_ILLNESS = "present_illness"
    PAST_MEDICAL_HISTORY = "past_medical_history"
    EXPOSURE_HISTORY = "exposure_history"
    MEDICATION_HISTORY = "medication_history"
    GENERAL_CONDITION = "general_condition"
    PHYSICAL_EXAM = "physical_exam"
    IMAGING_FINDINGS = "imaging_findings"
    LABORATORY_FINDINGS = "laboratory_findings"
    PULMONARY_FUNCTION_FINDINGS = "pulmonary_function_findings"
    PATHOLOGY_FINDINGS = "pathology_findings"
    TREATMENT = "treatment"
    CLINICIAN_ASSESSMENT = "clinician_assessment"
    OTHER = "other"


class DiscourseUnitType(StrEnum):
    DEMOGRAPHICS_CHIEF_COMPLAINT = "demographics_chief_complaint"
    PAST_MEDICAL_HISTORY = "past_medical_history"
    CURRENT_MEDICATION = "current_medication"
    CLINICAL_EPISODE = "clinical_episode"
    GENERAL_CONDITION = "general_condition"
    STANDALONE_IMAGING_REPORT = "standalone_imaging_report"
    STANDALONE_PULMONARY_FUNCTION_REPORT = "standalone_pulmonary_function_report"
    STANDALONE_LAB_PANEL = "standalone_lab_panel"
    STANDALONE_PATHOLOGY_REPORT = "standalone_pathology_report"
    STANDALONE_TREATMENT_PLAN = "standalone_treatment_plan"
    STANDALONE_CLINICIAN_ASSESSMENT = "standalone_clinician_assessment"
    OTHER = "other"


class ClassifiedSegment(BaseModel):
    segment_id: str = Field(description="Stable segment identifier, such as seg_001.")
    text: str = Field(description="Verbatim segment text from the input.")
    unit_type: DiscourseUnitType = Field(
        description="Discourse-level reason why this span is a segment."
    )
    contained_source_types: list[SourceType] = Field(
        default_factory=list,
        description="Medical information types contained inside the segment. These do not define boundaries.",
    )
    clinical_frame: str = Field(
        description="Clinical narrative frame, such as symptom_recurrence_episode."
    )
    start_char: int = Field(ge=0, description="0-based start character offset in the raw input.")
    end_char: int = Field(ge=0, description="0-based exclusive end character offset in the raw input.")
    temporal_anchor: str | None = Field(
        default=None,
        description="Explicit temporal anchor if present, such as 8年前, 2月前, current, or null.",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(description="Short reason for this source-type assignment.")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_offsets(self) -> "ClassifiedSegment":
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self


class DocumentClassification(BaseModel):
    segments: list[ClassifiedSegment]
    detected_contained_source_types: list[SourceType] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
