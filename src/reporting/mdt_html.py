"""Standalone clinician view of a saved MDT report."""
import json
from collections import defaultdict
from pathlib import Path

from src.agents.common.team_synthesis import MDTTeamReport
from src.agents.mdt_discussion.final_report import report_diagnosis
from src.agents.mdt_discussion.models import MDTDiscussionState
from src.utils.persistence import write_text

from .mdt_discussion_html import render_discussion
from .mdt_html_parts import details as _details, paragraph as _paragraph, text as _text

_NEED_STATUS = {"available": "已有", "partially_available": "待核对", "missing": "缺资料"}
_OTHER_PROBLEMS = {
    "acute_contributor": "近期变化及相关因素",
    "comorbidity": "并存问题",
    "unexplained_finding": "其他尚待明确的发现",
}
_NEED_ACTION = {
    "retrieve_existing": "核对已有结果", "obtain_new": "补充检查或资料",
    "retain_boundary": "现有资料暂不能明确", "no_further_request": "暂不补充资料",
}


_SOURCE = {
    "demographics": "人口学资料", "chief_complaint": "主诉", "present_illness": "现病史",
    "past_medical_history": "既往史", "exposure_history": "暴露史", "family_history": "家族史",
    "medication_history": "用药史", "imaging_findings": "影像", "pathology_findings": "病理",
    "pulmonary_function_findings": "肺功能", "physical_exam": "查体", "laboratory_findings": "实验室检查",
    "ctd_related_findings": "结缔组织病相关资料", "bronchoscopy_findings": "支气管镜",
    "clinician_assessment": "既往临床判断",
}


def _artifact(path: Path, report: MDTTeamReport, suffix: str) -> dict:
    source = path.with_name(f"{report.case_id}_{suffix}.json")
    return json.loads(source.read_bytes()) if source.is_file() else {}


def _document_id(item):
    return item.get("guideline_id") or item.get("source_file") or item.get("title") or item["chunk_id"]


def _labeled(label: str, body: str) -> str:
    if not body:
        return ""
    return f'<div class="labeled"><h4>{_text(label)}</h4><div>{body}</div></div>'


