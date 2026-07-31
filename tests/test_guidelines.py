import json
from pathlib import Path

import pytest

from src.guidelines.catalog import load_catalog
from src.guidelines.index import (
    MAX_UNIT_CHARS,
    _atomic_units,
    _embedding_text,
    _looks_corrupted,
    _normalize_text,
    _ordered_blocks,
    _repeated_margin_keys,
)
from src.guidelines.models import (
    GuidelineChunk,
    GuidelineEvidencePointer,
    GuidelineSearchHit,
)
from src.guidelines.runtime import (
    GuidelineRuntime,
    guideline_evidence_schema_constraints,
    guideline_quote_units,
    resolve_guideline_evidence,
)
from src.llm.structured import json_schema_response_format
from src.reporting.specialty_report_common import (
    render_guideline_audit,
    render_reasoning_audit,
)
from src.agents.pulmonology.models import ClinicalAssessmentItem
from src.utils.config import load_yaml


GUIDELINE_DIR = Path("data/guidelines")


def chunk() -> GuidelineChunk:
    return GuidelineChunk(
        chunk_id="guide:p001:c001",
        guideline_id="guide",
        title="测试指南",
        organization="测试学会",
        year=2025,
        source_file="guide.pdf",
        page=1,
        section_path=["诊断"],
        unit_type="recommendation",
        text="指南建议对疑似患者进行完整的临床、影像和肺功能综合评估。",
        document_sha256="a" * 64,
    )


def test_catalog_has_unique_existing_sources():
    catalog = load_catalog(GUIDELINE_DIR / "catalog.yaml")
    assert len(catalog) == 6
    assert all((GUIDELINE_DIR / item.file).is_file() for item in catalog.values())


def test_agent_yaml_uses_only_canonical_guideline_ids():
    catalog_ids = set(load_catalog(GUIDELINE_DIR / "catalog.yaml"))
    for specialty in ("pulmonology", "thoracic_radiology", "pathology", "rheumatology"):
        config = load_yaml(Path("configs/agents") / specialty / "agent.yaml")
        referenced = set(config["guideline_retrieval"]["scope"])
        for rule in config["clinical_rules"].values():
            referenced.update(rule.get("guideline_ids", []))
            if rule.get("guideline_id"):
                referenced.add(rule["guideline_id"])
        assert referenced <= catalog_ids


def test_citation_metadata_is_resolved_only_from_retrieved_chunk():
    source = chunk()
    pointer = GuidelineEvidencePointer(
        chunk_id=source.chunk_id,
        quote_unit_ids=[
            unit.quote_unit_id for unit in guideline_quote_units(source)
        ],
        relevance="规定诊断方法",
        application="用于解释患者资料",
    )
    used = resolve_guideline_evidence(pointer, {pointer.chunk_id: source})
    assert used == [pointer.chunk_id]
    assert pointer.page == 1
    assert pointer.quote == source.text
    assert source.text[pointer.quote_start : pointer.quote_end] == pointer.quote


def test_runtime_records_retrieved_candidates_for_stage():
    class FakeRetriever:
        def search(self, query, *, guideline_ids, limit):
            assert query == "诊断信度"
            assert guideline_ids == ["guide"]
            assert limit == 3
            return [GuidelineSearchHit(chunk=chunk(), score=0.91)]

    runtime = GuidelineRuntime(
        FakeRetriever(), ["guide"], {"initial": "诊断信度"}, limit=3
    )
    prompt, allowed, trace = runtime.prepare("initial")
    assert "guide:p001:c001" in prompt
    assert '"unit_type":"recommendation"' in prompt
    assert set(allowed) == {"guide:p001:c001"}
    assert trace["candidates"] == [{"chunk_id": "guide:p001:c001", "score": 0.91}]


def test_runtime_honors_stage_limit_and_skips_zero_limit_stage():
    calls = []

    class FakeRetriever:
        def search(self, query, *, guideline_ids, limit):
            calls.append((query, limit))
            return [GuidelineSearchHit(chunk=chunk(), score=0.91)]

    runtime = GuidelineRuntime(
        FakeRetriever(),
        ["guide"],
        {"assessment": "诊断", "mapping": "映射"},
        limit=6,
        limits={"assessment": 2, "mapping": 0},
    )

    prompt, _, _ = runtime.prepare("assessment")
    skipped, allowed, _ = runtime.prepare("mapping")

    assert calls == [("诊断", 2)]
    assert set(json.loads(prompt)[0]) == {
        "chunk_id",
        "section_path",
        "unit_type",
        "quote_units",
    }
    assert skipped == "[]"
    assert allowed == {}


def test_runtime_reuses_one_retriever_for_the_same_directory(monkeypatch, tmp_path):
    created = []

    class FakeRetriever:
        def __init__(self, directory):
            created.append(directory)

    monkeypatch.setattr("src.guidelines.runtime.GuidelineRetriever", FakeRetriever)
    GuidelineRuntime._retrievers.clear()
    config = {"guideline_retrieval": {"enabled": True, "directory": str(tmp_path)}}

    first = GuidelineRuntime.from_config(config)
    second = GuidelineRuntime.from_config(config)

    assert first is not None and second is not None
    assert first.retriever is second.retriever
    assert created == [tmp_path.resolve()]


