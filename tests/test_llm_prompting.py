import json

import pytest

from src.agents.common.prompt_contract import specialty_output_contract
from src.agents.semantic_graphing.clinical_proposition_extractor import (
    ExtractedGraphUnitClinicalPropositions,
)
from src.agents.pulmonology.models import EvidencePointer as PulmonologyPointer
from src.agents.pulmonology.models import InitialPulmonaryAssessment
from src.agents.pulmonology.models import SpecialistQuestion as PulmonologyQuestion
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
        relevance="相关",
        application="用于当前判断",
        title="程序标题",
        quote="程序回填的长指南原文",
    )

    value = json.loads(prompt_json({"evidence": pulmonary, "guideline": guideline}))

    assert value["evidence"] == {"evidence_ids": ["unit_ev_001"]}
    assert set(value["guideline"]) == {"chunk_id", "relevance", "application"}


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


def test_strict_response_schema_requires_every_property_and_has_atomic_pointers():
    response_format = json_schema_response_format(
        InitialPulmonaryAssessment, "initial_pulmonary_assessment"
    )
    schema = response_format["json_schema"]["schema"]
    pointer = schema["$defs"]["EvidencePointer"]["properties"]["evidence_ids"]

    assert response_format["json_schema"]["strict"] is True
    assert schema["required"] == list(schema["properties"])
    assert "default" not in schema["properties"]["limitations"]
    assert pointer["minItems"] == pointer["maxItems"] == 1


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
