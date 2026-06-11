from collections import Counter
from html import escape
from pathlib import Path
from typing import Any

from src.agents.semantic_graphing.agent import ClassificationRunResult
from src.schemas.semantic_graphing import (
    DocumentClinicalPropositions,
    DocumentGraphUnits,
    DocumentPrimaryFrames,
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

    total_elapsed = timing.get("total_elapsed_seconds")
    elapsed_text = f"{total_elapsed:.2f}s" if isinstance(total_elapsed, int | float) else "N/A"
    highlighted_text = _render_highlighted_raw_text(raw_text, segments)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{escape(result.case_id)} 语义图谱报告</title>
  <style>
    body {{ margin: 32px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            line-height: 1.7; color: #1f2937; background: #f8fafc; }}
    main {{ max-width: 1200px; margin: 0 auto; }}
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
    .graph-view {{ border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden; margin: 14px 0; }}
    .graph-toolbar {{ display: flex; align-items: center; gap: 8px; padding: 8px 12px;
                      background: #f8fafc; border-bottom: 1px solid #e2e8f0; }}
    .graph-toolbar button {{ padding: 4px 12px; border: 1px solid #94a3b8; border-radius: 4px;
                             background: #fff; cursor: pointer; font-size: 12px; }}
    .graph-toolbar button:hover {{ background: #e2e8f0; }}
    .cy-graph {{ height: 480px; background: #fafafa; }}
    .cy-graph.is-empty {{ display: flex; align-items: center; justify-content: center;
                          color: #94a3b8; font-size: 14px; padding: 24px; }}
    .graph-legend {{ display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 12px;
                     background: #f8fafc; border-top: 1px solid #e2e8f0; font-size: 11px; color: #475569; }}
    .graph-legend i {{ display: inline-block; width: 12px; height: 12px; border-radius: 3px;
                       vertical-align: middle; margin-right: 3px; }}
    @media (max-width: 900px) {{
      body {{ margin: 18px; }}
      .pipeline {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
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
    )}

    <h2>📊 统计信息</h2>
    {_render_statistics_summary(primary_frame_counts, source_counts, specialty_counts)}
  </main>
</body>
</html>
"""


def _render_unified_segments(
    graph_units: DocumentGraphUnits,
    segment_by_id: dict,
    primary_frame_by_unit: dict[str, Any] | None = None,
    propositions_by_unit: dict[str, Any] | None = None,
    validation_by_unit: dict[str, Any] | None = None,
) -> str:
    """Render each segment once with its graph units and primary-frame selections."""
    primary_frame_by_unit = primary_frame_by_unit or {}
    propositions_by_unit = propositions_by_unit or {}
    validation_by_unit = validation_by_unit or {}
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
                
                unit_blocks.append(
                    "<div class='unit'>"
                    f"<div class='unit-id'>{escape(unit.graph_unit_id)}</div>"
                    f"<div class='unit-meta'>{source_badge}{specialty_badges}{status_badge}{certainty_badge}</div>"
                    f"{primary_frame_block}"
                    f"<pre style='background:#f8fafc;padding:8px;border-radius:4px;font-size:13px;'>{escape(unit.text)}</pre>"
                    f"{proposition_block}"
                    f"{validation_block}"
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

    event_modifiers = ""
    if unit_propositions.event_modifiers:
        items = "".join(
            f"<li><strong>{escape(str(modifier.modifier_type))}</strong>: "
            f"{escape(modifier.value_text)} "
            f"<code>[{modifier.source_span.start_char}:{modifier.source_span.end_char}]</code></li>"
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
                f"{escape(modifier.value_text)} "
                f"<code>[{modifier.source_span.start_char}:{modifier.source_span.end_char}]</code></li>"
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
            f"{attribution} · "
            f"span: [{proposition.source_span.start_char}:{proposition.source_span.end_char}]</div>"
            f"{modifiers}</div>"
        )

    return (
        "<div class='propositions'><strong>Clinical propositions</strong>"
        f"{event_modifiers}{''.join(proposition_items)}</div>"
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
        f" · evidence coverage {metrics.evidence_coverage:.1%}"
        f" · propositions {metrics.proposition_count}"
        f" · event modifiers {metrics.event_modifier_count}"
        f" · proposition modifiers {metrics.proposition_modifier_count}"
        f" · attributed propositions {metrics.attributed_proposition_count}"
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
