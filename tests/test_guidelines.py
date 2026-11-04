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
            assert limit == 6
            return [GuidelineSearchHit(chunk=chunk(), score=0.91)]

    runtime = GuidelineRuntime(
        FakeRetriever(), ["guide"], limit=3
    )
    prompt, allowed, trace = runtime.prepare_query("诊断信度")
    assert "guide:p001:c001" in prompt
    assert '"unit_type":"recommendation"' in prompt
    assert set(allowed) == {"guide:p001:c001"}
    assert trace["candidates"][0]["chunk_id"] == "guide:p001:c001"


def test_runtime_honors_stage_limit_and_skips_zero_limit_stage():
    calls = []

    class FakeRetriever:
        def search(self, query, *, guideline_ids, limit):
            calls.append((query, limit))
            return [GuidelineSearchHit(chunk=chunk(), score=0.91)]

    runtime = GuidelineRuntime(
        FakeRetriever(),
        ["guide"],
        limit=6,
        limits={"assessment": 2, "mapping": 0},
    )

    prompt, _, _ = runtime.prepare_case("assessment", {"diagnosis": "诊断"})
    skipped, allowed, _ = runtime.prepare_case("mapping", {"diagnosis": "映射"})

    assert len(calls) == 1 and "诊断" in calls[0][0] and calls[0][1] == 4
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



def test_clinical_passages_keep_rules_and_disease_descriptions_together():
    text = ("【推荐意见1】建议疑似患者接受规范的高分辨率CT检查。"
            "進展性肺纤维化定义为一年内满足规定的进展条件。"
            "肺功能阈值为FVC绝对下降≥5%。"
            "纤维化可与气道病变及肺气肿并存，应分别解释其影像和生理贡献。")
    units = _atomic_units(text)
    assert len(units) == 1
    assert units[0][1] == __import__("unicodedata").normalize("NFKC", text)
    assert all(len(unit[1]) <= MAX_UNIT_CHARS for unit in units)


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


def test_clinical_description_without_recommendation_or_definition_is_preserved():
    text = "ILD can also be combined with non-ILD abnormalities, or combined pulmonary fibrosis and emphysema."
    assert _atomic_units(text) == [("clinical_description", text)]


def test_long_descriptions_are_split_without_losing_late_discriminators():
    text = "Clinical fibrosis has multiple morphologic findings. " * 50 + "Coexisting emphysema must also be evaluated."
    units = _atomic_units(text)
    assert len(units) > 1
    assert all(len(unit[1]) <= MAX_UNIT_CHARS for unit in units)
    assert "Coexisting emphysema" in units[-1][1]


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


def test_reference_section_stops_ingestion_even_when_heading_shares_a_block():
    from src.guidelines.index import _chunk_document
    from src.guidelines.catalog import load_catalog
    document = next(iter(load_catalog(Path('data/guidelines/catalog.yaml')).values()))
    def page(text):
        return dict(width=600, height=800, blocks=[dict(x0=40, x1=560, y0=180, y1=500, text=text)])
    chunks = _chunk_document(document, [page('Clinical disease features include fibrosis and airway abnormalities.'),
        page('References\n1 Smith A et al. Disease mentioned in a bibliography.'),
        page('2 Jones A et al. Further bibliographic mentions.')], '0' * 64)
    assert chunks and all(c.page == 1 for c in chunks)


def test_runtime_retrieves_each_candidate_question_including_late_candidates():
    from src.guidelines.runtime import GuidelineRuntime
    seen = []
    class Retriever:
        manifest = {'chunker_version': 'clinical_passages_v3'}
        def search(self, query, **kwargs):
            seen.append(query)
            return [GuidelineSearchHit(chunk=chunk(), score=1)]
    runtime = GuidelineRuntime(Retriever(), ['guide'])
    _, _, trace = runtime.prepare_queries([f'candidate {i}' for i in range(6)])
    assert len(seen) == 6
    assert trace['queries'] == seen and trace['index_version'] == 'clinical_passages_v3'


def test_short_exact_quote_units_survive_noncontiguous_splitting():
    source = chunk().model_copy(update={"text": "(3)炎性关节炎或多关节晨僵≥60 min;(4)手掌毛细血管扩张;(5)雷诺现象;"})
    units = guideline_quote_units(source)
    pointers = [GuidelineEvidencePointer(
        chunk_id=source.chunk_id, quote_unit_ids=[units[0].quote_unit_id, units[2].quote_unit_id],
        relevance="临床域", application="区分临床表现",
    )]
    assert resolve_guideline_evidence(pointers, {source.chunk_id: source}) == [source.chunk_id]
    assert [p.quote for p in pointers] == [units[0].text, "(5)雷诺现象;"]
    assert all(source.text[p.quote_start:p.quote_end] == p.quote for p in pointers)
    legacy = GuidelineEvidencePointer(chunk_id=source.chunk_id, quote="雷诺现象", relevance="临床域", application="判断")
    with pytest.raises(ValueError, match="too short"):
        resolve_guideline_evidence(legacy, {source.chunk_id: source})
