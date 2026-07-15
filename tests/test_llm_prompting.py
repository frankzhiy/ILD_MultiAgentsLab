import json

from src.agents.pulmonology.models import EvidencePointer as PulmonologyPointer
from src.agents.thoracic_radiology.models import EvidencePointer as RadiologyPointer
from src.guidelines.models import GuidelineEvidencePointer
from src.llm.prompting import prompt_json, prompt_schema_json


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
