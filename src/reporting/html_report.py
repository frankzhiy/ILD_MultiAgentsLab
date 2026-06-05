from collections import Counter
from html import escape
from pathlib import Path
from typing import Any

from src.agents.semantic_graphing.agent import ClassificationRunResult
from src.schemas.semantic_graphing import DocumentGraphUnits


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
) -> Path:
    """Render a single HTML report covering the full run pipeline."""
    html = _render_html(
        result=result,
        graph_units=graph_units,
        source_filename=source_filename,
        raw_text=raw_text,
        timing=timing,
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

    total_elapsed = timing.get("total_elapsed_seconds")
    elapsed_text = f"{total_elapsed:.2f}s" if isinstance(total_elapsed, int | float) else "N/A"
    highlighted_text = _render_highlighted_raw_text(raw_text, segments)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{escape(result.case_id)} semantic graphing report</title>
  <style>
    body {{ margin: 32px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            line-height: 1.7; color: #1f2937; background: #f8fafc; }}
    main {{ max-width: 1180px; margin: 0 auto; }}
    h1 {{ margin-bottom: 8px; font-size: 26px; }}
    h2 {{ margin-top: 40px; font-size: 21px; border-bottom: 2px solid #94a3b8; padding-bottom: 6px; }}
    h3 {{ margin: 18px 0 8px; font-size: 15px; color: #334155; }}
    .meta {{ margin-bottom: 24px; color: #475569; font-size: 14px; }}
    .note {{ color: #475569; font-size: 13px; margin: 8px 0 14px; }}
    .stat-badge {{ display: inline-block; background: #e2e8f0; border-radius: 3px;
                   padding: 1px 7px; font-size: 12px; margin-right: 4px; }}
    .pipeline {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 14px 0 18px; }}
    .stage-card {{ background: #ffffff; border: 1px solid #cbd5e1; border-radius: 5px; padding: 10px; min-height: 92px; }}
    .stage-card strong {{ display: block; font-size: 13px; margin-bottom: 4px; }}
    .stage-card .count {{ display: block; color: #0f172a; font-size: 18px; font-weight: 700; margin-bottom: 2px; }}
    .stage-card .desc {{ color: #64748b; font-size: 12px; line-height: 1.45; }}
    .segment-card {{ margin: 14px 0; padding: 14px 16px; border-left: 6px solid; border-radius: 4px;
                     background: #ffffff; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    .segment-card h3 {{ margin: 0 0 8px; font-size: 16px; }}
    .fields {{ font-size: 12px; color: #475569; margin-bottom: 8px; }}
    .unit {{ margin: 8px 0; padding: 10px 12px; border: 1px solid #cbd5e1; border-radius: 4px;
             background: #f8fafc; }}
    .unit-head {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 6px; }}
    .badge {{ display: inline-block; padding: 1px 7px; border-radius: 3px; font-size: 12px; }}
    .badge-spec {{ display: inline-flex; align-items: center; gap: 4px; padding: 1px 9px;
                   border-radius: 999px; font-size: 12px; font-weight: 600; background: #ffffff;
                   border: 1.5px solid #94a3b8; color: #1f2937; }}
    .badge-spec::before {{ content: "\\1F3E5"; font-size: 11px; }}
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
      <span class="stat-badge">总耗时 {escape(elapsed_text)}</span>
    </div>

    <h2>Pipeline Overview</h2>
    {_render_pipeline_overview(raw_text, segments, total_units, elapsed_text)}

    <h2>Step 2 · Clinical Discourse Segmentation</h2>
    <p class="note">将原文切分为完整的 clinical discourse unit / episode。颜色用于区分相邻 segment。</p>
    <h3>原文切分查看</h3>
    <div class="highlight-box">{highlighted_text}</div>
    <h3>Segment 列表</h3>
    {_render_segment_cards(segments)}

    <h2>Step 3 · Graph Unit Extraction</h2>
    <p class="note">每个 segment 内部切出 graph unit（一个临床事件核），不是最终 node/edge。</p>
    {_render_graph_unit_cards(graph_units, segment_by_id)}

    <h2>计数与字典</h2>
    {_render_counts_section(unit_type_counts, source_counts, specialty_counts)}
  </main>
</body>
</html>
"""


def _render_pipeline_overview(
    raw_text: str,
    segments: list,
    total_units: int,
    elapsed: str,
) -> str:
    cards = [
        ("Input", str(len(raw_text)), "原始病例文本字符数"),
        ("Step 2", str(len(segments)), "clinical discourse segmentation"),
        ("Step 3", str(total_units), "per-segment graph unit extraction"),
        ("Output", str(len(segments)), f"segment / {total_units} unit；耗时 {elapsed}"),
    ]
    return '<div class="pipeline">' + "".join(
        "<div class='stage-card'>"
        f"<strong>{escape(title)}</strong>"
        f"<span class='count'>{escape(count)}</span>"
        f"<span class='desc'>{escape(desc)}</span>"
        "</div>"
        for title, count, desc in cards
    ) + "</div>"


def _render_segment_cards(segments: list) -> str:
    cards = []
    for index, seg in enumerate(segments):
        color = _segment_color(index)
        contained = ", ".join(str(item) for item in seg.contained_source_types)
        cards.append(
            f"<div class='segment-card' style='border-left-color:{escape(color)}'>"
            f"<h3>{escape(seg.segment_id)} · {escape(str(seg.unit_type))}</h3>"
            f"<div class='fields'>contained_source_types=<code>{escape(contained)}</code> · "
            f"clinical_frame=<code>{escape(seg.clinical_frame)}</code> · "
            f"span={seg.start_char}-{seg.end_char} ({len(seg.text)} 字符) · "
            f"temporal_anchor={escape(seg.temporal_anchor or '')} · "
            f"confidence={seg.confidence:.2f} · rationale={escape(seg.rationale)}</div>"
            f"<pre>{escape(seg.text)}</pre>"
            "</div>"
        )
    return "".join(cards)


def _render_graph_unit_cards(graph_units: DocumentGraphUnits, segment_by_id: dict) -> str:
    cards = []
    for index, segment_units in enumerate(graph_units.segments):
        parent = segment_by_id.get(segment_units.segment_id)
        color = _segment_color(index)
        parent_fields = ""
        parent_text = ""
        if parent is not None:
            contained = ", ".join(str(item) for item in parent.contained_source_types)
            parent_fields = (
                f"unit_type=<code>{escape(str(parent.unit_type))}</code> · "
                f"contained=<code>{escape(contained)}</code> · "
                f"span={parent.start_char}-{parent.end_char}"
            )
            parent_text = f"<pre>{escape(parent.text)}</pre>"

        unit_blocks = []
        for unit in segment_units.graph_units:
            unit_source = str(unit.source_type)
            unit_color = SOURCE_TYPE_COLORS.get(unit_source, "#e5e7eb")
            span = ""
            if unit.start_char is not None and unit.end_char is not None:
                span = f" · span={unit.start_char}-{unit.end_char}"
            specialty_badges = "".join(
                f"<span class='badge-spec' style='border-color:{escape(MDT_SPECIALTY_COLORS.get(str(s), '#94a3b8'))}'>"
                f"{escape(MDT_SPECIALTY_ZH.get(str(s), str(s)))} / <code>{escape(str(s))}</code></span>"
                for s in unit.mdt_specialty
            )
            unit_blocks.append(
                "<div class='unit'>"
                "<div class='unit-head'>"
                f"<strong>{escape(unit.graph_unit_id)}</strong>"
                f"<span class='badge' style='background:{escape(unit_color)}'>"
                f"{escape(SOURCE_TYPE_ZH.get(unit_source, unit_source))} / "
                f"<code>{escape(unit_source)}</code></span>"
                f"{specialty_badges}"
                f"<span class='badge'>status=<code>{escape(unit.status)}</code></span>"
                f"<span class='badge'>certainty=<code>{escape(unit.certainty)}</code>{span}</span>"
                "</div>"
                f"<pre>{escape(unit.text)}</pre>"
                f"<div class='fields'>time={escape(unit.temporal_anchor or '')} · "
                f"context={escape(unit.clinical_context or '')} · "
                f"rationale={escape(unit.rationale)}</div>"
                "</div>"
            )
        if not unit_blocks:
            unit_blocks.append("<p class='note'>该 segment 未输出 graph unit。</p>")

        cards.append(
            f"<div class='segment-card' style='border-left-color:{escape(color)}'>"
            f"<h3>{escape(segment_units.segment_id)}</h3>"
            f"<div class='fields'>{parent_fields}</div>"
            f"{parent_text}"
            f"{''.join(unit_blocks)}"
            "</div>"
        )
    return "".join(cards)


def _render_counts_section(
    unit_type_counts: Counter,
    source_counts: Counter,
    specialty_counts: Counter,
) -> str:
    body = (
        _render_unit_dictionary(unit_type_counts)
        + _render_counter_table("Graph Unit · Source Type（叙事角色）", source_counts)
        + _render_counter_table("Graph Unit · MDT Specialty（会诊专科）", specialty_counts)
    )
    return (
        "<details>"
        "<summary>展开计数与字典</summary>"
        f"<div class='body'>{body}</div>"
        "</details>"
    )


def _render_unit_dictionary(counts: Counter) -> str:
    rows = []
    unit_types = [
        "demographics_chief_complaint",
        "past_medical_history",
        "current_medication",
        "clinical_episode",
        "general_condition",
        "standalone_imaging_report",
        "standalone_pulmonary_function_report",
        "standalone_lab_panel",
        "standalone_pathology_report",
        "standalone_treatment_plan",
        "standalone_clinician_assessment",
        "other",
    ]
    for unit_type in unit_types:
        count = counts.get(unit_type, 0)
        rows.append(
            "<tr>"
            f"<td><code>{escape(unit_type)}</code></td>"
            f"<td>{count}</td>"
            "</tr>"
        )
    return (
        "<h3>Segment · Discourse Unit Type</h3>"
        "<table><thead><tr><th>unit_type</th><th>当前数量</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _render_counter_table(title: str, counts: Counter) -> str:
    rows = []
    for key, count in counts.most_common():
        rows.append(
            "<tr>"
            f"<td><code>{escape(str(key))}</code></td>"
            f"<td>{count}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='2'>无</td></tr>")
    return (
        f"<h3>{escape(title)}</h3>"
        "<table><thead><tr><th>Type</th><th>Count</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
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
