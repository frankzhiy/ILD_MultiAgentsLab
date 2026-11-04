"""All four specialties select evidence in their formal opinion request."""

import json
from copy import deepcopy

import pytest

from src.agents.common.evidence_projection import build_specialty_evidence_prompt_input
from src.agents.common.initial_output_validation import formal_evidence_schema_constraints
from src.agents.mdt_chair.agent import build_chair_prompt_bundle
from src.agents.mdt_chair.prompt_projection import build_chair_prompt_view
from src.agents.pathology.agent import PathologyAgent
from src.agents.pulmonology.agent import PulmonologyAgent
from src.agents.rheumatology.agent import RheumatologyAgent
from src.agents.thoracic_radiology.agent import ThoracicRadiologyAgent
from src.agents.thoracic_radiology.evidence_projection import build_radiology_working_input
from src.llm.base import LLMResponse
from src.llm.prompting import llm_value
from src.llm.structured import StructuredGenerationError
from synthetic_case import case_input
from test_initial_output import assessment, output_payload


AGENTS = {
    "pulmonology": PulmonologyAgent,
    "rheumatology": RheumatologyAgent,
    "thoracic_radiology": ThoracicRadiologyAgent,
    "pathology": PathologyAgent,
}


class FormalResponse:
    def __init__(self, payload, strict):
        self.payload = payload
        self.supports_json_schema = strict
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        assert len(self.calls) == 1, "Unexpected additional evidence request"
        return LLMResponse(content=json.dumps(self.payload, ensure_ascii=False), raw={})


@pytest.mark.parametrize("specialty", AGENTS)
@pytest.mark.parametrize("strict", [True, False])
def test_formal_opinion_selects_evidence_once_and_preserves_claim_links(monkeypatch, specialty, strict):
    case = case_input(specialty)
    eligible = {block.evidence_id for segment in case.segments for unit in segment.units
                if unit.may_support_diagnostic_claim
                for block in unit.clinical_propositions.evidence_blocks}
    if specialty == "thoracic_radiology":
        eligible = {evidence_id for unit in build_radiology_working_input(case).evidence_units
                    for statement in unit.statements if statement.thoracic_imaging_eligible
                    for evidence_id in statement.evidence_ids}
    evidence_id = sorted(eligible)[0]
    payload = output_payload()
    payload["specialty_assessments"]["evidence_gaps"] = []
    item = payload["specialty_assessments"]["assessments"][0]
    if specialty == "rheumatology":
        item["assessment_type"] = "rheumatic_disease"
        payload["specialty_assessments"]["assessments"].append(assessment("assessment_002", "ild_attribution"))
    for item in payload["specialty_assessments"]["assessments"]:
        item["claims"] = [
            {"statement": "现有资料支持有限的专业判断。", "evidence_relations": [
                {"evidence_ids": [evidence_id], "direction": "supports", "function": "foundational"},
            ]},
            {"statement": "缺少纵向检查，无法评价进展。", "evidence_relations": []},
        ]
    original = deepcopy(payload)
    llm = FormalResponse(payload, strict)
    events = []
    agent = AGENTS[specialty].from_config(
        f"configs/agents/{specialty}/agent.yaml", llm, enable_guidelines=False,
        event_callback=lambda name, data: events.append((name, data)),
    )
    monkeypatch.setattr(agent, "initial_assessment", lambda case: ({}, {"stages": []}))
    consultation = agent.initial_consult(case)
    assert payload == original
    assert len(llm.calls) == 1
    assert [event for event, _ in events].count("llm_attempt_completed") == 1
    assert [stage["stage"] for stage in consultation.trace["stages"]] == ["initial_reasoning_output"]
    assert "evidence_assignment" not in consultation.trace["stages"][0]
    prompt = llm.calls[0][0][-1].content
    assert "不另设证据标注阶段" in prompt
    assert "本阶段不选择病例证据" not in prompt
    if strict:
        schema = llm.calls[0][1]["response_format"]["json_schema"]["schema"]
        assert "evidence_relations" in schema["$defs"]["SpecialtyAtomicClaim"]["required"]
        choices = schema['$defs']['SpecialtyAssessment']['anyOf']
        assert choices[0]['properties']['assessability']['enum'] == ['assessable', 'partially_assessable']
        assert choices[1]['properties']['assessability']['enum'] == ['not_assessable']
        assert choices[1]['properties']['confidence']['enum'] == ['unknown']
    result = consultation.formal_output
    for item in result.specialty_assessments.assessments:
        claim = item.claims[0]
        relation = claim.evidence_relations[0]
        assert relation.target_claim_id == claim.claim_id == f"{item.assessment_id}_c001"
        assert relation.quote and relation.graph_unit_id and relation.segment_id
        assert item.evidence.evidence_relations[0] == relation
        assert item.conditions.evidence_scope.evidence_ids == [evidence_id]
        assert item.claims[1].evidence_relations == []
    outputs = {name: output_payload() for name in AGENTS}
    outputs[specialty] = result.model_dump(mode="json")
    bundle = build_chair_prompt_bundle("synthetic-case", outputs)
    view = build_chair_prompt_view(bundle)
    projected_specialty = next(item for item in view["chair_input"]["specialties"]
                               if item["specialty"] == specialty)
    projected = projected_specialty["specialty_assessments"][0]["claims"][0]
    citation = projected["evidence_relations"][0]
    assert projected["claim_id"] == result.specialty_assessments.assessments[0].claims[0].claim_id
    assert bundle.evidence_registry[citation["evidence_ref"]].quote
    assert citation["direction"] == "supports"
    assert "quote" not in citation
    assert view["case_evidence"]["references"][citation["evidence_ref"]]