def test_unretrieved_citation_is_rejected():
    pointer = GuidelineEvidencePointer(
        chunk_id="invented",
        quote_unit_ids=["invented:q001"],
        relevance="无",
        application="无",
    )
    with pytest.raises(ValueError, match="was not retrieved"):
        resolve_guideline_evidence(pointer, {})


def test_retrieved_chunk_ids_are_closed_in_structured_output_schema():
    model = ClinicalAssessmentItem
    allowed = {chunk().chunk_id: chunk()}
    constraints = guideline_evidence_schema_constraints(allowed)
    schema = json_schema_response_format(
        model,
        "clinical_assessment",
        pointer_field_constraints=constraints,
    )["json_schema"]["schema"]
    guideline_items = schema["properties"]["guideline_evidence"]["items"]

    assert guideline_items["properties"]["chunk_id"]["enum"] == [
        "guide:p001:c001"
    ]
    assert "quote_unit_ids" in guideline_items["required"]
    assert "quote" not in guideline_items["properties"]
    assert guideline_items["properties"]["quote_unit_ids"]["items"]["enum"] == [
        unit.quote_unit_id for unit in guideline_quote_units(chunk())
    ]

    empty_schema = json_schema_response_format(
        model,
        "clinical_assessment",
        pointer_field_constraints=guideline_evidence_schema_constraints({}),
    )["json_schema"]["schema"]
    assert empty_schema["properties"]["guideline_evidence"]["maxItems"] == 0


def test_shared_report_renders_exact_guideline_location(tmp_path):
    source = chunk().model_copy(
        update={"source_file": "cra_ctd-ild_standard_2022_zh.pdf", "page": 4}
    )
    pointer = GuidelineEvidencePointer(
        chunk_id=source.chunk_id,
        quote_unit_ids=[
            unit.quote_unit_id for unit in guideline_quote_units(source)
        ],
        relevance="规定诊断方法",
        application="用于解释患者资料",
    )
    resolve_guideline_evidence(pointer, {source.chunk_id: source})
    item = ClinicalAssessmentItem(
        assessment="工作判断",
        confidence="moderate",
        reasoning_summary="病例证据和指南共同限定该判断。",
        guideline_evidence=[pointer],
    )
    report_path = tmp_path / "report.html"
    html = render_reasoning_audit(item, {}, report_path) + render_guideline_audit(
        item, report_path
    )
    assert pointer.quote in html
    assert "PDF 第 4 页" in html
    assert "#page=4" in html
    assert "相关上下文（不直接支持结论）" in html


def test_atomic_units_split_recommendation_definition_and_threshold():
    text = (
        "【推荐意见1】建议疑似患者接受规范的高分辨率CT检查。"
        "进展性肺纤维化定义为一年内满足规定的进展条件。"
        "肺功能阈值为FVC绝对下降≥5%。"
    )

    units = _atomic_units(text)

    assert units == [
        ("recommendation", "【推荐意见1】建议疑似患者接受规范的高分辨率CT检查。"),
        ("definition", "进展性肺纤维化定义为一年内满足规定的进展条件。"),
        ("threshold", "肺功能阈值为FVC绝对下降≥5%。"),
    ]
    assert all(len(item[1]) <= MAX_UNIT_CHARS for item in units)


def test_repeated_margins_are_removed_and_two_columns_keep_reading_order():
    pages = [
        {
            "width": 600,
            "height": 800,
            "blocks": [
                {"x0": 50, "y0": 20, "x1": 550, "y1": 35, "text": "期刊 2025 第1页"},
                {"x0": 50, "y0": 100, "x1": 280, "y1": 120, "text": "左栏第一句。"},
                {"x0": 320, "y0": 90, "x1": 550, "y1": 110, "text": "右栏第一句。"},
                {"x0": 50, "y0": 130, "x1": 280, "y1": 150, "text": "左栏第二句。"},
            ],
        }
        for _ in range(5)
    ]
    for page_number, page in enumerate(pages, start=1):
        page["blocks"][0]["text"] = f"期刊 2025 第{page_number}页"

    repeated = _repeated_margin_keys(pages)
    ordered = _ordered_blocks(pages[0], repeated, "zh")

    assert [item["text"] for item in ordered] == [
        "左栏第一句。",
        "左栏第二句。",
        "右栏第一句。",
    ]


def test_corrupted_table_text_is_rejected():
    assert _looks_corrupted("\x03\x03\x03\x03乱码表格ۘฮߵ", "zh")
    assert not _looks_corrupted("建议患者接受高分辨率CT检查。", "zh")


def test_normalization_removes_pdf_line_wrap_spaces_between_chinese_characters():
    assert _normalize_text("风湿免 疫科\n应进行 评估") == "风湿免疫科应进行评估"


