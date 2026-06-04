from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SourceType(StrEnum):
    HISTORY = "history"
    HRCT = "hrct"
    PULMONARY_FUNCTION = "pulmonary_function"
    LABORATORY = "laboratory"
    PATHOLOGY = "pathology"
    EXPOSURE_MEDICATION = "exposure_medication"
    CLINICIAN_ASSESSMENT = "clinician_assessment"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ClassifiedSegment(BaseModel):
    segment_id: str = Field(description="Stable segment identifier, such as seg_001.")
    text: str = Field(description="Verbatim segment text from the input.")
    source_type: SourceType
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(description="Short reason for this source-type assignment.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentClassification(BaseModel):
    segments: list[ClassifiedSegment]
    detected_source_types: list[SourceType]
    notes: list[str] = Field(default_factory=list)