def test_formal_json_object_cannot_silently_omit_inline_evidence(monkeypatch):
    payload = output_payload()
    payload["specialty_assessments"]["assessments"][0]["claims"] = [{"statement": "有限判断。"}]
    agent = PulmonologyAgent.from_config(
        "configs/agents/pulmonology/agent.yaml", FormalResponse(payload, False), enable_guidelines=False,
    )
    agent.generator.max_attempts = 1
    monkeypatch.setattr(agent, "initial_assessment", lambda case: ({}, {"stages": []}))
    with pytest.raises(StructuredGenerationError, match="explicitly return evidence_relations"):
        agent.initial_consult(case_input())


def test_pathology_without_material_generates_boundary_with_no_evidence_request(monkeypatch):
    from pydantic import BaseModel
    from src.agents.pathology.models import SourceAssessment

    class InternalState(BaseModel):
        source_assessment: SourceAssessment
    payload = output_payload()
    payload["specialty_assessments"]["assessability"] = "not_assessable"
    payload["specialty_assessments"]["evidence_gaps"] = []
    item = assessment("assessment_001", "material_evaluability", "not_assessable")
    item["claims"] = [{"statement": "病理材料未提供，无法评价组织学模式。", "evidence_relations": []}]
    payload["specialty_assessments"]["assessments"] = [item]
    llm = FormalResponse(payload, True)
    agent = PathologyAgent.from_config("configs/agents/pathology/agent.yaml", llm, enable_guidelines=False)
    state = InternalState(source_assessment=SourceAssessment(
        assessment="病理材料未提供。", confidence="unknown", reasoning_summary="无法评价组织学模式。",
        material_status="no_pathology_material", review_basis="no_material",
    ))
    monkeypatch.setattr(agent, "initial_assessment", lambda case: (state, {"stages": []}))
    result = agent.initial_consult(case_input("pathology"))
    assert len(llm.calls) == 1
    properties = llm.calls[0][1]['response_format']['json_schema']['schema']['$defs']['_MaterialPlanAssessment']['properties']
    assert properties['assessability']['enum'] == ['not_assessable']
    assert properties['confidence']['enum'] == ['unknown']
    assert result.formal_output.specialty_assessments.assessability == "not_assessable"
    assert result.formal_output.specialty_assessments.assessments[0].evidence.evidence_relations == []