def write_mdt_html(
    path: Path, report: MDTTeamReport, state: MDTDiscussionState | None = None,
) -> None:
    team = report.team_synthesis
    primary = next(problem for problem in team.problems if problem.relationship == "primary")
    titles = {problem.problem_id: problem.title for problem in team.problems}
    needs_by_problem = defaultdict(list)
    for index, need in enumerate(team.evidence_needs, start=1):
        needs_by_problem[need.problem_id].append((index, need))
    boundaries_by_problem = {}
    for index, boundary in enumerate(team.judgment_boundaries, start=1):
        boundaries_by_problem.setdefault(boundary.problem_id, index)
    names = {
        candidate.candidate_id: candidate.diagnosis
        for candidate in (team.diagnostic_exploration.candidates if team.diagnostic_exploration else [])
    }
    propositions = _artifact(path, report, "clinical_propositions")
    blocks = {
        block["evidence_id"]: block["text"]
        for segment in propositions.get("segments", [])
        for unit in segment["units"]
        for block in unit["evidence_blocks"]
    }
    graph = _artifact(path, report, "graph_units")
    units = {unit["graph_unit_id"]: unit for segment in graph.get("segments", []) for unit in segment["graph_units"]}

    references = {}
    linked_guidelines = set()
    final_guidelines = [item.model_dump() for item in team.guideline_evidence]

    def guidelines(items, label):
        groups = defaultdict(dict)
        for item in items:
            doc_id = _document_id(item)
            number, _ = references.setdefault(doc_id, (len(references) + 1, item))
            key = (item["chunk_id"], tuple(item["quote_unit_ids"]), item.get("application"))
            groups[doc_id].setdefault(key, item)
        entries = []
        for doc_id, excerpts in groups.items():
            number, document = references[doc_id]
            body = []
            for item in excerpts.values():
                locator = list(item.get("section_path") or [])
                if item.get("page") is not None:
                    locator.append(f'PDF 第 {item["page"]} 页')
                body.append(
                    '<article class="citation">' + _paragraph(" · ".join(locator), "引文位置　")
                    + f'<blockquote>{_text(item.get("quote") or "报告未保存引文原文。")}</blockquote>'
                    + _paragraph(item.get("application"), "本例应用　") + "</article>"
                )
            title = document.get("title") or doc_id
            entries.append(_details(
                f'<span class="cite-no">[{number}]</span> {_text(title)}',
                "".join(body) + f'<a href="#ref-{number}">查看完整出处</a>', "guide",
            ))
        return _labeled(label, "".join(entries))

    def evidence(refs, source_refs=(), directions=()) -> str:
        # Distinct references may select the same source block. Preserve its identity, not its wording.
        entries = {}
        claims = defaultdict(list)
        for source_ref in source_refs:
            source = team.source_catalog.get(source_ref, {})
            for claim in source.get("claims") or []:
                for relation in claim.get("evidence_relations", []):
                    if relation.get("direction") in directions:
                        claims[relation["evidence_ref"]].append(claim["statement"])
        for ref in refs:
            item = team.evidence_catalog.get(ref, {})
            unit = units.get(item.get("graph_unit_id"), {})
            location = _SOURCE.get(unit.get("source_type"), "病例资料")
            when = unit.get("temporal_anchor")
            label = f"{location} · {when}" if when else location
            selected = [(key, blocks[key]) for key in item.get("evidence_ids", []) if key in blocks]
            if len(selected) != len(item.get("evidence_ids", [])) or not selected:
                selected = [(ref, item.get("quote") or "报告未保存该证据原文。")]
            for key, quote in selected:
                entry = entries.setdefault(key, (label, quote, {}))
                entry[2].update(dict.fromkeys(claims[ref]))
        rendered = []
        shown_claims = set()
        for label, quote, interpretations in entries.values():
            distinct = [value for value in interpretations if value not in shown_claims]
            shown_claims.update(distinct)
            rendered.append(
                '<li><span class="evidence-source">' + _text(label) + "</span>"
                + (_paragraph(distinct[0]) + _details(
                    "查看原文", f'<blockquote>{_text(quote)}</blockquote>'
                    + "".join(_paragraph(value) for value in distinct[1:]), "original"
                ) if distinct else _paragraph(quote)) + "</li>"
            )
        return f'<ul class="evidence">{"".join(rendered)}</ul>' if rendered else ''

    def candidate_row(candidate, problem_id) -> str:
        cited_units = {
            quote_id for ref in candidate.source_refs
            for item in team.source_catalog.get(ref, {}).get("guideline_evidence", [])
            for quote_id in item.get("quote_unit_ids", [])
        }
        # The sealed report has no explicit candidate-to-guideline field. Match only shared quote units.
        matched = [item for item in final_guidelines if cited_units.intersection(item["quote_unit_ids"])]
        linked_guidelines.update((item["chunk_id"], tuple(item["quote_unit_ids"])) for item in matched)
        name = names.get(candidate.candidate_id) or candidate.diagnosis
        preferred = problem_id == primary.problem_id and candidate.position == "preferred"
        supporting = evidence(candidate.supporting_evidence_refs, candidate.source_refs, ("supports",))
        opposing = evidence(candidate.opposing_evidence_refs, candidate.source_refs, ("weakens",))
        action = "查看依据" if preferred else "查看鉴别依据"
        return _details(
            f'<span class="row-name">{_text(name)}</span>'
            f'<span class="link-action" data-open="收起依据">{action}</span>',
            _labeled("支持依据", supporting or _paragraph("本次未记录明确支持依据。"))
            + _labeled("反对或削弱依据", opposing or _paragraph("本次未记录明确反对依据。"))
            + _labeled("尚待明确", _paragraph("；".join(candidate.limitations)))
            + guidelines(matched, "指南依据"),
            "candidate",
        )

    preferred = next((item for item in primary.candidates if item.position == "preferred"), None)
    alternatives = [item for item in primary.candidates if item.position != "preferred"]
    main_basis = candidate_row(preferred, primary.problem_id) if preferred else ""
    if not main_basis and team.diagnostic_opinion:
        opinion = team.diagnostic_opinion
        main_basis = _details(
            '<span class="row-name">综合分析</span><span class="link-action" data-open="收起依据">查看依据</span>',
            _paragraph(opinion.reasoning) + evidence(opinion.evidence_refs),
            "candidate",
        )
    candidates = ("<h3>主要依据</h3>" + main_basis if main_basis else "") + (
        "<h3>鉴别诊断</h3>" + "".join(candidate_row(item, primary.problem_id) for item in alternatives)
        if alternatives else ""
    )
    other_problems = "".join(
        _details(
            _text(problem.title),
            _paragraph(problem.statement)
            + "".join(candidate_row(item, problem.problem_id) for item in problem.candidates)
            + (evidence(problem.evidence_refs) if not problem.candidates and problem.evidence_refs else ""),
        )
        for problem in team.problems if problem.relationship != "primary"
    )
    general_guidelines = guidelines(
        [item for item in final_guidelines if (item["chunk_id"], tuple(item["quote_unit_ids"])) not in linked_guidelines],
        "全案参考依据",
    )
    conclusion = report_diagnosis(team)
    grouped_problems = defaultdict(list)
    for problem in team.problems:
        if problem.relationship != "primary":
            grouped_problems[problem.relationship].append(f"<li>{_text(problem.statement)}</li>")
    associated = "".join(
        f'<h3>{label}</h3><ul>{"".join(grouped_problems[key])}</ul>'
        for key, label in _OTHER_PROBLEMS.items() if grouped_problems[key]
    )

    boundaries = []
    for index, boundary in enumerate(team.judgment_boundaries, start=1):
        material_links = "".join(
            f'<li><a href="#need-{i}">{_text(need.information)}</a></li>'
            for i, need in needs_by_problem[boundary.problem_id]
        )
        body = (
            _paragraph(boundary.established, "现有结论　")
            + _paragraph(boundary.reason, "原因　")
            + _paragraph(boundary.decision_impact, "对诊断的影响　")
        )
        boundaries.append(
            f'<article id="question-{index}" class="document-row">'
            + f'<h3>{_text(titles[boundary.problem_id])}</h3>'
            + _paragraph(boundary.undetermined)
            + (_details("查看相关资料", f'<ul class="related-materials">{material_links}</ul>', "inline-detail")
               if material_links else "")
            + _details("查看分析", body, "inline-detail") + "</article>"
        )
    if team.diagnostic_opinion and team.diagnostic_opinion.limitations:
        limitations = dict.fromkeys(team.diagnostic_opinion.limitations)
        boundaries.append(_details("其他待明确事项", "".join(map(_paragraph, limitations)), "inline-detail"))

    needs = []
    for index, need in enumerate(team.evidence_needs, start=1):
        boundary_index = boundaries_by_problem.get(need.problem_id)
        related = (
            f'<a href="#question-{boundary_index}">查看相关问题</a>' if boundary_index else ""
        )
        body = (
            _paragraph(need.available_information, "已有资料　")
            + _paragraph(need.missing_information, "待核实内容　")
            + _paragraph(_NEED_ACTION[need.action], "处理意见　")
            + "".join(_paragraph(f"{branch.result} → {branch.changed_decision}") for branch in need.branches)
            + _paragraph(need.if_unavailable, "如资料无法取得　")
            + related
        )
        needs.append(
            f'<div id="need-{index}">' + _details(
                f'<span class="row-name">{_text(need.information)}</span>'
                f'<span class="row-status">{_NEED_STATUS[need.status]}</span>'
                '<span class="link-action" data-open="收起">查看资料</span>',
                body, "material",
            ) + '</div>'
        )

    discussion = render_discussion(
        report, state, _artifact(path, report, "mdt_chair_integration"), guidelines, blocks,
    )
    bibliography = []
    for number, item in references.values():
        title = item.get("title") or _document_id(item)
        authors = " · ".join(str(part) for part in (item.get("organization"), item.get("year")) if part)
        bibliography.append(
            f'<li id="ref-{number}"><cite>{_text(title)}</cite>'
            f'<span>{_text(authors)}</span></li>'
        )

    style = Path(__file__).with_name("mdt_html.css").read_text(encoding="utf-8")
    script = Path(__file__).with_name("mdt_html.js").read_text(encoding="utf-8")
    write_text(path, f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>间质性肺疾病多学科讨论报告</title>
<style>{style}</style>
</head>
<body>
<main class="sheet">
  <div id="report">
    <header class="document-header">
      <div class="kicker">间质性肺疾病</div>
      <div class="heading">
        <h1>多学科讨论报告</h1>
        <nav aria-label="报告操作"><a href="#discussion">讨论记录</a><button type="button" id="print" hidden>打印</button></nav>
      </div>
    </header>
    <section id="result">
      <h2><span class="number">01</span>诊断结果</h2>
      <div class="section-content">
        <p class="diagnosis-result">{_text(conclusion)}</p>
        {associated}
      </div>
    </section>
    <section id="diagnosis">
      <h2><span class="number">02</span>诊断依据与鉴别诊断</h2>
      <div class="section-content">
        {candidates or _paragraph("本次未单列诊断依据。")}
        {_details("主要问题的原始判断", _paragraph(primary.statement), "inline-detail") if primary.statement != conclusion else ""}
        {_details("其他临床问题", other_problems, "inline-detail") if other_problems else ""}
      </div>
    </section>
    <section id="boundaries">
      <h2><span class="number">03</span>尚待明确的问题</h2>
      <div class="section-content">{"".join(boundaries) or _paragraph("本次未单列待明确的问题。")}</div>
    </section>
    <section id="needs">
      <h2><span class="number">04</span>待核实资料</h2>
      <div class="section-content">{"".join(needs) or _paragraph("本次未单列待核实资料。")}</div>
    </section>
    <section id="references">
      <h2>参考文献</h2>
      {general_guidelines}
      {"<ol>" + "".join(bibliography) + "</ol>" if bibliography else _paragraph("本次未列参考文献。")}
    </section>
  </div>
  <div id="discussion">
    <header class="document-header">
      <a href="#report" class="back-link">返回报告</a>
      <h1>多学科讨论记录</h1>
    </header>
    {discussion}
  </div>
</main>
<script>{script}</script>
</body>
</html>
""")
