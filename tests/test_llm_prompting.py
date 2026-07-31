import json
from types import SimpleNamespace

import pytest

from src.agents.common.initial_output import (
    EvidenceBundle,
    EvidenceRelation,
    SpecialtyInitialOutput,
)
from src.agents.common.prompt_contract import specialty_output_contract
from src.agents.common.validation import _split_evidence_pointers_by_unit
from src.agents.pathology.models import EvidencePointer as PathologyPointer
from src.agents.semantic_graphing.clinical_proposition_extractor import (
    ExtractedGraphUnitClinicalPropositions,
)
from src.agents.pulmonology.models import EvidencePointer as PulmonologyPointer
from src.agents.pulmonology.models import InitialPulmonaryAssessment
from src.agents.pulmonology.models import SpecialistQuestion as PulmonologyQuestion
from src.agents.rheumatology.models import EvidencePointer as RheumatologyPointer
from src.agents.rheumatology.models import SpecialistQuestion as RheumatologyQuestion
from src.agents.thoracic_radiology.models import EvidencePointer as RadiologyPointer
from src.agents.thoracic_radiology.models import SpecialistQuestion as RadiologyQuestion
from src.guidelines.models import GuidelineEvidencePointer
from src.llm.prompting import prompt_json, prompt_schema_json
from src.llm.structured import (
    StructuredGenerationError,
    StructuredLLMGenerator,
    json_schema_response_format,
)


def test_prompt_json_removes_program_filled_fields_recursively():
    pulmonary = PulmonologyPointer(evidence_ids=["unit_ev_001"])
    pulmonary.graph_unit_id = "unit"
    pulmonary.segment_id = "segment"
    pulmonary.node_ids = ["node"]
    pulmonary.quote = "原文"
    guideline = GuidelineEvidencePointer(
        chunk_id="guide:p001:c001",
        quote_unit_ids=["guide:p001:c001:q001"],
        relevance="相关",
        application="用于当前判断",
        title="程序标题",
    )

    value = json.loads(prompt_json({"evidence": pulmonary, "guideline": guideline}))

    assert value["evidence"] == {"evidence_ids": ["unit_ev_001"]}
    assert set(value["guideline"]) == {
        "chunk_id",
        "quote_unit_ids",
        "relevance",
        "application",
    }


def test_radiology_prompt_keeps_llm_owned_pointer_keys_only():
    pointer = RadiologyPointer(graph_unit_id="unit", proposition_ids=["prop_001"])
    pointer.evidence_ids = ["unit_ev_001"]
    pointer.quote = "原文"

    assert json.loads(prompt_json(pointer)) == {
        "graph_unit_id": "unit",
        "proposition_ids": ["prop_001"],
    }


def test_prompt_schema_is_compact_and_keeps_required_structure():
    schema = prompt_schema_json(PulmonologyPointer)

    assert "\n" not in schema
    assert '"evidence_ids"' in schema
    assert '"graph_unit_id"' not in schema
    assert '"title"' not in schema


def test_specialist_question_schemas_exclude_shared_context():
    for model in (PulmonologyQuestion, RheumatologyQuestion, RadiologyQuestion):
        schema = model.model_json_schema()
        assert schema["$defs"]["SpecialistTarget"]["enum"] == [
            "pulmonology",
            "thoracic_radiology",
            "pathology",
            "rheumatology",
        ]


def test_strict_response_schema_requires_every_property_and_allows_graph_unit_pointers():
    response_format = json_schema_response_format(
        InitialPulmonaryAssessment, "initial_pulmonary_assessment"
    )
    schema = response_format["json_schema"]["schema"]
    pointer = schema["$defs"]["EvidencePointer"]["properties"]["evidence_ids"]

    assert response_format["json_schema"]["strict"] is True
    assert schema["required"] == list(schema["properties"])
    assert "default" not in schema["properties"]["limitations"]
    assert pointer["minItems"] == 1
    assert "maxItems" not in pointer


@pytest.mark.parametrize(
    "pointer_type",
    [PulmonologyPointer, RheumatologyPointer, PathologyPointer],
)
def test_specialty_pointer_schema_allows_multiple_ids_within_one_graph(pointer_type):
    evidence_ids = pointer_type.model_json_schema()["properties"]["evidence_ids"]

    assert evidence_ids["minItems"] == 1
    assert "maxItems" not in evidence_ids
    assert "一个 Graph Unit" in evidence_ids["description"]


def test_pointer_normalizer_merges_pointers_from_the_same_graph_unit():
    unit = SimpleNamespace(graph_unit=SimpleNamespace(graph_unit_id="gu_001"))
    pointers = [
        PulmonologyPointer(evidence_ids=["ev_001"]),
        PulmonologyPointer(evidence_ids=["ev_002"]),
    ]

    _split_evidence_pointers_by_unit(
        pointers,
        PulmonologyPointer,
        {"ev_001": (unit, "证据一"), "ev_002": (unit, "证据二")},
    )

    assert len(pointers) == 1
    assert pointers[0].evidence_ids == ["ev_001", "ev_002"]


def test_pointer_normalizer_keeps_distinct_evidence_dimensions():
    unit = SimpleNamespace(graph_unit=SimpleNamespace(graph_unit_id="gu_001"))
    relations = [
        EvidenceRelation(
            evidence_ids=["ev_001"],
            direction="supports",
            function="foundational",
        ),
        EvidenceRelation(
            evidence_ids=["ev_002"],
            direction="supports",
            function="qualifying",
        ),
    ]

    _split_evidence_pointers_by_unit(
        relations,
        EvidenceRelation,
        {"ev_001": (unit, "证据一"), "ev_002": (unit, "证据二")},
    )

    assert len(relations) == 2