def test_available_evidence_directory_is_complete_and_constraints_apply_to_claims():
    from src.agents.common.initial_output import SpecialtyInitialOutput
    from src.llm.structured import json_schema_response_format
    case = case_input()
    view = llm_value(build_specialty_evidence_prompt_input(case))
    evidence_ids = {block["evidence_id"] for key in ("diagnostic_evidence_units", "context_only_evidence_units")
                    for unit in view[key] for block in unit["evidence_blocks"]}
    assert evidence_ids == {block.evidence_id for segment in case.segments for unit in segment.units
                            for block in unit.clinical_propositions.evidence_blocks}
    schema = json_schema_response_format(
        SpecialtyInitialOutput, "specialty_initial", pointer_field_constraints=formal_evidence_schema_constraints(case),
    )["json_schema"]["schema"]
    alternatives = schema["$defs"]["SpecialtyAtomicClaim"]["properties"]["evidence_relations"]["items"]["anyOf"]
    diagnostic_ids = {block.evidence_id for segment in case.segments for unit in segment.units
                      if unit.may_support_diagnostic_claim for block in unit.clinical_propositions.evidence_blocks}
    assert set(alternatives[0]["properties"]["evidence_ids"]["items"]["enum"]) == diagnostic_ids
    assert alternatives[1]["properties"]["direction"]["enum"] == ["neutral"]
    assert alternatives[1]["properties"]["function"]["enum"] == ["background"]


def test_claim_links_survive_state_versioning_and_supplement():
    from src.agents.common.decision_state import (
        JudgmentContent, active_judgments, apply_judgment_update,
        initialize_decision_state, project_active_specialty_outputs,
    )
    from src.agents.common.initial_output import EvidenceRelation
    from test_decision_state import outputs, proposal, update
    raw = outputs()
    item = raw["pulmonology"]["specialty_assessments"]["assessments"][0]
    item["claims"][0]["evidence_relations"] = item["evidence"]["evidence_relations"]
    state = initialize_decision_state("case-1", raw)
    current = active_judgments(state, "pulmonology")[0]
    content = JudgmentContent.from_assessment(current.assessment)
    content.claims[0].evidence_relations = [EvidenceRelation(
        evidence_ids=["ev_002"], direction="weakens", function="qualifying",
    )]
    changed = apply_judgment_update(state, update(proposal(state, "supplement", content)), round_number=1)
    active = active_judgments(changed, "pulmonology")[0].assessment
    assert active.claims[0].claim_id == current.assessment.claims[0].claim_id
    assert {eid for relation in active.evidence.evidence_relations for eid in relation.evidence_ids} == {"ev_001", "ev_002"}
    assert all(relation.target_claim_id == active.claims[0].claim_id for relation in active.evidence.evidence_relations)
    projected = project_active_specialty_outputs(changed)["pulmonology"]["specialty_assessments"]["assessments"][0]
    assert len(projected["claims"][0]["evidence_relations"]) == 2


@pytest.mark.parametrize('specialty', AGENTS)
def test_invalid_formal_field_is_patched_without_regenerating_the_opinion(monkeypatch, specialty):
    payload = output_payload()
    payload['specialty_assessments']['evidence_gaps'] = []
    items = payload['specialty_assessments']['assessments']
    items[0].update(status='not_assessable', assessability='not_assessable', direction='indeterminate', confidence='high')
    items[0]['claims'][0]['evidence_relations'] = []
    if specialty == 'rheumatology':
        items[0]['assessment_type'] = 'rheumatic_disease'
        items.append(assessment('assessment_002', 'ild_attribution'))
        items[1]['claims'][0]['evidence_relations'] = []
    original = deepcopy(items[0])

    class LLM:
        supports_json_schema = False
        def __init__(self):
            self.calls = []
        def complete(self, messages, **kwargs):
            self.calls.append(messages)
            result = payload if len(self.calls) == 1 else {'edits': [{
                'op': 'replace', 'path': '/specialty_assessments/assessments/0/confidence', 'value': 'unknown'}]}
            return LLMResponse(content=json.dumps(result, ensure_ascii=False), raw={})

    llm = LLM()
    agent = AGENTS[specialty].from_config(f'configs/agents/{specialty}/agent.yaml', llm, enable_guidelines=False)
    agent.generator.max_attempts = 2
    monkeypatch.setattr(agent, 'initial_assessment', lambda case: ({}, {'stages': []}))
    result = agent.initial_consult(case_input(specialty))
    assert len(llm.calls) == 2
    assert '只返回 edits JSON' in llm.calls[1][0].content
    item = result.formal_output.specialty_assessments.assessments[0]
    assert item.confidence == 'unknown'
    assert item.statement == original['statement']
    assert item.medical_basis == original['medical_basis']
    assert item.claims[0].statement == original['claims'][0]['statement']
