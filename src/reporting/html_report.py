from collections import Counter
from html import escape
import json
from pathlib import Path
from typing import Any

from src.agents.semantic_graphing.agent import ClassificationRunResult
from src.schemas.semantic_graphing.clinical_proposition import (
    DocumentClinicalPropositions,
)
from src.schemas.semantic_graphing.graph_unit import DocumentGraphUnits
from src.schemas.semantic_graphing.local_graph import DocumentLocalGraphs
from src.schemas.semantic_graphing.primary_frame import DocumentPrimaryFrames
from src.schemas.semantic_graphing.proposition_validation import (
    DocumentPropositionValidation,
)


PRIMARY_FRAME_ZH: dict[str, str] = {
    "symptom_episode": "症状病程事件",
    "encounter": "诊疗接触事件",
    "standalone_examination": "独立检查事件",
    "clinical_assessment": "独立临床判断事件",
    "treatment_course": "独立治疗过程",
    "background_context": "背景上下文",
}

PRIMARY_FRAME_COLORS: dict[str, str] = {
    "symptom_episode": "#bfdbfe",
    "encounter": "#fed7aa",
    "standalone_examination": "#c7d2fe",
    "clinical_assessment": "#fdba74",
    "treatment_course": "#fde68a",
    "background_context": "#e5e7eb",
}


SOURCE_TYPE_ZH: dict[str, str] = {
    "demographics": "人口学信息",
    "chief_complaint": "主诉/入院主因",
    "present_illness": "现病史/病程",
    "past_medical_history": "既往史/合并症",
    "exposure_history": "暴露史",
    "medication_history": "用药史",
    "general_condition": "一般状态",
    "physical_exam": "体格检查",
    "imaging_findings": "影像发现",
    "laboratory_findings": "实验室发现",
    "pulmonary_function_findings": "肺功能发现",
    "pathology_findings": "病理发现",
    "treatment": "治疗",
    "clinician_assessment": "医生评估",
    "other": "其他",
}

SOURCE_TYPE_COLORS: dict[str, str] = {
    "demographics": "#e5e7eb",
    "chief_complaint": "#a7f3d0",
    "present_illness": "#bfdbfe",
    "past_medical_history": "#bbf7d0",
    "exposure_history": "#ddd6fe",
    "medication_history": "#fde68a",
    "general_condition": "#d1fae5",
    "physical_exam": "#fecaca",
    "imaging_findings": "#c7d2fe",
    "laboratory_findings": "#d9f99d",
    "pulmonary_function_findings": "#bae6fd",
    "pathology_findings": "#f5d0fe",
    "treatment": "#fed7aa",
    "clinician_assessment": "#fdba74",
    "other": "#e5e7eb",
}

MDT_SPECIALTY_ZH: dict[str, str] = {
    "pulmonology": "呼吸科",
    "thoracic_radiology": "胸部影像",
    "pathology": "病理科",
    "rheumatology": "风湿免疫",
    "occupational_environmental": "职业与环境",
    "shared_context": "共享背景",
    "other": "其他",
}

MDT_SPECIALTY_COLORS: dict[str, str] = {
    "pulmonology": "#bae6fd",
    "thoracic_radiology": "#c7d2fe",
    "pathology": "#f5d0fe",
    "rheumatology": "#fecaca",
    "occupational_environmental": "#ddd6fe",
    "shared_context": "#e5e7eb",
    "other": "#e5e7eb",
}

SEGMENT_PALETTE: list[str] = [
    "#bfdbfe",
    "#bbf7d0",
    "#fed7aa",
    "#c7d2fe",
    "#fecaca",
    "#d9f99d",
    "#f5d0fe",
    "#bae6fd",
    "#ddd6fe",
    "#a7f3d0",
]


def _segment_color(index: int) -> str:
    return SEGMENT_PALETTE[index % len(SEGMENT_PALETTE)]


def render_report(
    result: ClassificationRunResult,
    graph_units: DocumentGraphUnits,
    *,
    source_filename: str,
    raw_text: str,
    timing: dict[str, Any],
    output_path: str | Path,
    primary_frames: DocumentPrimaryFrames | None = None,
    clinical_propositions: DocumentClinicalPropositions | None = None,
    proposition_validation: DocumentPropositionValidation | None = None,
    local_graphs: DocumentLocalGraphs | None = None,
) -> Path:
    """Render a single HTML report covering the full run pipeline."""
    html = _render_html(
        result=result,
        graph_units=graph_units,
        source_filename=source_filename,
        raw_text=raw_text,
        timing=timing,
        primary_frames=primary_frames,
        clinical_propositions=clinical_propositions,
        proposition_validation=proposition_validation,
        local_graphs=local_graphs,
    )
    path = Path(output_path)
    path.write_text(html, encoding="utf-8")
    return path


