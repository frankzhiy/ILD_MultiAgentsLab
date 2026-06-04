from collections import Counter
from html import escape
from pathlib import Path
from typing import Any

from src.agents.semantic_graphing.agent import ClassificationRunResult
from src.schemas.semantic_graphing import SourceType


SOURCE_TYPE_ZH: dict[str, str] = {
    "history": "病史文本",
    "hrct": "HRCT 报告",
    "pulmonary_function": "肺功能检查",
    "laboratory": "实验室检查",
    "pathology": "病理报告",
    "exposure_medication": "用药/暴露史",
    "clinician_assessment": "医生总结/转诊记录",
    "mixed": "混合片段",
    "unknown": "未知",
}

SOURCE_TYPE_COLORS: dict[str, str] = {
    "history": "#bfdbfe",
    "hrct": "#c7d2fe",
    "pulmonary_function": "#bae6fd",
    "laboratory": "#d9f99d",
    "pathology": "#f5d0fe",
    "exposure_medication": "#ddd6fe",
    "clinician_assessment": "#fdba74",
    "mixed": "#fed7aa",
    "unknown": "#cbd5e1",
}


def render_classification_report(
    result: ClassificationRunResult,
    *,
    source_filename: str,
    raw_text: str,
    timing: dict[str, Any],
    output_path: str | Path,
) -> Path:
    html = _render_html(
        result=result,
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
    source_filename: str,
    raw_text: str,
    timing: dict[str, Any],
) -> str:
    classification = result.classification
    counts = Counter(str(seg.source_type) for seg in classification.segments)
    detected = [str(item) for item in classification.detected_source_types]
    total_elapsed = timing.get("total_elapsed_seconds")
    elapsed_text = f"{total_elapsed:.2f}s" if isinstance(total_elapsed, int | float) else "N/A"
    highlighted_text, unmatched_segments = _render_highlighted_raw_text(
        raw_text,
        classification.segments,
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{escape(result.case_id)} classification</title>
  <style>
    body {{ margin: 32px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            line-height: 1.7; color: #1f2937; background: #f8fafc; }}
    main {{ max-width: 1180px; margin: 0 auto; }}
    h1 {{ margin-bottom: 8px; font-size: 26px; }}
    h2 {{ margin-top: 36px; font-size: 20px; border-bottom: 2px solid #94a3b8; padding-bottom: 6px; }}
    h3 {{ margin: 18px 0 8px; font-size: 15px; color: #334155; }}
    .meta {{ margin-bottom: 24px; color: #475569; font-size: 14px; }}
    .note {{ color: #475569; font-size: 13px; margin: 8px 0 14px; }}
    .stat-badge {{ display: inline-block; background: #e2e8f0; border-radius: 3px;
                   padding: 1px 7px; font-size: 12px; margin-right: 4px; }}
    .pipeline {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 14px 0 18px; }}
    .stage-card {{ background: #ffffff; border: 1px solid #cbd5e1; border-radius: 5px; padding: 10px; min-height: 88px; }}
    .stage-card strong {{ display: block; font-size: 13px; margin-bottom: 4px; }}
    .stage-card .count {{ display: block; color: #0f172a; font-size: 18px; font-weight: 700; margin-bottom: 2px; }}
    .stage-card .desc {{ color: #64748b; font-size: 12px; line-height: 1.45; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #e2e8f0; }}
    tr td:first-child {{ width: 70px; text-align: center; white-space: nowrap; }}
    .segment-card {{ margin: 12px 0; padding: 14px 16px; border-left: 6px solid; border-radius: 4px;
                     background: #ffffff; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    .segment-card h3 {{ margin: 0 0 8px; font-size: 16px; }}
    .segment-card .fields {{ font-size: 12px; color: #475569; margin-bottom: 8px; }}
    .highlight-box {{ white-space: pre-wrap; word-break: break-word;
                      font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px;
                      background: #ffffff; padding: 14px; border: 1px solid #cbd5e1;
                      border-radius: 4px; }}
    .highlight-box span {{ padding: 1px 0; border-radius: 2px; }}
    .unmatched {{ background: #fff7ed; border: 1px solid #fed7aa; border-radius: 5px;
                  padding: 10px 12px; margin-top: 10px; color: #9a3412; font-size: 13px; }}
    pre {{ margin: 0; white-space: pre-wrap; word-break: break-word;
           font-family: ui-monospace, Menlo, Consolas, monospace; background: #f1f5f9;
           padding: 10px; border-radius: 4px; font-size: 13px; }}
    code {{ background: rgba(15,23,42,0.06); padding: 0 4px; border-radius: 3px; font-size: 12px; }}
    @media (max-width: 900px) {{
      body {{ margin: 18px; }}
      .pipeline {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(result.case_id)} · Step 2 分类结果</h1>
    <div class="meta">
      Source: {escape(source_filename)}<br>
      <span class="stat-badge">原文 {len(raw_text)} 字符</span>
      <span class="stat-badge">segment {len(classification.segments)}</span>
      <span class="stat-badge">source type {len(detected)}</span>
      <span class="stat-badge">总耗时 {escape(elapsed_text)}</span>
    </div>

    <h2>Overview: 当前验证阶段</h2>
    {_render_pipeline_overview(raw_text, classification.segments, detected, elapsed_text)}

    <h2>Source Type Dictionary</h2>
    <p class="note">这里展示当前分类器支持的文本来源类型，并标出本次输出中实际出现的次数。</p>
    {_render_type_dictionary(counts)}

    <h2>原文染色查看</h2>
    <p class="note">颜色对应 source type。这里直接在原始病例文本上标出 LLM 分段结果，用来检查是否切分合理、是否漏分或错分。</p>
    <div class="highlight-box">{highlighted_text}</div>
    {_render_unmatched_notice(unmatched_segments)}

    <h2>Step 2 Output: 文本分段与来源类型分类</h2>
    <p class="note">这一阶段只判断每个片段属于哪类医学信息来源，不做建图，不做子图合并。</p>
    {_render_segments_table(classification.segments)}

    <h2>Segment Cards</h2>
    {_render_segment_cards(classification.segments)}
  </main>
</body>
</html>
"""


def _render_pipeline_overview(raw_text: str, segments: list, detected: list[str], elapsed: str) -> str:
    cards = [
        ("Input", str(len(raw_text)), "原始病例文本字符数"),
        ("Step 1", "1", "读取并准备病例文本"),
        ("Step 2", str(len(segments)), "LLM 分段 + 来源类型分类"),
        ("Output", str(len(detected)), f"识别出的来源类型；耗时 {elapsed}"),
    ]
    return '<div class="pipeline">' + "".join(
        "<div class='stage-card'>"
        f"<strong>{escape(title)}</strong>"
        f"<span class='count'>{escape(count)}</span>"
        f"<span class='desc'>{escape(desc)}</span>"
        "</div>"
        for title, count, desc in cards
    ) + "</div>"


def _render_type_dictionary(counts: Counter) -> str:
    rows = []
    for source_type in SourceType:
        key = str(source_type)
        count = counts.get(key, 0)
        color = SOURCE_TYPE_COLORS.get(key, "#e5e7eb")
        rows.append(
            "<tr>"
            f"<td><span style='background:{escape(color)}; padding:2px 8px; border-radius:4px;'>"
            f"{escape(SOURCE_TYPE_ZH.get(key, key))}</span></td>"
            f"<td><code>{escape(key)}</code></td>"
            f"<td>{count}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>中文说明</th><th>代码名称</th><th>当前数量</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _render_segments_table(segments: list) -> str:
    rows = []
    for seg in segments:
        source_type = str(seg.source_type)
        rows.append(
            "<tr>"
            f"<td>{escape(seg.segment_id)}</td>"
            f"<td>{_source_badge(source_type)}</td>"
            f"<td>{seg.confidence:.2f}</td>"
            f"<td>{escape(seg.rationale)}</td>"
            f"<td>{escape(seg.text)}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Segment</th><th>Source Type</th><th>Confidence</th>"
        "<th>Rationale</th><th>Text</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _render_segment_cards(segments: list) -> str:
    cards = []
    for seg in segments:
        source_type = str(seg.source_type)
        color = SOURCE_TYPE_COLORS.get(source_type, "#e5e7eb")
        cards.append(
            f"<div class='segment-card' style='border-left-color:{escape(color)}'>"
            f"<h3>{escape(seg.segment_id)} · {escape(SOURCE_TYPE_ZH.get(source_type, source_type))}</h3>"
            f"<div class='fields'>source_type=<code>{escape(source_type)}</code> · "
            f"confidence={seg.confidence:.2f} · rationale={escape(seg.rationale)}</div>"
            f"<pre>{escape(seg.text)}</pre>"
            "</div>"
        )
    return "".join(cards)


def _render_highlighted_raw_text(raw_text: str, segments: list) -> tuple[str, list[str]]:
    cursor = 0
    parts: list[str] = []
    unmatched: list[str] = []

    for seg in segments:
        segment_text = seg.text
        match_text = segment_text
        start = raw_text.find(match_text, cursor)
        if start == -1 and segment_text.strip():
            match_text = segment_text.strip()
            start = raw_text.find(match_text, cursor)
        if start == -1:
            unmatched.append(seg.segment_id)
            continue

        end = start + len(match_text)
        if start > cursor:
            parts.append(escape(raw_text[cursor:start]))

        source_type = str(seg.source_type)
        color = SOURCE_TYPE_COLORS.get(source_type, "#e5e7eb")
        label = f"{seg.segment_id} · {SOURCE_TYPE_ZH.get(source_type, source_type)}"
        parts.append(
            f"<span title='{escape(label)}' style='background:{escape(color)};'>"
            f"{escape(raw_text[start:end])}</span>"
        )
        cursor = end

    if cursor < len(raw_text):
        parts.append(escape(raw_text[cursor:]))
    return "".join(parts), unmatched


def _render_unmatched_notice(unmatched_segments: list[str]) -> str:
    if not unmatched_segments:
        return ""
    items = ", ".join(escape(item) for item in unmatched_segments)
    return (
        "<div class='unmatched'>"
        "以下 segment 未能在原文中精确匹配，因此没有被染色："
        f"<code>{items}</code>。通常说明 LLM 对片段文本做了改写或省略，需要人工检查。"
        "</div>"
    )


def _source_badge(source_type: str) -> str:
    color = SOURCE_TYPE_COLORS.get(source_type, "#e5e7eb")
    label = SOURCE_TYPE_ZH.get(source_type, source_type)
    return (
        f"<span style='background:{escape(color)}; padding:2px 8px; border-radius:4px;'>"
        f"{escape(label)} / <code>{escape(source_type)}</code></span>"
    )

