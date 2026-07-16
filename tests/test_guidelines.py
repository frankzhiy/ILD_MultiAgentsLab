import json
from pathlib import Path

import pytest

from src.guidelines.catalog import load_catalog
from src.guidelines.models import (
    GuidelineChunk,
    GuidelineEvidencePointer,
    GuidelineSearchHit,
)
from src.guidelines.runtime import GuidelineRuntime, resolve_guideline_evidence
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
        text="指南原文",
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
    pointer = GuidelineEvidencePointer(
        chunk_id="guide:p001:c001",
        relevance="规定诊断方法",
        application="用于解释患者资料",
    )
    used = resolve_guideline_evidence(pointer, {pointer.chunk_id: chunk()})
    assert used == [pointer.chunk_id]
    assert pointer.page == 1
    assert pointer.quote == "指南原文"


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
    assert set(json.loads(prompt)[0]) == {"chunk_id", "section_path", "text"}
    assert skipped == "[]"
    assert allowed == {}


def test_unretrieved_citation_is_rejected():
    pointer = GuidelineEvidencePointer(
        chunk_id="invented",
        relevance="无",
        application="无",
    )
    with pytest.raises(ValueError, match="was not retrieved"):
        resolve_guideline_evidence(pointer, {})


def test_shared_report_renders_exact_guideline_location(tmp_path):
    source = chunk().model_copy(
        update={"source_file": "cra_ctd-ild_standard_2022_zh.pdf", "page": 4}
    )
    pointer = GuidelineEvidencePointer(
        chunk_id=source.chunk_id,
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
    assert "指南原文" in html
    assert "PDF 第 4 页" in html
    assert "#page=4" in html
    assert "相关上下文（不直接支持结论）" in html
