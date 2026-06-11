from src.agents.semantic_graphing.agent import ClassificationRunResult
from src.reporting.html_report import render_report
from src.schemas.semantic_graphing import (
    ClassifiedSegment,
    ClinicalModifier,
    ClinicalProposition,
    DiscourseUnitType,
    DocumentClassification,
    DocumentClinicalPropositions,
    DocumentGraphUnits,
    DocumentPrimaryFrames,
    EvidenceSpan,
    GraphUnit,
    GraphUnitClinicalPropositions,
    GraphUnitPrimaryFrame,
    MdtSpecialty,
    ModifierType,
    PrimaryFrame,
    PropositionType,
    SegmentClinicalPropositions,
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
    clinical_propositions = DocumentClinicalPropositions(
        segments=[
            SegmentClinicalPropositions(
                segment_id="seg_001",
                units=[
                    GraphUnitClinicalPropositions(
                        graph_unit_id="seg_001_gu_001",
                        primary_frame=PrimaryFrame.ENCOUNTER,
                        propositions=[
                            ClinicalProposition(
                                proposition_id="prop_001",
                                proposition_type=PropositionType.SYMPTOM,
                                concept_text="测试",
                                source_span=EvidenceSpan(
                                    text="测试",
                                    start_char=0,
                                    end_char=2,
                                ),
                                modifiers=[
                                    ClinicalModifier(
                                        modifier_id="mod_001",
                                        modifier_type=ModifierType.QUALITY,
                                        value_text="原文",
                                        source_span=EvidenceSpan(
                                            text="原文",
                                            start_char=2,
                                            end_char=4,
                                        ),
                                    )
                                ],
                                rationale="test",
                            )
                        ],
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
        clinical_propositions=clinical_propositions,
    )
    html = output_path.read_text(encoding="utf-8")

    assert "诊疗接触事件" in html
    assert "围绕一次接触展开。" in html
    assert "复核事件核边界。" in html
    assert "Clinical propositions" in html
    assert "quality" in html
    EvidenceSpan,
    GraphUnitClinicalPropositions,
    ModifierType,
    PropositionType,
    SegmentClinicalPropositions,
