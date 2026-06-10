"""Event-frame definitions for Step 3 (segment graph construction), Stage 1: frame triage.

Frames are the real drivers of graph construction, not source_type. A single graph
unit may trigger multiple frames (see segment_graph_construction.md, 坑 1), and many
source_types map onto the same frame (坑 2). These definitions live here, not in the
prompt: the triage prompt only references them and injects them dynamically.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from src.schemas.semantic_graphing.document import SourceType


class GraphFrame(StrEnum):
    """Controlled, closed set of clinical event frames a graph unit can trigger."""

    SYMPTOM_EPISODE = "symptom_episode"
    ENCOUNTER = "encounter"
    EXAMINATION_REPORT = "examination_report"
    DIAGNOSIS = "diagnosis"
    TREATMENT_COURSE = "treatment_course"
    BACKGROUND_FACT = "background_fact"


class FrameDefinition(BaseModel):
    """Static, human-curated definition of a single frame.

    Used to render the triage prompt and to document each frame's intent. This is
    metadata only; it is never produced by the LLM.
    """

    frame: GraphFrame
    label_zh: str = Field(description="Chinese label for reports and prompts.")
    covers_source_types: list[SourceType] = Field(
        default_factory=list,
        description="source_types that typically map onto this frame (guidance, not a rule).",
    )
    description: str = Field(description="When this frame should be triggered.")


FRAME_DEFINITIONS: list[FrameDefinition] = [
    FrameDefinition(
        frame=GraphFrame.SYMPTOM_EPISODE,
        label_zh="症状起病/加重",
        covers_source_types=[
            SourceType.PRESENT_ILLNESS,
            SourceType.CHIEF_COMPLAINT,
        ],
        description=(
            "一次起病或症状加重的病程叙事：围绕某条症状主线的起病、演变、加重、诱因、"
            "性质、伴随与阴性症状，以及夹杂其中的自我应对与主观感受。"
        ),
    ),
    FrameDefinition(
        frame=GraphFrame.ENCOUNTER,
        label_zh="就诊/住院接触",
        covers_source_types=[
            SourceType.TREATMENT,
            SourceType.CLINICIAN_ASSESSMENT,
        ],
        description=(
            "一次就诊或住院接触事件：患者与某一医疗场所或医疗行为的一次完整接触，"
            "通常含就诊/收住，并在该次接触中发生检查、结果、当场处置与转归。"
            "常被标为 treatment 或 clinician_assessment，但其叙事核心是一次接触。"
            "消歧（叙事形态判据）：仅当原文把该事件展开为一次接触过程"
            "（出现就诊/收住/检查/当场处置/转归等可填槽位、属当前病程主线的一环）时才归此；"
            "若只是一句既往诊疗史实陈述、未展开接触过程，则归 background_fact，不归此。"
        ),
    ),
    FrameDefinition(
        frame=GraphFrame.EXAMINATION_REPORT,
        label_zh="检查报告面板",
        covers_source_types=[
            SourceType.IMAGING_FINDINGS,
            SourceType.LABORATORY_FINDINGS,
            SourceType.PULMONARY_FUNCTION_FINDINGS,
            SourceType.PATHOLOGY_FINDINGS,
        ],
        description=(
            "一份检查报告面板：影像、实验室、肺功能或病理的检查所见与发现，"
            "不论其叙事是否依附于某次具体就诊，只要含可建图的检查发现即触发。"
        ),
    ),
    FrameDefinition(
        frame=GraphFrame.DIAGNOSIS,
        label_zh="诊断判断",
        covers_source_types=[
            SourceType.CLINICIAN_ASSESSMENT,
        ],
        description=(
            "医生的诊断判断、诊断倾向、鉴别诊断、转诊或 MDT 申请、管理计划等"
            "临床判断性内容（纯判断，不含具体诊疗接触动作时）。"
        ),
    ),
    FrameDefinition(
        frame=GraphFrame.TREATMENT_COURSE,
        label_zh="独立治疗/用药",
        covers_source_types=[
            SourceType.TREATMENT,
            SourceType.MEDICATION_HISTORY,
        ],
        description=(
            "不依附于某次具体就诊的独立治疗或用药过程：长期口服用药、"
            "抗纤维化治疗、用药调整等围绕治疗本身展开的叙事。"
            "消歧（叙事形态判据）：仅当原文是脱离具体就诊接触、围绕用药/治疗本身展开的叙事时才归此；"
            "若该治疗依附于某次就诊接触（在那次接触中开具或施行），则应归入该次 encounter 的 treatments 槽位，不单独归此。"
        ),
    ),
    FrameDefinition(
        frame=GraphFrame.BACKGROUND_FACT,
        label_zh="背景史",
        covers_source_types=[
            SourceType.DEMOGRAPHICS,
            SourceType.EXPOSURE_HISTORY,
            SourceType.PAST_MEDICAL_HISTORY,
            SourceType.GENERAL_CONDITION,
        ],
        description=(
            "不进入病程时间轴的背景事实：人口学信息、暴露史、既往史/合并症、"
            "一般状态等，为所有专科提供上下文但本身不是时序事件。"
            "消歧（叙事形态判据）：既往某次诊疗/手术若只是一句史实陈述、未展开为接触过程"
            "（无检查/结果/转归等可填槽位，呈‘既往……史’的旁白语气），则归此而非 encounter；"
            "判据只看叙事形态，不看该事件是否与 ILD 相关。"
        ),
    ),
]

FRAME_DEFINITION_BY_FRAME: dict[GraphFrame, FrameDefinition] = {
    definition.frame: definition for definition in FRAME_DEFINITIONS
}


def render_frame_catalog() -> str:
    """Render the frame catalog as a Markdown block for prompt injection.

    Keeping the catalog generation here (not in the prompt file) guarantees the
    prompt always reflects the single source of truth in FRAME_DEFINITIONS.
    """
    lines: list[str] = []
    for definition in FRAME_DEFINITIONS:
        covers = ", ".join(str(item) for item in definition.covers_source_types) or "—"
        lines.append(
            f"- `{definition.frame}`（{definition.label_zh}）\n"
            f"  - 触发条件：{definition.description}\n"
            f"  - 常见 source_type：{covers}"
        )
    return "\n".join(lines)


class TriagedFrame(BaseModel):
    """A single frame the LLM judged the unit to trigger, with its reason."""

    frame: GraphFrame = Field(description="Triggered frame, must be from the controlled enum.")
    rationale: str = Field(description="Short reason, grounded in the unit text, for triggering it.")


class GraphUnitFrameTriage(BaseModel):
    """Triage result for one graph unit: which frames its raw text triggers."""

    graph_unit_id: str = Field(description="The graph unit this triage belongs to.")
    triggered_frames: list[TriagedFrame] = Field(
        min_length=1,
        description="1..N frames triggered by the unit's own raw text.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class SegmentFrameTriage(BaseModel):
    """Frame triage for all graph units inside one segment."""

    segment_id: str
    units: list[GraphUnitFrameTriage] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DocumentFrameTriage(BaseModel):
    """Document-level frame triage, parallel to DocumentGraphUnits."""

    segments: list[SegmentFrameTriage] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
