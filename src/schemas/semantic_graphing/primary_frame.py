"""Primary-frame definitions for graph-unit event-nucleus organization."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.semantic_graphing.document import SourceType


class PrimaryFrame(StrEnum):
    """Closed set of event-nucleus containers used to organize a graph unit."""

    SYMPTOM_EPISODE = "symptom_episode"
    ENCOUNTER = "encounter"
    STANDALONE_EXAMINATION = "standalone_examination"
    CLINICAL_ASSESSMENT = "clinical_assessment"
    TREATMENT_COURSE = "treatment_course"
    BACKGROUND_CONTEXT = "background_context"


class PrimaryFrameDefinition(BaseModel):
    """Human-curated definition of one event-nucleus organization template."""

    frame: PrimaryFrame
    label_zh: str = Field(description="Chinese label for reports and prompts.")
    covers_source_types: list[SourceType] = Field(
        default_factory=list,
        description="Source types commonly organized by this frame; guidance only.",
    )
    description: str = Field(description="When this frame is the unit's primary frame.")


PRIMARY_FRAME_DEFINITIONS: list[PrimaryFrameDefinition] = [
    PrimaryFrameDefinition(
        frame=PrimaryFrame.SYMPTOM_EPISODE,
        label_zh="症状病程事件",
        covers_source_types=[
            SourceType.PRESENT_ILLNESS,
            SourceType.CHIEF_COMPLAINT,
        ],
        description=(
            "以一次症状起病、持续、复发或加重为事件核的连续病程叙事。症状、伴随表现、"
            "阴性表现、自我应对和主观反应均属于该症状事件内部内容。若叙事核心已经进入"
            "一次具体就诊或住院过程，则选择 encounter。"
        ),
    ),
    PrimaryFrameDefinition(
        frame=PrimaryFrame.ENCOUNTER,
        label_zh="诊疗接触事件",
        covers_source_types=[
            SourceType.PRESENT_ILLNESS,
            SourceType.TREATMENT,
            SourceType.CLINICIAN_ASSESSMENT,
        ],
        description=(
            "以患者与医疗机构或医疗服务的一次具体接触为事件核，包括门急诊、住院、手术"
            "或其他展开描述的诊疗过程。接触中发生的检查、诊断判断、治疗、即时反应和转归"
            "均作为该 encounter 的内部内容，不因此改选其他 primary frame。"
        ),
    ),
    PrimaryFrameDefinition(
        frame=PrimaryFrame.STANDALONE_EXAMINATION,
        label_zh="独立检查事件",
        covers_source_types=[
            SourceType.PHYSICAL_EXAM,
            SourceType.IMAGING_FINDINGS,
            SourceType.LABORATORY_FINDINGS,
            SourceType.PULMONARY_FUNCTION_FINDINGS,
            SourceType.PATHOLOGY_FINDINGS,
        ],
        description=(
            "以一项或一组独立检查、检验、体格检查或报告及其发现为事件核，且原文未将其"
            "组织为某次具体诊疗接触的一部分。"
        ),
    ),
    PrimaryFrameDefinition(
        frame=PrimaryFrame.CLINICAL_ASSESSMENT,
        label_zh="独立临床判断事件",
        covers_source_types=[SourceType.CLINICIAN_ASSESSMENT],
        description=(
            "以独立的医生评估、诊断判断、鉴别诊断、管理建议、转诊意见或 MDT 申请为事件核，"
            "且这些判断未被组织在一次具体诊疗接触过程中。"
        ),
    ),
    PrimaryFrameDefinition(
        frame=PrimaryFrame.TREATMENT_COURSE,
        label_zh="独立治疗过程",
        covers_source_types=[
            SourceType.TREATMENT,
            SourceType.MEDICATION_HISTORY,
        ],
        description=(
            "以脱离具体诊疗接触的治疗或用药过程为事件核，例如持续用药、治疗调整及治疗期间"
            "反应。若治疗是在一次具体就诊或住院中实施，则选择 encounter。"
        ),
    ),
    PrimaryFrameDefinition(
        frame=PrimaryFrame.BACKGROUND_CONTEXT,
        label_zh="背景上下文",
        covers_source_types=[
            SourceType.DEMOGRAPHICS,
            SourceType.EXPOSURE_HISTORY,
            SourceType.PAST_MEDICAL_HISTORY,
            SourceType.GENERAL_CONDITION,
        ],
        description=(
            "以非当前病程事件的患者背景为组织核心，包括人口学、暴露史、既往史、合并症和"
            "一般状态。仅以史实口吻提及、未展开过程的既往诊疗或手术也归入此类。"
        ),
    ),
]

PRIMARY_FRAME_DEFINITION_BY_FRAME: dict[PrimaryFrame, PrimaryFrameDefinition] = {
    definition.frame: definition for definition in PRIMARY_FRAME_DEFINITIONS
}


def render_primary_frame_catalog() -> str:
    """Render the controlled primary-frame catalog for prompt injection."""

    lines: list[str] = []
    for definition in PRIMARY_FRAME_DEFINITIONS:
        covers = ", ".join(str(item) for item in definition.covers_source_types) or "—"
        lines.append(
            f"- `{definition.frame}`（{definition.label_zh}）\n"
            f"  - 选择条件：{definition.description}\n"
            f"  - 常见 source_type（仅弱提示）：{covers}"
        )
    return "\n".join(lines)


class GraphUnitPrimaryFrame(BaseModel):
    """Primary-frame selection for one graph unit."""

    model_config = ConfigDict(extra="forbid")

    graph_unit_id: str = Field(description="Graph unit to which this selection belongs.")
    primary_frame: PrimaryFrame = Field(
        description="Single event-nucleus organization template for this graph unit."
    )
    rationale: str = Field(
        min_length=1,
        description="Short reason grounded in the unit text and its dominant event nucleus.",
    )
    boundary_warning: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Non-null only when the unit may contain multiple independent event nuclei and its "
            "boundary should be reviewed."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class SegmentPrimaryFrames(BaseModel):
    """Primary-frame selections for graph units inside one segment."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str
    units: list[GraphUnitPrimaryFrame] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DocumentPrimaryFrames(BaseModel):
    """Document-level primary-frame selections, parallel to DocumentGraphUnits."""

    model_config = ConfigDict(extra="forbid")

    segments: list[SegmentPrimaryFrames] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
