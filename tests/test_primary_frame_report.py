from src.agents.semantic_graphing.agent import ClassificationRunResult
from src.reporting.html_report import render_report
from src.schemas.semantic_graphing import (
    ClassifiedSegment,
    DiscourseUnitType,
    DocumentClassification,
    DocumentGraphUnits,
    DocumentPrimaryFrames,
    GraphUnit,
    GraphUnitPrimaryFrame,
    MdtSpecialty,
    PrimaryFrame,
    SegmentGraphUnits,
    SegmentPrimaryFrames,
    SourceType,
)


def test_report_renders_single_primary_frame_and_boundary_warning(tmp_path):
    text = "测试原文"
    classification = DocumentClassification(
        segments=[
            ClassifiedSegment(
                segment_id="seg_001",
                text=text,
                unit_type=DiscourseUnitType.OTHER,
                clinical_frame="test",
                start_char=0,
                end_char=len(text),
                confidence=1,
                rationale="test",
            )
        ]
    )
    graph_units = DocumentGraphUnits(
        segments=[
            SegmentGraphUnits(
                segment_id="seg_001",
                graph_units=[
                    GraphUnit(
                        graph_unit_id="seg_001_gu_001",
                        segment_id="seg_001",
                        text=text,
                        source_type=SourceType.OTHER,
                        mdt_specialty=[MdtSpecialty.OTHER],
                        rationale="test",
                    )
                ],
            )
        ]
    )
    primary_frames = DocumentPrimaryFrames(
        segments=[
            SegmentPrimaryFrames(
                segment_id="seg_001",
                units=[
                    GraphUnitPrimaryFrame(
                        graph_unit_id="seg_001_gu_001",
                        primary_frame=PrimaryFrame.ENCOUNTER,
                        rationale="围绕一次接触展开。",
                        boundary_warning="复核事件核边界。",
                    )
                ],
            )
        ]
    )

    output_path = render_report(
        ClassificationRunResult(case_id="case", classification=classification, trace={}),
        graph_units,
        source_filename="case.txt",
        raw_text=text,
        timing={},
        output_path=tmp_path / "report.html",
        primary_frames=primary_frames,
    )
    html = output_path.read_text(encoding="utf-8")

    assert "诊疗接触事件" in html
    assert "围绕一次接触展开。" in html
    assert "复核事件核边界。" in html
