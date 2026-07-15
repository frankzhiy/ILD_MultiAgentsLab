"""Schemas shared by guideline ingestion, retrieval, agents, and reports."""

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema


class GuidelineDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guideline_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    original_title: str = Field(min_length=1)
    organization: str = Field(min_length=1)
    year: int
    language: str = Field(min_length=2)
    file: str = Field(min_length=1)
    specialties: list[str] = Field(min_length=1)


class GuidelineChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    guideline_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    organization: str = Field(min_length=1)
    year: int
    source_file: str = Field(min_length=1)
    page: int = Field(ge=1)
    section_path: list[str] = Field(default_factory=list)
    text: str = Field(min_length=1)
    document_sha256: str = Field(min_length=64, max_length=64)


class GuidelineEvidencePointer(BaseModel):
    """The LLM selects a retrieved chunk; source metadata is resolved locally."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    relevance: str = Field(min_length=1, description="该段指南与当前判断的关系。")
    application: str = Field(min_length=1, description="该段指南如何用于当前患者。")
    guideline_id: SkipJsonSchema[str] = ""
    title: SkipJsonSchema[str] = ""
    organization: SkipJsonSchema[str] = ""
    year: SkipJsonSchema[int | None] = None
    source_file: SkipJsonSchema[str] = ""
    page: SkipJsonSchema[int | None] = None
    section_path: SkipJsonSchema[list[str]] = Field(default_factory=list)
    quote: SkipJsonSchema[str] = ""


class GuidelineSearchHit(BaseModel):
    chunk: GuidelineChunk
    score: float