def test_pointer_normalizer_never_merges_relations_across_atomic_claims():
    unit = SimpleNamespace(graph_unit=SimpleNamespace(graph_unit_id="gu_001"))
    relations = [
        EvidenceRelation(
            evidence_ids=["ev_001"],
            target_claim_id="claim_001",
            direction="supports",
            function="discriminating",
        ),
        EvidenceRelation(
            evidence_ids=["ev_002"],
            target_claim_id="claim_002",
            direction="supports",
            function="discriminating",
        ),
        EvidenceRelation(
            evidence_ids=["ev_002"],
            target_claim_id="claim_001",
            direction="weakens",
            function="qualifying",
        ),
    ]

    _split_evidence_pointers_by_unit(
        relations,
        EvidenceRelation,
        {"ev_001": (unit, "证据一"), "ev_002": (unit, "证据二")},
    )

    assert len(relations) == 3
    assert [relation.target_claim_id for relation in relations] == [
        "claim_001",
        "claim_002",
        "claim_001",
    ]
    assert [relation.evidence_ids for relation in relations] == [
        ["ev_001"],
        ["ev_002"],
        ["ev_002"],
    ]


def test_specialty_output_schema_defers_evidence_and_requires_atomic_claims():
    schema = SpecialtyInitialOutput.model_json_schema()
    assessment_schema = schema["$defs"]["SpecialtyAssessment"]
    assessment = assessment_schema["properties"]
    claim = schema["$defs"]["SpecialtyAtomicClaim"]["properties"]
    evidence_schema = EvidenceBundle.model_json_schema()
    evidence = evidence_schema["properties"]
    relation = evidence_schema["$defs"]["EvidenceRelation"]["properties"]

    assert "claims" in assessment
    assert "claims" in assessment_schema["required"]
    assert "evidence" not in assessment
    assert set(claim) == {"statement"}
    assert set(evidence) == {"evidence_relations"}
    assert relation["direction"]["enum"] == ["supports", "weakens", "neutral"]
    assert relation["function"]["enum"] == [
        "foundational",
        "discriminating",
        "qualifying",
        "background",
    ]


def test_relation_schema_can_constrain_direction_function_and_locator_together():
    schema = json_schema_response_format(
        EvidenceBundle,
        "evidence_bundle",
        pointer_field_constraints={
            "evidence_relations": [
                {
                    "evidence_ids": {"ev_diagnostic"},
                    "direction": {"supports", "weakens", "neutral"},
                    "function": {"foundational", "discriminating", "qualifying"},
                },
                {
                    "evidence_ids": {"ev_context"},
                    "direction": {"neutral"},
                    "function": {"background"},
                },
            ]
        },
    )["json_schema"]["schema"]
    alternatives = schema["properties"]["evidence_relations"]["items"]["anyOf"]

    assert alternatives[0]["properties"]["evidence_ids"]["items"]["enum"] == [
        "ev_diagnostic"
    ]
    assert alternatives[1]["properties"]["direction"]["enum"] == ["neutral"]
    assert alternatives[1]["properties"]["function"]["enum"] == ["background"]


def test_strict_response_schema_has_no_ref_sibling_keywords():
    schema = json_schema_response_format(
        ExtractedGraphUnitClinicalPropositions,
        "graph_unit_clinical_propositions",
    )["json_schema"]["schema"]

    def ref_nodes(value):
        if isinstance(value, dict):
            if "$ref" in value:
                yield value
            for item in value.values():
                yield from ref_nodes(item)
        elif isinstance(value, list):
            for item in value:
                yield from ref_nodes(item)

    references = list(ref_nodes(schema))
    assert references
    assert all(set(reference) == {"$ref"} for reference in references)


def test_final_contract_distinguishes_partitioned_and_working_inputs():
    working = specialty_output_contract(
        pointer_style="evidence_id", initial_stage=True
    )
    partitioned = specialty_output_contract(
        pointer_style="evidence_id",
        initial_stage=True,
        partitioned_evidence=True,
    )

    assert "may_support_diagnostic_claim=true" in working
    assert "diagnostic_evidence_units" in partitioned
    assert "context_only_evidence_units" in partitioned
    assert partitioned.endswith("specialist_opinion_ids 必须为空列表。")


def test_final_contract_defers_case_evidence_for_formal_claim_draft():
    contract = specialty_output_contract(
        pointer_style="evidence_id",
        initial_stage=True,
        partitioned_evidence=True,
        defer_case_evidence=True,
    )

    assert "原子 claims" in contract
    assert "固定 claim × evidence 槽位" in contract
    assert "supporting_evidence" not in contract
    assert "evidence_relations" not in contract


def test_declared_json_schema_support_does_not_silently_downgrade():
    class RejectingLLM:
        def __init__(self):
            self.formats = []

        def complete(self, messages, *, temperature, max_tokens, response_format=None):
            self.formats.append(response_format)
            raise RuntimeError("provider rejected json_schema")

    llm = RejectingLLM()
    generator = StructuredLLMGenerator(
        llm,
        temperature=0,
        max_tokens=100,
        max_attempts=2,
        response_format_mode="json_schema",
    )

    with pytest.raises(StructuredGenerationError, match="provider rejected json_schema"):
        generator.generate(
            schema_model=PulmonologyPointer,
            schema_name="pointer",
            system_prompt="system",
            user_prompt="user",
        )

    assert len(llm.formats) == 1
    assert llm.formats[0]["type"] == "json_schema"
