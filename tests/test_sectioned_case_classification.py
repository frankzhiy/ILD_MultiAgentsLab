import json
from pathlib import Path

import pytest

from src.agents.semantic_graphing.document_classifier import (
    DocumentClassifier,
    UnitRangeDocumentClassification,
    build_source_units,
    rebuild_document_classification,
)
from src.agents.semantic_graphing.graph_unit_extractor import SegmentGraphUnitExtractor
from src.llm.base import LLMResponse
from src.workbench.catalog import RunCatalog


TITLES = [
    "一、基本信息与主诉",
    "二、病史与暴露史",
    "三、体格检查与辅助检查",
    "四、诊疗经过",
    "五、当前评估与待明确问题",
]


def case_text():
    bodies = [
        "患者女，77岁，气短2月。",
        "否认粉尘接触。",
        "【2024-06-11 当地检查】\nCT示双肺间质增粗。\n【胸部影像报告】\n双侧胸膜增厚。",
        "抗感染后气短改善不明显。",
        "初步考虑间质性肺炎。",
    ]
    return "\n\n".join(f"{title}\n\n{body}" for title, body in zip(TITLES, bodies))


def classification_for(end_units):
    return UnitRangeDocumentClassification.model_validate({
        "segments": [
            {
                "end_unit": end,
                "unit_type": "other",
                "contained_source_types": ["other"],
                "clinical_frame": "test",
                "confidence": 1,
                "rationale": "test",
            }
            for end in end_units
        ]
    })


def test_section_and_context_headings_are_metadata_not_clinical_text():
    text = case_text()
    units = build_source_units(text)
    assert len(units) == 6
    assert units[2].section_title == TITLES[2]
    assert units[2].context_label == "2024-06-11 当地检查"
    assert units[3].context_label == "胸部影像报告"
    assert units[4].context_label is None
    for unit in units:
        assert unit.text == text[unit.start_char:unit.end_char]
        assert not any(title in unit.text for title in TITLES)
        assert "【" not in unit.text
    rebuilt = rebuild_document_classification(classification_for(range(1, 7)), text, units)
    assert rebuilt.segments[2].metadata == {
        "section_title": TITLES[2], "context_label": "2024-06-11 当地检查",
    }


@pytest.mark.parametrize("end_units", [[2, 3, 4, 5, 6], [1, 2, 4, 5, 6]])
def test_segment_cannot_cross_a_section_or_context_heading(end_units):
    text = case_text()
    with pytest.raises(ValueError, match="crosses a structural heading"):
        rebuild_document_classification(classification_for(end_units), text, build_source_units(text))


def test_empty_sections_do_not_create_clinical_segments():
    text = "\n\n".join(TITLES[:-1]) + "\n" + TITLES[-1] + "\n初步考虑IPF。"
    units = build_source_units(text)
    rebuilt = rebuild_document_classification(classification_for([1]), text, units)
    assert len(rebuilt.segments) == 1
    assert rebuilt.segments[0].text == "初步考虑IPF。"
    assert rebuilt.segments[0].metadata["section_title"] == TITLES[-1]


def test_section_can_contain_multiple_segments_or_a_multi_sentence_segment():
    text = case_text().replace("否认粉尘接触。", "否认粉尘接触。既往高血压。")
    units = build_source_units(text)
    separate = rebuild_document_classification(classification_for(range(1, 8)), text, units)
    grouped = rebuild_document_classification(classification_for([1, 3, 4, 5, 6, 7]), text, units)
    assert separate.segments[1].metadata == separate.segments[2].metadata
    assert grouped.segments[1].text == "否认粉尘接触。既往高血压。"


@pytest.mark.parametrize("text", [
    "一、基本信息与主诉\n患者女，77岁。",
    "前置临床信息。\n" + case_text(),
    case_text().replace(TITLES[1], TITLES[0]),
])
def test_malformed_section_layout_does_not_silently_omit_text(text):
    with pytest.raises(ValueError):
        build_source_units(text)


def test_classifier_passes_structure_separately_to_model():
    class CapturingLLM:
        def complete(self, messages, **kwargs):
            self.prompt = messages[-1].content
            return LLMResponse(content=classification_for(range(1, 7)).model_dump_json(), raw={})

    llm = CapturingLLM()
    classifier = DocumentClassifier(
        llm, "src/prompts/semantic_graphing/document_classification.md",
        temperature=0, max_tokens=1000,
    )
    result, _ = classifier.classify(case_text())
    assert TITLES[2] in llm.prompt
    assert "2024-06-11 当地检查" in llm.prompt
    assert len(result.segments) == 6
    assert all("【" not in segment.text for segment in result.segments)


def test_all_reorganized_cases_preserve_every_body_character():
    paths = list(Path("data/reorganized_cases").glob("*.txt"))
    assert paths
    for path in paths:
        text = path.read_text()
        units = build_source_units(text)
        rebuilt = rebuild_document_classification(
            classification_for(range(1, len(units) + 1)), text, units,
        )
        assert {unit.section_title for unit in units} <= set(TITLES)
        assert len(rebuilt.segments) == len(units)
        for segment in rebuilt.segments:
            assert segment.text == text[segment.start_char:segment.end_char]


def test_graph_unit_recognition_keeps_structure_and_original_offsets():
    text = case_text()
    classification = rebuild_document_classification(
        classification_for(range(1, 7)), text, build_source_units(text),
    )
    segment = classification.segments[2]

    class CapturingLLM:
        def complete(self, messages, **kwargs):
            self.prompt = messages[-1].content
            return LLMResponse(content=json.dumps({"graph_units": [{
                "text": segment.text,
                "source_type": "imaging_findings",
                "mdt_specialty": ["thoracic_radiology"],
                "primary_frame": "standalone_examination",
                "primary_frame_rationale": "影像检查结果。",
                "rationale": "test",
            }]}), raw={})

    llm = CapturingLLM()
    extractor = SegmentGraphUnitExtractor(
        llm, "src/prompts/semantic_graphing/graph_unit_extraction.md",
        temperature=0, max_tokens=1000,
    )
    result, _ = extractor.extract(segment)
    unit = result.graph_units[0]
    assert TITLES[2] in llm.prompt
    assert "2024-06-11 当地检查" in llm.prompt
    assert unit.metadata == segment.metadata
    assert unit.text == text[unit.start_char:unit.end_char] == "CT示双肺间质增粗。"
    assert unit.temporal_anchor is None


def test_catalog_prefers_reorganized_cases_and_keeps_legacy_fallback(tmp_path):
    legacy = tmp_path / "data/raw_cases"
    legacy.mkdir(parents=True)
    (legacy / "old.txt").write_text("旧病例。")
    assert RunCatalog(tmp_path).case_text("old") == "旧病例。"
    current = tmp_path / "data/reorganized_cases"
    current.mkdir()
    (current / "new.txt").write_text(case_text())
    catalog = RunCatalog(tmp_path)
    assert [case["id"] for case in catalog.list_cases()] == ["new"]
    assert catalog.case_text("new") == case_text()


def test_cli_defaults_to_reorganized_cases(tmp_path, monkeypatch):
    from scripts.run.run_semantic_graph_agent import input_files

    cases = tmp_path / "data/reorganized_cases"
    cases.mkdir(parents=True)
    (cases / "case.txt").write_text(case_text())
    monkeypatch.chdir(tmp_path)
    assert [path.name for path in input_files()] == ["case.txt"]