def test_atomic_units_reject_noise_and_incomplete_fragments():
    text = (
        "Consensus required ≥70% agreement on each recommendation."
        "Correspondence and requests for reprints should be addressed to the author."
        "Absolute decline in DLCO >10% within one year 3 Radiological evidence of progression: a."
        "残气量占预计值百分比<80%、第1秒钟用力呼气"
        "We suggest performing pulmonary function tests every 3–6 months."
    )

    assert _atomic_units(text) == [
        (
            "recommendation",
            "We suggest performing pulmonary function tests every 3–6 months.",
        )
    ]


def test_recommendation_strength_is_attached_to_preceding_statement():
    text = (
        "【推荐意见1】建议将MDD纳入ILD患者诊治流程。"
        "【强推荐】三、病例评估内容。"
    )

    assert _atomic_units(text) == [
        (
            "recommendation",
            "【推荐意见1】建议将MDD纳入ILD患者诊治流程 【强推荐】。",
        )
    ]


def test_embedding_uses_context_without_polluting_quote_text():
    source = chunk()

    embedded = _embedding_text(source)

    assert source.title in embedded
    assert source.section_path[0] in embedded
    assert source.unit_type in embedded
    assert embedded.endswith(source.text)


def test_modified_or_ambiguous_guideline_quote_is_rejected():
    source = chunk()
    modified = GuidelineEvidencePointer(
        chunk_id=source.chunk_id,
        quote="对疑似患者进行完整的临床、影像和病理综合评估",
        relevance="规定诊断方法",
        application="用于解释患者资料",
    )
    with pytest.raises(ValueError, match="not an exact substring"):
        resolve_guideline_evidence(modified, {source.chunk_id: source})

    repeated_source = source.model_copy(
        update={"text": "完整的临床、影像和肺功能综合评估；完整的临床、影像和肺功能综合评估。"}
    )
    ambiguous = GuidelineEvidencePointer(
        chunk_id=source.chunk_id,
        quote="完整的临床、影像和肺功能综合评估",
        relevance="规定诊断方法",
        application="用于解释患者资料",
    )
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_guideline_evidence(ambiguous, {source.chunk_id: repeated_source})


def test_legacy_exact_guideline_quote_remains_readable():
    source = chunk()
    quote = "对疑似患者进行完整的临床、影像和肺功能综合评估"
    pointer = GuidelineEvidencePointer(
        chunk_id=source.chunk_id,
        quote=quote,
        relevance="规定诊断方法",
        application="用于解释患者资料",
    )

    resolve_guideline_evidence(pointer, {source.chunk_id: source})

    assert pointer.quote_unit_ids == []
    assert pointer.quote == quote
    assert source.text[pointer.quote_start : pointer.quote_end] == quote


def test_quote_unit_selection_preserves_original_unicode_characters():
    source = chunk().model_copy(
        update={
            "text": (
                "怀疑AAV时应查抗中性粒细胞胞质抗体"
                "(anti‐neutrophil cytoplasmic antibodies, ANCA)。"
            )
        }
    )
    units = guideline_quote_units(source)
    assert len(units) == 1
    assert "(anti‐neutrophil cytoplasmic antibodies, ANCA)" in units[0].text
    pointer = GuidelineEvidencePointer(
        chunk_id=source.chunk_id,
        quote_unit_ids=[unit.quote_unit_id for unit in units],
        relevance="规定检查项目",
        application="用于当前检查判断",
    )

    resolve_guideline_evidence(pointer, {source.chunk_id: source})

    assert "anti‐neutrophil" in pointer.quote
    assert "anti-neutrophil" not in pointer.quote
    assert pointer.quote == source.text


def test_quote_units_must_belong_to_the_chunk_and_noncontiguous_are_split():
    source = chunk().model_copy(
        update={
            "text": (
                "第一项完整且足够长度的指南建议，"
                "第二项完整且足够长度的指南建议，"
                "第三项完整且足够长度的指南建议。"
            )
        }
    )
    units = guideline_quote_units(source)
    unknown = GuidelineEvidencePointer(
        chunk_id=source.chunk_id,
        quote_unit_ids=[f"{source.chunk_id}:q999"],
        relevance="相关",
        application="用于判断",
    )
    with pytest.raises(ValueError, match="do not belong"):
        resolve_guideline_evidence(unknown, {source.chunk_id: source})

    noncontiguous = GuidelineEvidencePointer(
        chunk_id=source.chunk_id,
        quote_unit_ids=[units[0].quote_unit_id, units[2].quote_unit_id],
        relevance="相关",
        application="用于判断",
    )
    result = {"guideline_evidence": [noncontiguous]}

    resolve_guideline_evidence(result, {source.chunk_id: source})

    pointers = result["guideline_evidence"]
    assert [pointer.quote_unit_ids for pointer in pointers] == [
        [units[0].quote_unit_id],
        [units[2].quote_unit_id],
    ]
    assert [pointer.quote for pointer in pointers] == [units[0].text, units[2].text]
    assert all(
        source.text[pointer.quote_start : pointer.quote_end] == pointer.quote
        for pointer in pointers
    )