def _render_html(
    *,
    result: ClassificationRunResult,
    graph_units: DocumentGraphUnits,
    source_filename: str,
    raw_text: str,
    timing: dict[str, Any],
    primary_frames: DocumentPrimaryFrames | None = None,
    clinical_propositions: DocumentClinicalPropositions | None = None,
    proposition_validation: DocumentPropositionValidation | None = None,
    local_graphs: DocumentLocalGraphs | None = None,
) -> str:
    classification = result.classification
    segments = classification.segments
    segment_by_id = {segment.segment_id: segment for segment in segments}

    total_units = sum(len(item.graph_units) for item in graph_units.segments)
    source_counts = Counter(
        str(unit.source_type)
        for item in graph_units.segments
        for unit in item.graph_units
    )
    specialty_counts = Counter(
        str(specialty)
        for item in graph_units.segments
        for unit in item.graph_units
        for specialty in unit.mdt_specialty
    )
    detected_contained = [str(item) for item in classification.detected_contained_source_types]

    primary_frame_by_unit: dict[str, Any] = {}
    primary_frame_counts: Counter = Counter()
    if primary_frames is not None:
        for seg in primary_frames.segments:
            for unit in seg.units:
                primary_frame_by_unit[unit.graph_unit_id] = unit
                primary_frame_counts[str(unit.primary_frame)] += 1

    propositions_by_unit: dict[str, Any] = {}
    proposition_count = 0
    modifier_count = 0
    if clinical_propositions is not None:
        for seg in clinical_propositions.segments:
            for unit in seg.units:
                propositions_by_unit[unit.graph_unit_id] = unit
                proposition_count += len(unit.propositions)
                modifier_count += len(unit.event_modifiers) + sum(
                    len(proposition.modifiers) for proposition in unit.propositions
                )

    validation_by_unit: dict[str, Any] = {}
    if proposition_validation is not None:
        for seg in proposition_validation.segments:
            for unit in seg.units:
                validation_by_unit[unit.graph_unit_id] = unit

    local_graph_by_unit: dict[str, Any] = {}
    if local_graphs is not None:
        for seg in local_graphs.segments:
            for unit in seg.units:
                local_graph_by_unit[unit.graph_unit_id] = unit

    total_elapsed = timing.get("total_elapsed_seconds")
    elapsed_text = f"{total_elapsed:.2f}s" if isinstance(total_elapsed, int | float) else "N/A"
    highlighted_text = _render_highlighted_raw_text(raw_text, segments)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{escape(result.case_id)} 语义图谱报告</title>
  <script src="https://unpkg.com/cytoscape@3.30.4/dist/cytoscape.min.js"></script>
  <style>
    body {{ margin: 32px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            line-height: 1.7; color: #1f2937; background: #f8fafc; }}
    main {{ max-width: 1500px; margin: 0 auto; }}
    h1 {{ margin-bottom: 8px; font-size: 28px; color: #0f172a; }}
    h2 {{ margin-top: 40px; font-size: 20px; border-bottom: 2px solid #94a3b8; padding-bottom: 8px; color: #334155; }}
    h3 {{ margin: 18px 0 10px; font-size: 16px; color: #475569; font-weight: 600; }}
    .meta {{ margin-bottom: 24px; color: #475569; font-size: 14px; line-height: 1.8; }}
    .note {{ color: #64748b; font-size: 13px; margin: 8px 0 16px; font-style: italic; }}
    .stat-badge {{ display: inline-block; background: #e2e8f0; border-radius: 4px;
                   padding: 3px 10px; font-size: 13px; margin-right: 6px; font-weight: 500; }}
    .segment-card {{ margin: 20px 0; padding: 0; border-left: 5px solid; border-radius: 6px;
                     background: #ffffff; box-shadow: 0 2px 4px rgba(0,0,0,0.06); overflow: hidden; }}
    .segment-header {{ padding: 16px 20px; background: #f8fafc; border-bottom: 1px solid #e2e8f0; }}
    .segment-header h3 {{ margin: 0 0 8px; font-size: 17px; color: #0f172a; }}
    .segment-info {{ font-size: 12px; color: #64748b; line-height: 1.6; }}
    .segment-text {{ padding: 16px 20px; background: #ffffff; border-bottom: 1px solid #f1f5f9; }}
    .segment-text pre {{ margin: 0; white-space: pre-wrap; font-family: ui-monospace, Menlo, monospace; 
                        font-size: 14px; line-height: 1.7; color: #1e293b; background: none; padding: 0; }}
    .units-container {{ padding: 16px 20px; }}
    .unit {{ margin: 12px 0; padding: 14px; border: 1px solid #e2e8f0; border-radius: 6px;
             background: #fafbfc; }}
    .unit-id {{ font-weight: 700; font-size: 13px; color: #0f172a; margin-bottom: 8px; }}
    .unit-meta {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 500; }}
    .badge-spec {{ display: inline-flex; align-items: center; gap: 4px; padding: 1px 9px;
                   border-radius: 999px; font-size: 12px; font-weight: 600; background: #ffffff;
                   border: 1.5px solid #94a3b8; color: #1f2937; }}
    .badge-spec::before {{ content: "\\1F3E5"; font-size: 11px; }}
    .badge-frame {{ font-weight: 600; border: 1px solid rgba(15,23,42,0.15); }}
    .frame-row {{ margin: 4px 0 8px; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }}
    .frame-label {{ font-size: 12px; color: #475569; }}
    .frame-list {{ list-style: none; margin: 0; padding: 0; }}
    .frame-list li {{ display: flex; gap: 8px; align-items: flex-start; margin: 5px 0; }}
    .frame-rationale {{ font-size: 12px; color: #475569; line-height: 1.5; }}
    .propositions {{ margin-top: 12px; }}
    .proposition {{ margin: 8px 0; padding: 9px 11px; border-left: 3px solid #60a5fa;
                    background: #eff6ff; border-radius: 4px; }}
    .proposition-head {{ font-size: 13px; font-weight: 650; color: #1e3a8a; }}
    .proposition-meta {{ font-size: 11px; color: #475569; margin-top: 2px; }}
    .modifier-list {{ margin: 5px 0 0; padding-left: 18px; font-size: 12px; color: #334155; }}
    .event-modifiers {{ margin-top: 8px; padding: 8px 10px; background: #f3e8ff;
                        border-radius: 4px; font-size: 12px; }}
    .validation {{ margin-top: 10px; padding: 9px 11px; border-radius: 5px; font-size: 12px; }}
    .validation-ready {{ background: #ecfdf5; border: 1px solid #86efac; }}
    .validation-error {{ background: #fef2f2; border: 1px solid #fca5a5; }}
    .validation-issues {{ margin: 6px 0 0; padding-left: 18px; }}
    .validation-issues li {{ margin: 3px 0; }}
    .severity-error {{ color: #b91c1c; }}
    .severity-warning {{ color: #a16207; }}
    .severity-info {{ color: #1d4ed8; }}
    .highlight-box {{ white-space: pre-wrap; word-break: break-word;
                      font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px;
                      background: #ffffff; padding: 14px; border: 1px solid #cbd5e1;
                      border-radius: 4px; }}
    .highlight-box span {{ padding: 1px 0; border-radius: 2px; }}
    pre {{ margin: 0; white-space: pre-wrap; word-break: break-word;
           font-family: ui-monospace, Menlo, Consolas, monospace; background: #f1f5f9;
           padding: 10px; border-radius: 4px; font-size: 13px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 10px; }}
    th, td {{ border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #e2e8f0; }}
    code {{ background: rgba(15,23,42,0.06); padding: 0 4px; border-radius: 3px; font-size: 12px; }}
    details {{ margin: 10px 0; border: 1px solid #cbd5e1; border-radius: 5px; background: #ffffff; }}
    details > summary {{ cursor: pointer; padding: 10px 14px; font-weight: 600; font-size: 14px;
                         color: #334155; list-style: none; }}
    details > summary::-webkit-details-marker {{ display: none; }}
    details > summary::before {{ content: "\\25B6"; display: inline-block; margin-right: 8px;
                                 font-size: 11px; color: #64748b; transition: transform 0.15s; }}
    details[open] > summary::before {{ transform: rotate(90deg); }}
    details .body {{ padding: 0 14px 12px; }}
    .graph-view {{ border: 1px solid #dbe3ef; border-radius: 14px; overflow: hidden; margin: 16px 0;
                   background: #fff; box-shadow: 0 10px 28px rgba(15,23,42,0.08); }}
    .graph-toolbar {{ display: flex; align-items: center; gap: 8px; min-height: 42px; padding: 9px 14px;
                      background: linear-gradient(135deg, #f8fafc, #eef4ff);
                      border-bottom: 1px solid #dbe3ef; }}
    .graph-toolbar strong {{ color: #172554; font-size: 13px; letter-spacing: 0.01em; }}
    .graph-toolbar .graph-counts {{ margin-right: auto; color: #64748b; font-size: 11px; }}
    .graph-toolbar button {{ padding: 5px 11px; border: 1px solid #cbd5e1; border-radius: 999px;
                             background: rgba(255,255,255,0.9); color: #334155; cursor: pointer;
                             font-size: 11px; transition: all 0.15s ease; }}
    .graph-toolbar button:hover {{ background: #fff; border-color: #818cf8; color: #3730a3;
                                   box-shadow: 0 2px 8px rgba(79,70,229,0.14); }}
    .graph-reader {{ display: grid; grid-template-columns: minmax(0, 2.35fr) minmax(300px, 1fr); }}
    .cy-graph {{ height: 680px; border-right: 1px solid #dbe3ef;
                 background-color: #f8fafc;
                 background-image: radial-gradient(#cbd5e1 0.7px, transparent 0.7px);
                 background-size: 18px 18px; }}
    .cy-graph.is-empty {{ display: flex; align-items: center; justify-content: center;
                          color: #94a3b8; font-size: 14px; padding: 24px; }}
    .graph-detail {{ padding: 18px; background: linear-gradient(180deg, #fff, #f8fafc);
                     min-height: 160px; font-size: 12px; }}
    .graph-detail h5 {{ margin: 0 0 12px; font-size: 15px; line-height: 1.4; color: #0f172a; }}
    .graph-detail-row {{ margin: 7px 0; color: #475569; }}
    .graph-detail-row strong {{ color: #1e293b; }}
    .graph-evidence {{ margin-top: 12px; padding-top: 10px; border-top: 1px solid #e2e8f0; }}
    .evidence-block {{ margin: 7px 0; padding: 8px; border-radius: 4px; background: #f8fafc;
                       border: 1px solid #e2e8f0; white-space: pre-wrap; }}
    .evidence-block.is-referenced {{ border-color: #f59e0b; background: #fffbeb; }}
    mark {{ background: #fde68a; padding: 1px 2px; border-radius: 2px; }}
    .graph-blocked {{ padding: 14px; margin: 12px 0; border: 1px solid #fca5a5;
                      background: #fef2f2; border-radius: 6px; color: #991b1b; }}
    .graph-legend {{ display: flex; flex-wrap: wrap; gap: 8px 14px; padding: 9px 14px;
                     background: #fff; border-top: 1px solid #e2e8f0; font-size: 11px; color: #64748b; }}
    .graph-legend span {{ display: inline-flex; align-items: center; }}
    .graph-legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 999px;
                       margin-right: 5px; box-shadow: inset 0 0 0 1px rgba(15,23,42,0.12); }}
    @media (max-width: 900px) {{
      body {{ margin: 18px; }}
      .pipeline {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .graph-reader {{ grid-template-columns: 1fr; }}
      .cy-graph {{ height: 580px; border-right: 0; border-bottom: 1px solid #e2e8f0; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(result.case_id)} · Semantic Graphing Report</h1>
    <div class="meta">
      Source: {escape(source_filename)}<br>
      <span class="stat-badge">原文 {len(raw_text)} 字符</span>
      <span class="stat-badge">segment {len(segments)}</span>
      <span class="stat-badge">graph unit {total_units}</span>
      <span class="stat-badge">contained source type {len(detected_contained)}</span>
      <span class="stat-badge">primary frame {sum(primary_frame_counts.values())}</span>
      <span class="stat-badge">proposition {proposition_count}</span>
      <span class="stat-badge">modifier {modifier_count}</span>
      {_render_validation_summary_badges(proposition_validation)}
      {_render_local_graph_summary_badges(local_graphs)}
      <span class="stat-badge">总耗时 {escape(elapsed_text)}</span>
    </div>

    <h2>📋 原文分段视图</h2>
    <p class="note">原文按临床叙述逻辑切分为多个discourse segment，每种颜色代表一个segment</p>
    <div class="highlight-box">{highlighted_text}</div>

    <h2>🔍 Segment 详细分析</h2>
    <p class="note">每个segment包含：graph units、单一 primary frame，以及有原文证据和明确修饰归属的 clinical propositions</p>
    {_render_unified_segments(
        graph_units,
        segment_by_id,
        primary_frame_by_unit,
        propositions_by_unit,
        validation_by_unit,
        local_graph_by_unit,
    )}

    <h2>📊 统计信息</h2>
    {_render_statistics_summary(primary_frame_counts, source_counts, specialty_counts)}
  </main>
  {_render_cytoscape_script()}
</body>
</html>
"""


def _render_unified_segments(
    graph_units: DocumentGraphUnits,
    segment_by_id: dict,
    primary_frame_by_unit: dict[str, Any] | None = None,
    propositions_by_unit: dict[str, Any] | None = None,
    validation_by_unit: dict[str, Any] | None = None,
    local_graph_by_unit: dict[str, Any] | None = None,
) -> str:
    """Render each segment once with its graph units and primary-frame selections."""
    primary_frame_by_unit = primary_frame_by_unit or {}
    propositions_by_unit = propositions_by_unit or {}
    validation_by_unit = validation_by_unit or {}
    local_graph_by_unit = local_graph_by_unit or {}
    cards = []
    
    for index, segment_units in enumerate(graph_units.segments):
        parent = segment_by_id.get(segment_units.segment_id)
        if parent is None:
            continue
            
        color = _segment_color(index)
        
        # Segment header - 简化信息，只显示关键字段
        contained_labels = [SOURCE_TYPE_ZH.get(str(item), str(item)) for item in parent.contained_source_types]
        contained_zh = ", ".join(contained_labels)
        
        segment_header = (
            f"<div class='segment-header'>"
            f"<h3>{escape(parent.segment_id)} · {escape(str(parent.unit_type))}</h3>"
            f"<div class='segment-info'>"
            f"📦 包含信息类型: <strong>{escape(contained_zh)}</strong> · "
            f"⏱️ 时间: {escape(parent.temporal_anchor or '无')} · "
            f"📏 长度: {len(parent.text)} 字符"
            f"</div>"
            "</div>"
        )
        
        # Segment text
        segment_text = f"<div class='segment-text'><pre>{escape(parent.text)}</pre></div>"
        
        # Graph Units
        unit_blocks = []
        if segment_units.graph_units:
            for unit in segment_units.graph_units:
                unit_source = str(unit.source_type)
                unit_color = SOURCE_TYPE_COLORS.get(unit_source, "#e5e7eb")
                
                # Source type badge
                source_badge = (
                    f"<span class='badge' style='background:{escape(unit_color)}'>"
                    f"{escape(SOURCE_TYPE_ZH.get(unit_source, unit_source))}</span>"
                )
                
                # MDT specialty badges
                specialty_badges = "".join(
                    f"<span class='badge-spec' style='border-color:{escape(MDT_SPECIALTY_COLORS.get(str(s), '#94a3b8'))}'>"
                    f"{escape(MDT_SPECIALTY_ZH.get(str(s), str(s)))}</span>"
                    for s in unit.mdt_specialty
                )
                
                # Status & certainty
                status_badge = f"<span class='badge' style='background:#dbeafe'>状态: {escape(unit.status)}</span>"
                certainty_badge = f"<span class='badge' style='background:#fef3c7'>确定性: {escape(unit.certainty)}</span>"
                
                selection = primary_frame_by_unit.get(unit.graph_unit_id)
                primary_frame_block = ""
                if selection is not None:
                    frame = str(selection.primary_frame)
                    warning = (
                        f"<div class='frame-rationale'><strong>边界复核提示：</strong>"
                        f"{escape(selection.boundary_warning)}</div>"
                        if selection.boundary_warning
                        else ""
                    )
                    primary_frame_block = (
                        "<div class='frame-row'><span class='frame-label'>Primary frame:</span>"
                        f"<span class='badge badge-frame' style='background:{escape(PRIMARY_FRAME_COLORS.get(frame, '#e5e7eb'))}'>"
                        f"{escape(PRIMARY_FRAME_ZH.get(frame, frame))}</span></div>"
                        f"<div class='frame-rationale'>{escape(selection.rationale)}</div>"
                        f"{warning}"
                    )

                proposition_block = _render_clinical_propositions(
                    propositions_by_unit.get(unit.graph_unit_id)
                )
                validation_block = _render_proposition_validation(
                    validation_by_unit.get(unit.graph_unit_id)
                )
                local_graph_block = _render_local_graph(
                    local_graph_by_unit.get(unit.graph_unit_id)
                )
                
                unit_blocks.append(
                    "<div class='unit'>"
                    f"<div class='unit-id'>{escape(unit.graph_unit_id)}</div>"
                    f"<div class='unit-meta'>{source_badge}{specialty_badges}{status_badge}{certainty_badge}</div>"
                    f"{primary_frame_block}"
                    f"<pre style='background:#f8fafc;padding:8px;border-radius:4px;font-size:13px;'>{escape(unit.text)}</pre>"
                    f"{proposition_block}"
                    f"{validation_block}"
                    f"{local_graph_block}"
                    "</div>"
                )
        else:
            unit_blocks.append("<p class='note' style='padding:16px 20px;'>该segment未提取出graph unit</p>")
        
        cards.append(
            f"<div class='segment-card' style='border-left-color:{escape(color)}'>"
            f"{segment_header}"
            f"{segment_text}"
            f"<div class='units-container'>"
            f"<h4 style='margin:0 0 12px;font-size:14px;color:#64748b;font-weight:600;'>Graph Units ({len(segment_units.graph_units)})</h4>"
            f"{''.join(unit_blocks)}"
            "</div>"
            "</div>"
        )
    
    return "".join(cards)


def _render_clinical_propositions(unit_propositions: Any | None) -> str:
    if unit_propositions is None:
        return ""

    evidence_blocks = "".join(
        "<li>"
        f"<code>{escape(block.evidence_id)}</code>: {escape(block.text)}"
        "</li>"
        for block in unit_propositions.evidence_blocks
    )
    evidence_section = (
        "<div class='evidence-blocks'><strong>Evidence blocks</strong>"
        f"<ul class='modifier-list'>{evidence_blocks}</ul></div>"
    )

    def render_evidence(evidence: Any) -> str:
        evidence_ids = ", ".join(escape(item) for item in evidence.evidence_ids)
        return f"<code>{evidence_ids}</code> · quote: {escape(evidence.quote)}"

    event_modifiers = ""
    if unit_propositions.event_modifiers:
        items = "".join(
            f"<li><strong>{escape(str(modifier.modifier_type))}</strong>: "
            f"{escape(modifier.value_text)} · {render_evidence(modifier.evidence)}</li>"
            for modifier in unit_propositions.event_modifiers
        )
        event_modifiers = (
            "<div class='event-modifiers'><strong>Event modifiers</strong>"
            f"<ul class='modifier-list'>{items}</ul></div>"
        )

    proposition_items = []
    for proposition in unit_propositions.propositions:
        modifiers = ""
        if proposition.modifiers:
            modifier_items = "".join(
                f"<li><strong>{escape(str(modifier.modifier_type))}</strong>: "
                f"{escape(modifier.value_text)} · {render_evidence(modifier.evidence)}</li>"
                for modifier in proposition.modifiers
            )
            modifiers = f"<ul class='modifier-list'>{modifier_items}</ul>"
        attribution = (
            f" · attribution: {escape(proposition.attribution.actor_text)}"
            if proposition.attribution is not None
            else ""
        )
        proposition_items.append(
            "<div class='proposition'>"
            f"<div class='proposition-head'>{escape(proposition.concept_text)}</div>"
            f"<div class='proposition-meta'>{escape(str(proposition.proposition_type))} · "
            f"status: {escape(proposition.status)} · certainty: {escape(proposition.certainty)}"
            f"{attribution} · {render_evidence(proposition.evidence)}</div>"
            f"{modifiers}</div>"
        )

    return (
        "<div class='propositions'><strong>Clinical propositions</strong>"
        f"{evidence_section}{event_modifiers}{''.join(proposition_items)}</div>"
    )


def _render_validation_summary_badges(
    validation: DocumentPropositionValidation | None,
) -> str:
    if validation is None:
        return ""
    summary = validation.summary
    return (
        f'<span class="stat-badge">graph-ready '
        f"{summary.graph_ready_unit_count}/{summary.unit_count}</span>"
        f'<span class="stat-badge">validation errors {summary.error_count}</span>'
        f'<span class="stat-badge">validation warnings {summary.warning_count}</span>'
    )


def _render_local_graph_summary_badges(local_graphs: DocumentLocalGraphs | None) -> str:
    if local_graphs is None:
        return ""
    summary = local_graphs.summary
    return (
        f'<span class="stat-badge">local graphs {summary.built_graph_count} built</span>'
        f'<span class="stat-badge">local graphs {summary.blocked_graph_count} blocked</span>'
        f'<span class="stat-badge">graph nodes {summary.node_count}</span>'
        f'<span class="stat-badge">graph edges {summary.edge_count}</span>'
    )


def _render_local_graph(local_graph: Any | None) -> str:
    if local_graph is None:
        return ""
    if str(local_graph.build_status) != "built":
        issues = "".join(
            f"<li><strong>{escape(issue.code)}</strong>: {escape(issue.message)}</li>"
            for issue in local_graph.build_issues
        )
        return (
            "<div class='graph-blocked'><strong>Local graph not rendered for this unit.</strong>"
            f"<ul class='validation-issues'>{issues}</ul></div>"
        )

    safe_id = local_graph.graph_unit_id.replace("_", "-")
    graph_data = json.dumps(local_graph.model_dump(mode="json"), ensure_ascii=False).replace(
        "</", "<\\/"
    )
    legend = (
        "<span><i style='background:#334155'></i>Graph unit</span>"
        "<span><i style='background:#f59e0b'></i>Event</span>"
        "<span><i style='background:#60a5fa'></i>Proposition</span>"
        "<span><i style='background:#c084fc'></i>Modifier</span>"
        "<span><i style='background:#34d399'></i>Source actor</span>"
        "<span>虚线边框 = absent / not performed</span>"
    )
    return (
        "<div class='graph-view'>"
        "<div class='graph-toolbar'><strong>Local evidence graph</strong>"
        f"<span class='graph-counts'>{len(local_graph.nodes)} nodes · {len(local_graph.edges)} relations</span>"
        f"<button type='button' data-graph-action='fit' data-graph-target='{escape(safe_id)}'>"
        "适应画布</button>"
        f"<button type='button' data-graph-action='reset' data-graph-target='{escape(safe_id)}'>"
        "重置选择</button></div>"
        "<div class='graph-reader'>"
        f"<div class='cy-graph' id='cy-{escape(safe_id)}'></div>"
        f"<aside class='graph-detail' id='detail-{escape(safe_id)}'>"
        "<h5>节点详情</h5><p>点击节点查看语义、状态和原文证据。</p></aside>"
        "</div>"
        f"<div class='graph-legend'>{legend}</div>"
        f"<script type='application/json' id='graph-data-{escape(safe_id)}'>{graph_data}</script>"
        "</div>"
    )


def _render_cytoscape_script() -> str:
    return """<script>
(() => {
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

  const highlightQuote = (text, quote) => {
    const safeText = escapeHtml(text);
    if (!quote) return safeText;
    const index = text.indexOf(quote);
    if (index < 0) return safeText;
    return escapeHtml(text.slice(0, index)) + "<mark>" + escapeHtml(quote) + "</mark>" +
      escapeHtml(text.slice(index + quote.length));
  };

  const renderDetail = (detail, node, graph) => {
    const data = node.data();
    const evidence = data.evidence || { evidence_ids: [], quote: "" };
    const referenced = new Set(evidence.evidence_ids || []);
    const blocks = graph.evidence_blocks.map((block) =>
      `<div class="evidence-block ${referenced.has(block.evidence_id) ? "is-referenced" : ""}">` +
      `<code>${escapeHtml(block.evidence_id)}</code><br>` +
      highlightQuote(block.text, referenced.has(block.evidence_id) ? evidence.quote : "") +
      `</div>`).join("");
    const detailLabel = data.node_type === "event"
      ? (frameLabels[data.label] || data.label)
      : data.label;
    detail.innerHTML =
      `<h5>${escapeHtml(detailLabel)}</h5>` +
      `<div class="graph-detail-row"><strong>节点类型：</strong>${escapeHtml(data.node_type)}</div>` +
      `<div class="graph-detail-row"><strong>语义类型：</strong>${escapeHtml(data.semantic_type)}</div>` +
      (data.status ? `<div class="graph-detail-row"><strong>状态：</strong>${escapeHtml(data.status)}</div>` : "") +
      (data.certainty ? `<div class="graph-detail-row"><strong>确定性：</strong>${escapeHtml(data.certainty)}</div>` : "") +
      `<div class="graph-detail-row"><strong>证据原文：</strong>${escapeHtml(evidence.quote)}</div>` +
      `<div class="graph-evidence"><strong>证据块</strong>${blocks}</div>`;
  };

  const nodeLabels = {
    graph_unit: "GRAPH UNIT",
    event: "EVENT",
    proposition: "PROPOSITION",
    modifier: "MODIFIER",
    source_actor: "SOURCE"
  };
  const edgeLabels = {
    organizes_as: "组织为",
    contains_proposition: "包含陈述",
    has_event_modifier: "事件修饰",
    has_modifier: "修饰",
    attributed_to: "来源"
  };
  const frameLabels = {
    symptom_episode: "症状病程事件",
    encounter: "诊疗接触事件",
    standalone_examination: "独立检查事件",
    clinical_assessment: "独立临床判断事件",
    treatment_course: "独立治疗过程",
    background_context: "背景上下文"
  };
  const layoutOptions = (graph, animate = false) => ({
    name: "breadthfirst",
    directed: true,
    roots: [graph.root_node_id],
    spacingFactor: graph.nodes.length > 16 ? 1.08 : 1.22,
    padding: 36,
    animate
  });
  const fitGraph = (cy) => {
    cy.fit(undefined, 36);
    if (cy.zoom() < 0.58) {
      cy.zoom(0.58);
      cy.center(cy.nodes('[node_type = "event"]'));
    }
  };

  const style = [
    { selector: "node", style: {
      "label": "data(display_label)", "text-wrap": "wrap", "text-max-width": 150,
      "font-family": "Inter, ui-sans-serif, system-ui, sans-serif", "font-size": 11,
      "font-weight": 600, "line-height": 1.25, "color": "#1e3a5f",
      "background-color": "#eff6ff", "border-width": 1.5, "border-color": "#93c5fd",
      "shape": "round-rectangle", "width": 172, "height": 58,
      "text-valign": "center", "text-halign": "center", "overlay-opacity": 0,
      "shadow-blur": 10, "shadow-color": "#64748b", "shadow-opacity": 0.15,
      "shadow-offset-x": 0, "shadow-offset-y": 3
    }},
    { selector: 'node[node_type = "graph_unit"]', style: {
      "background-color": "#172554", "border-color": "#172554", "color": "#fff",
      "width": 190, "height": 64, "font-size": 12,
      "shadow-color": "#172554", "shadow-opacity": 0.28
    }},
    { selector: 'node[node_type = "event"]', style: {
      "background-color": "#fff7ed", "border-color": "#fb923c", "color": "#9a3412",
      "width": 190, "height": 64, "font-size": 12, "border-width": 2.5,
      "shadow-color": "#f97316", "shadow-opacity": 0.19
    }},
    { selector: 'node[node_type = "modifier"]', style: {
      "background-color": "#faf5ff", "border-color": "#d8b4fe", "color": "#6b21a8",
      "width": 142, "height": 46, "font-size": 10, "border-style": "dashed",
      "shadow-opacity": 0.08
    }},
    { selector: 'node[node_type = "source_actor"]', style: {
      "background-color": "#ecfdf5", "border-color": "#6ee7b7", "color": "#065f46",
      "width": 142, "height": 46, "font-size": 10, "shadow-opacity": 0.08
    }},
    { selector: 'node[status = "absent"], node[status = "not_performed"]', style: {
      "border-style": "dashed", "border-width": 2.5, "background-color": "#f8fafc",
      "border-color": "#94a3b8", "color": "#64748b"
    }},
    { selector: 'node[status = "possible"], node[status = "planned"]', style: {
      "border-style": "double", "border-width": 3
    }},
    { selector: "edge", style: {
      "curve-style": "round-taxi", "taxi-direction": "downward", "taxi-radius": 10,
      "target-arrow-shape": "triangle", "arrow-scale": 0.7,
      "line-color": "#cbd5e1", "target-arrow-color": "#94a3b8", "width": 1.4,
      "label": "data(display_label)", "font-size": 8, "font-weight": 500, "color": "#64748b",
      "text-background-color": "#f8fafc", "text-background-opacity": 1,
      "text-background-padding": 3, "text-background-shape": "roundrectangle",
      "overlay-opacity": 0
    }},
    { selector: 'edge[edge_type = "organizes_as"]', style: {
      "line-color": "#64748b", "target-arrow-color": "#475569", "width": 2
    }},
    { selector: 'edge[edge_type = "has_modifier"], edge[edge_type = "has_event_modifier"]', style: {
      "line-color": "#d8b4fe", "target-arrow-color": "#c084fc", "line-style": "dashed"
    }},
    { selector: 'edge[edge_type = "attributed_to"]', style: {
      "line-color": "#6ee7b7", "target-arrow-color": "#34d399", "line-style": "dashed"
    }},
    { selector: ".faded", style: { "opacity": 0.1, "text-opacity": 0.1 }},
    { selector: ".focused", style: {
      "border-width": 3, "border-color": "#4f46e5", "line-color": "#6366f1",
      "target-arrow-color": "#4f46e5", "shadow-color": "#4f46e5",
      "shadow-opacity": 0.3, "z-index": 999
    }}
  ];

  document.querySelectorAll('script[id^="graph-data-"]').forEach((dataElement) => {
    const suffix = dataElement.id.replace("graph-data-", "");
    const container = document.getElementById(`cy-${suffix}`);
    const detail = document.getElementById(`detail-${suffix}`);
    const graph = JSON.parse(dataElement.textContent);
    if (!window.cytoscape) {
      container.classList.add("is-empty");
      container.textContent = "Cytoscape 未能加载，图数据仍保存在 local_graphs.json。";
      return;
    }
    const elements = [
      ...graph.nodes.map((node) => ({ data: {
        ...node, id: node.node_id,
        display_label: `${nodeLabels[node.node_type] || node.node_type}\\n${
          node.node_type === "event" ? (frameLabels[node.label] || node.label) : node.label
        }`,
      }})),
      ...graph.edges.map((edge) => ({ data: {
        ...edge, id: edge.edge_id, source: edge.source_node_id, target: edge.target_node_id,
        display_label: edgeLabels[edge.edge_type] || edge.edge_type,
      }})),
    ];
    const cy = cytoscape({
      container, elements, style,
      layout: layoutOptions(graph),
      minZoom: 0.2, maxZoom: 2.5, userZoomingEnabled: false
    });
    container._cy = cy;
    cy.on("tap", "node", (event) => {
      cy.elements().addClass("faded").removeClass("focused");
      const neighborhood = event.target.closedNeighborhood();
      neighborhood.removeClass("faded").addClass("focused");
      renderDetail(detail, event.target, graph);
    });
    cy.on("tap", (event) => {
      if (event.target === cy) cy.elements().removeClass("faded focused");
    });
    fitGraph(cy);
  });

  document.querySelectorAll("[data-graph-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const container = document.getElementById(`cy-${button.dataset.graphTarget}`);
      const cy = container && container._cy;
      if (!cy) return;
      cy.elements().removeClass("faded focused");
      if (button.dataset.graphAction === "fit") fitGraph(cy);
      if (button.dataset.graphAction === "reset") cy.layout(layoutOptions(
        JSON.parse(document.getElementById(`graph-data-${button.dataset.graphTarget}`).textContent),
        true
      )).run();
    });
  });
})();
</script>"""


def _render_proposition_validation(unit_validation: Any | None) -> str:
    if unit_validation is None:
        return ""

    state_class = "validation-ready" if unit_validation.is_graph_ready else "validation-error"
    state_label = "Graph ready" if unit_validation.is_graph_ready else "Has validation errors"
    metrics = unit_validation.metrics
    issues = ""
    if unit_validation.issues:
        issue_items = "".join(
            f"<li class='severity-{escape(str(issue.severity))}'>"
            f"<strong>{escape(str(issue.severity))} · {escape(issue.code)}</strong>: "
            f"{escape(issue.message)}"
            f"{' · proposition ' + escape(issue.proposition_id) if issue.proposition_id else ''}"
            f"{' · modifier ' + escape(issue.modifier_id) if issue.modifier_id else ''}"
            "</li>"
            for issue in unit_validation.issues
        )
        issues = f"<ul class='validation-issues'>{issue_items}</ul>"

    return (
        f"<div class='validation {state_class}'><strong>{state_label}</strong>"
        f" · propositions {metrics.proposition_count}"
        f" · event modifiers {metrics.event_modifier_count}"
        f" · proposition modifiers {metrics.proposition_modifier_count}"
        f" · attributed propositions {metrics.attributed_proposition_count}"
        f" · evidence blocks {metrics.referenced_evidence_block_count}/{metrics.evidence_block_count}"
        f" ({metrics.evidence_block_coverage:.1%})"
        f"{issues}</div>"
    )


def _render_statistics_summary(
    primary_frame_counts: Counter,
    source_counts: Counter,
    specialty_counts: Counter,
) -> str:
    """简化的统计信息展示"""
    
    # Primary frame statistics
    frame_rows = []
    for frame, count in primary_frame_counts.most_common():
        frame_label = PRIMARY_FRAME_ZH.get(frame, frame)
        frame_rows.append(
            f"<tr><td><span class='badge badge-frame' style='background:{escape(PRIMARY_FRAME_COLORS.get(frame, '#e5e7eb'))}'>"
            f"{escape(frame_label)}</span></td><td>{count}</td></tr>"
        )
    frame_rows_html = "".join(frame_rows) or "<tr><td colspan='2'>无数据</td></tr>"
    frame_table = (
        "<table><thead><tr><th>Primary frame 类型</th><th>Unit 数量</th></tr></thead>"
        f"<tbody>{frame_rows_html}</tbody></table>"
    )
    
    # Source type统计
    source_rows = []
    for source, count in source_counts.most_common():
        source_label = SOURCE_TYPE_ZH.get(source, source)
        source_rows.append(f"<tr><td>{escape(source_label)}</td><td>{count}</td></tr>")
    source_table = (
        "<table><thead><tr><th>信息来源类型</th><th>Graph Unit数量</th></tr></thead>"
        f"<tbody>{''.join(source_rows)}</tbody></table>"
    )
    
    # MDT specialty统计
    specialty_rows = []
    for specialty, count in specialty_counts.most_common():
        specialty_label = MDT_SPECIALTY_ZH.get(specialty, specialty)
        specialty_rows.append(f"<tr><td>{escape(specialty_label)}</td><td>{count}</td></tr>")
    specialty_table = (
        "<table><thead><tr><th>MDT专科</th><th>相关Unit数量</th></tr></thead>"
        f"<tbody>{''.join(specialty_rows)}</tbody></table>"
    )
    
    return (
        f"<details open><summary>Primary frame 统计</summary><div class='body'>{frame_table}</div></details>"
        f"<details><summary>信息来源类型分布</summary><div class='body'>{source_table}</div></details>"
        f"<details><summary>MDT专科分布</summary><div class='body'>{specialty_table}</div></details>"
    )




def _render_highlighted_raw_text(raw_text: str, segments: list) -> str:
    cursor = 0
    parts: list[str] = []

    for index, seg in enumerate(sorted(segments, key=lambda item: item.start_char)):
        start = max(0, min(seg.start_char, len(raw_text)))
        end = max(start, min(seg.end_char, len(raw_text)))
        if start < cursor:
            continue
        if start > cursor:
            parts.append(escape(raw_text[cursor:start]))

        color = _segment_color(index)
        label = f"{seg.segment_id} · {seg.unit_type}"
        parts.append(
            f"<span title='{escape(label)}' style='background:{escape(color)};'>"
            f"{escape(raw_text[start:end])}</span>"
        )
        cursor = end

    if cursor < len(raw_text):
        parts.append(escape(raw_text[cursor:]))
    return "".join(parts)
