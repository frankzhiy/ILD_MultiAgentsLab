import pytest
from pydantic import ValidationError

from src.agents.semantic_graphing.primary_frame_selector import (
    validate_primary_frame_selection,
)
from src.schemas.semantic_graphing import (
    GraphUnit,
    GraphUnitPrimaryFrame,
    MdtSpecialty,
    PrimaryFrame,
    SourceType,
    render_primary_frame_catalog,
)


def make_unit() -> GraphUnit:
    return GraphUnit(
        graph_unit_id="seg_001_gu_001",
        segment_id="seg_001",
        text="原文",
        source_type=SourceType.OTHER,
        mdt_specialty=[MdtSpecialty.OTHER],
        rationale="test",
    )


def test_primary_frame_selection_accepts_exactly_one_controlled_frame():
    selection = GraphUnitPrimaryFrame(
        graph_unit_id="seg_001_gu_001",
        primary_frame=PrimaryFrame.ENCOUNTER,
        rationale="test",
    )

    assert selection.primary_frame == PrimaryFrame.ENCOUNTER

    with pytest.raises(ValidationError):
        GraphUnitPrimaryFrame.model_validate(
            {
                "graph_unit_id": "seg_001_gu_001",
                "triggered_frames": ["encounter", "clinical_assessment"],
                "rationale": "old shape",
            }
        )


def test_primary_frame_selection_rejects_wrong_unit_id():
    selection = GraphUnitPrimaryFrame(
        graph_unit_id="seg_999_gu_001",
        primary_frame=PrimaryFrame.BACKGROUND_CONTEXT,
        rationale="test",
    )

    with pytest.raises(ValueError, match="does not match"):
        validate_primary_frame_selection(selection, make_unit())


def test_primary_frame_catalog_contains_only_container_level_frames():
    catalog = render_primary_frame_catalog()

    assert "`encounter`" in catalog
    assert "`standalone_examination`" in catalog
    assert "`clinical_assessment`" in catalog
    assert "`diagnosis`" not in catalog
    assert "`examination_report`" not in catalog
