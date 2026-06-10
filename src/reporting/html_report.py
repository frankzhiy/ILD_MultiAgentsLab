from collections import Counter
from html import escape
from pathlib import Path
from typing import Any

from src.agents.semantic_graphing.agent import ClassificationRunResult
from src.schemas.semantic_graphing import (
    DocumentFrameTriage,
    DocumentGraphUnits,
    FRAME_DEFINITION_BY_FRAME,
)


FRAME_ZH: dict[str, str] = {
    "symptom_episode": "症状起病/加重",
    "encounter": "就诊/住院接触",
    "examination_report": "检查报告面板",
    "diagnosis": "诊断判断",
    "treatment_course": "独立治疗/用药",
    "background_fact": "背景史",
}

FRAME_COLORS: dict[str, str] = {
    "symptom_episode": "#bfdbfe",
    "encounter": "#fed7aa",
    "examination_report": "#c7d2fe",
    "diagnosis": "#fdba74",
    "treatment_course": "#fde68a",
    "background_fact": "#e5e7eb",
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
    frame_triage: DocumentFrameTriage | None = None,
) -> Path:
    """Render a single HTML report covering the full run pipeline."""
    html = _render_html(
        result=result,
        graph_units=graph_units,
        source_filename=source_filename,
        raw_text=raw_text,
        timing=timing,
        frame_triage=frame_triage,
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
    frame_triage: DocumentFrameTriage | None = None,
) -> str:
    classification = result.classification
    segments = classification.segments
    segment_by_id = {segment.segment_id: segment for segment in segments}

    total_units = sum(len(item.graph_units) for item in graph_units.segments)
    unit_type_counts = Counter(str(seg.unit_type) for seg in segments)
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

    triage_by_unit: dict[str, list] = {}
    frame_counts: Counter = Counter()
    if frame_triage is not None:
        for seg in frame_triage.segments:
            for unit in seg.units:
                triage_by_unit[unit.graph_unit_id] = list(unit.triggered_frames)
                for tf in unit.triggered_frames:
                    frame_counts[str(tf.frame)] += 1

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
      <span class="stat-badge">触发 frame {sum(frame_counts.values())}</span>
      <span class="stat-badge">总耗时 {escape(elapsed_text)}</span>
    </div>

    <h2>📋 原文分段视图</h2>
    <p class="note">原文按临床叙述逻辑切分为多个discourse segment，每种颜色代表一个segment</p>
    <div class="highlight-box">{highlighted_text}</div>

    <h2>🔍 Segment 详细分析</h2>
    <p class="note">每个segment包含：段落分类、内部的graph units（临床事件核心）及其触发的临床框架</p>
    {_render_unified_segments(graph_units, segment_by_id, triage_by_unit)}

    <h2>📊 统计信息</h2>
    {_render_statistics_summary(frame_counts, source_counts, specialty_counts)}
  </main>
</body>
</html>
"""


def _render_unified_segments(
    graph_units: DocumentGraphUnits,
    segment_by_id: dict,
    triage_by_unit: dict[str, list] | None = None,
) -> str:
    """统一渲染：每个segment只显示一次，包含其graph units和triggered frames"""
    triage_by_unit = triage_by_unit or {}
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
                
                # Triggered frames
                triggered = triage_by_unit.get(unit.graph_unit_id, [])
                frame_badges = ""
                if triggered:
                    frame_badges = "<div class='frame-row'><span class='frame-label'>🎯 触发框架:</span>" + "".join(
                        f"<span class='badge badge-frame' style='background:{escape(FRAME_COLORS.get(str(tf.frame), '#e5e7eb'))}'>"
                        f"{escape(FRAME_ZH.get(str(tf.frame), str(tf.frame)))}</span>"
                        for tf in triggered
                    ) + "</div>"
                
                unit_blocks.append(
                    "<div class='unit'>"
                    f"<div class='unit-id'>{escape(unit.graph_unit_id)}</div>"
                    f"<div class='unit-meta'>{source_badge}{specialty_badges}{status_badge}{certainty_badge}</div>"
                    f"{frame_badges}"
                    f"<pre style='background:#f8fafc;padding:8px;border-radius:4px;font-size:13px;'>{escape(unit.text)}</pre>"
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


def _render_statistics_summary(
    frame_counts: Counter,
    source_counts: Counter,
    specialty_counts: Counter,
) -> str:
    """简化的统计信息展示"""
    
    # Frame统计
    frame_rows = []
    for frame, count in frame_counts.most_common():
        frame_label = FRAME_ZH.get(frame, frame)
        frame_rows.append(
            f"<tr><td><span class='badge badge-frame' style='background:{escape(FRAME_COLORS.get(frame, '#e5e7eb'))}'>"
            f"{escape(frame_label)}</span></td><td>{count}</td></tr>"
        )
    frame_table = (
        "<table><thead><tr><th>临床框架类型</th><th>触发次数</th></tr></thead>"
        f"<tbody>{''.join(frame_rows) if frame_rows else '<tr><td colspan=\"2\">无数据</td></tr>'}</tbody></table>"
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
        f"<details open><summary>临床框架触发统计</summary><div class='body'>{frame_table}</div></details>"
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
