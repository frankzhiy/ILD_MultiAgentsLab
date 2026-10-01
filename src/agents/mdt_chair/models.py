"""Source locators used by the current team synthesis."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from src.agents.common.team_synthesis import Specialty
SourceType = Literal["specialty_assessment", "discussion_answer", "interspecialty_question", "assessment_evidence_need", "conditional_contribution"]

class SpecialtySourceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str
    specialty: Specialty
    source_type: SourceType
    source_subtype: str = ""
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
