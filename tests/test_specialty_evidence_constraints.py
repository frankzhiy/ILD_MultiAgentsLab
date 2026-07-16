from types import SimpleNamespace

import pytest

from src.agents.common.validation import (
    diagnostic_evidence_schema_constraints,
    validate_authorized_items,
)
from src.agents.rheumatology.models import InitialAutoimmuneAssessment
from src.agents.thoracic_radiology.models import InitialConsultFormulation
from src.llm.structured import json_schema_response_format


def test_dynamic_schema_limits_diagnostic_evidence_but_not_related_evidence():
    constraints = {
        "supporting_evidence": [{"evidence_ids": {"allowed_ev_001"}}],
        "conflicting_evidence": [{"evidence_ids": {"allowed_ev_001"}}],
        "related_evidence": [
            {"evidence_ids": {"allowed_ev_001", "context_ev_001"}}
        ],
    }
    schema = json_schema_response_format(
        InitialAutoimmuneAssessment,
        "initial_autoimmune_assessment",
        pointer_field_constraints=constraints,
    )["json_schema"]["schema"]
    properties = schema["$defs"]["SerologicFinding"]["properties"]

    for field_name in ("supporting_evidence", "conflicting_evidence"):
        evidence_ids = properties[field_name]["items"]["properties"]["evidence_ids"]
        assert evidence_ids["items"]["enum"] == ["allowed_ev_001"]
    related_ids = properties["related_evidence"]["items"]["properties"]["evidence_ids"]
    assert related_ids["items"]["enum"] == ["allowed_ev_001", "context_ev_001"]


def test_dynamic_schema_keeps_radiology_unit_and_proposition_pairs_together():
    constraints = {
        "supporting_evidence": [
            {"graph_unit_id": {"unit_1"}, "proposition_ids": {"prop_1", "prop_2"}},
            {"graph_unit_id": {"unit_2"}, "proposition_ids": {"prop_3"}},
        ],
        "conflicting_evidence": [],
    }
    schema = json_schema_response_format(
        InitialConsultFormulation,
        "initial_consult_formulation",
        pointer_field_constraints=constraints,
    )["json_schema"]["schema"]
    properties = schema["$defs"]["RadiologyTaskAssessment"]["properties"]
    choices = properties["supporting_evidence"]["items"]["anyOf"]

    assert [
        (
            choice["properties"]["graph_unit_id"]["enum"],
            choice["properties"]["proposition_ids"]["items"]["enum"],
        )
        for choice in choices
    ] == [(["unit_1"], ["prop_1", "prop_2"]), (["unit_2"], ["prop_3"])]
    assert properties["conflicting_evidence"]["maxItems"] == 0


def test_permission_fallback_reports_all_invalid_evidence(monkeypatch):
    def unit(unit_id, evidence_id, may_support):
        return SimpleNamespace(
            may_support_diagnostic_claim=may_support,
            evidence_role="owned" if may_support else "reference_only",
            graph_unit=SimpleNamespace(graph_unit_id=unit_id, segment_id="segment_1"),
            local_graph=SimpleNamespace(nodes=[]),
            clinical_propositions=SimpleNamespace(
                evidence_blocks=[SimpleNamespace(evidence_id=evidence_id)]
            ),
        )

    units = {
        "allowed_unit": unit("allowed_unit", "allowed_ev", True),
        "context_1": unit("context_1", "context_ev_1", False),
        "context_2": unit("context_2", "context_ev_2", False),
    }
    monkeypatch.setattr(
        "src.agents.common.validation.case_units",
        lambda case_input: units,
    )

    def pointer(unit_id, evidence_id):
        return SimpleNamespace(
            graph_unit_id=unit_id,
            segment_id="segment_1",
            node_ids=[],
            evidence_ids=[evidence_id],
        )

    items = [
        SimpleNamespace(
            supporting_evidence=[pointer("context_1", "context_ev_1")],
            conflicting_evidence=[],
            related_evidence=[],
            specialist_opinion_ids=[],
        ),
        SimpleNamespace(
            supporting_evidence=[],
            conflicting_evidence=[pointer("context_2", "context_ev_2")],
            related_evidence=[],
            specialist_opinion_ids=[],
        ),
    ]

    with pytest.raises(ValueError) as exc_info:
        validate_authorized_items(
            items,
            object(),
            {},
            lambda unit, pointer: f"invalid: {pointer.evidence_ids[0]}",
        )

    message = str(exc_info.value)
    assert "invalid: context_ev_1" in message
    assert "invalid: context_ev_2" in message


def test_schema_constraints_include_only_base_diagnostic_evidence(monkeypatch):
    units = {
        "diagnostic": SimpleNamespace(
            may_support_diagnostic_claim=True,
            clinical_propositions=SimpleNamespace(
                evidence_blocks=[SimpleNamespace(evidence_id="diagnostic_ev")]
            ),
        ),
        "context": SimpleNamespace(
            may_support_diagnostic_claim=False,
            clinical_propositions=SimpleNamespace(
                evidence_blocks=[SimpleNamespace(evidence_id="context_ev")]
            ),
        ),
    }
    monkeypatch.setattr(
        "src.agents.common.validation.case_units",
        lambda case_input: units,
    )

    constraints = diagnostic_evidence_schema_constraints(object())

    assert constraints["supporting_evidence"] == [
        {"evidence_ids": {"diagnostic_ev"}}
    ]
    assert constraints["conflicting_evidence"] == [
        {"evidence_ids": {"diagnostic_ev"}}
    ]
    assert constraints["related_evidence"] == [
        {"evidence_ids": {"context_ev", "diagnostic_ev"}}
    ]
