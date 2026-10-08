import json
import ssl
import urllib.error
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, model_validator

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
from src.llm.prompting import build_shared_prompt_view, llm_value, prompt_json, prompt_schema_json, shared_prompt_json
from src.llm.base import LLMResponse
from src.llm.structured import (
    StructuredGenerationError,
    StructuredLLMGenerator,
    json_schema_response_format,
)


def test_shared_prompt_preserves_all_values_and_omits_unused_catalog_entries():
    quote = "原始临床证据，包括确定度、否定和时间。" * 12
    block = {"quote": quote, "status": "not_assessable", "evidence_id": "E1"}
    original = {"case_id": "100-IPF", "assessment": [block, block], "evidence": block}
    view = build_shared_prompt_view(llm_value(original))
    used = set()

    def expand(value):
        if isinstance(value, dict):
            if set(value) == {"shared_value_ref"}:
                reference = value["shared_value_ref"]
                used.add(reference)
                return expand(view["shared_values"][reference])
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    assert expand(view["input"]) == json.loads(prompt_json(original))
    assert used == set(view["shared_values"])
    assert len(shared_prompt_json(original)) < len(prompt_json(original))
    assert shared_prompt_json({"quote": "无重复。"}) == prompt_json({"quote": "无重复。"})
    # Repeated short evidence quotes are now eligible, even within different objects.
    short_quote = "医学依据及其成立条件。" * 12
    assert json.dumps(build_shared_prompt_view([{"a": short_quote}, {"b": short_quote}]),
                      ensure_ascii=False).count(short_quote) == 1


def test_semantic_retry_repairs_only_invalid_fields_and_preserves_other_sections():
    class Ledger(BaseModel):
        claim_groups: list[dict]
        question_routes: list[dict]
        evidence_need_groups: list[dict]

    original = {
        "claim_groups": [{"disposition": "conflict", "conflict_nature": "decision_relevant_discordance"}],
        "question_routes": [{"source_refs": ["S003", "S004"]}],
        "evidence_need_groups": [{"source_refs": ["S013"]}],
    }
    patch = {"edits": [
        {"op": "replace", "path": "/claim_groups/0/disposition", "value": "boundary"},
        {"op": "replace", "path": "/claim_groups/0/conflict_nature", "value": None},
    ]}

    class FakeLLM:
        def __init__(self):
            self.calls = []

        def complete(self, messages, *, temperature, max_tokens, response_format=None):
            self.calls.append((messages, response_format))
            value = original if len(self.calls) == 1 else patch
            return LLMResponse(
                content=json.dumps(value),
                raw={"choices": [{"finish_reason": "stop"}]},
            )

    def validate(ledger):
        if ledger.claim_groups[0]["disposition"] == "conflict":
            raise ValueError("boundary assessments cannot be a conflict")
        return ledger

    llm = FakeLLM()
    generator = StructuredLLMGenerator(
        llm, temperature=0, max_tokens=24000, max_attempts=2,
        response_format_mode="json_schema",
    )
    result, trace = generator.generate(
        schema_model=Ledger,
        schema_name="ledger",
        system_prompt="system",
        user_prompt="user",
        extra_validation=validate,
        repair_on_validation_error=True,
    )

    assert result.claim_groups[0] == {"disposition": "boundary", "conflict_nature": None}
    assert result.question_routes == original["question_routes"]
    assert result.evidence_need_groups == original["evidence_need_groups"]
    assert trace["attempts"][0]["validated"] is False
    assert trace["attempts"][1]["validated"] is True
    assert llm.calls[0][1]["type"] == "json_schema"
    assert llm.calls[1][1] == {"type": "json_object"}
    assert "edits" in llm.calls[1][0][-1].content


def test_semantic_retry_cannot_replace_an_entire_section():
    from src.llm.structured import _apply_repair_edits

    with pytest.raises(ValueError, match="edits array"):
        _apply_repair_edits(
            {"question_routes": [{"source_refs": ["S003"]}]},
            {"claim_groups": [], "question_routes": [], "evidence_need_groups": []},
        )
    with pytest.raises(ValueError, match="scalar field"):
        _apply_repair_edits(
            {"question_routes": [{"source_refs": ["S003"]}]},
            {"edits": [{"op": "replace", "path": "/question_routes", "value": []}]},
        )


def test_object_array_repair_explains_append_and_missing_indexes():
    from src.llm.structured import _apply_repair_edits

    original = {"branches": [{"result": "existing"}]}
    for patch in (
        {"edits": [{"op": "replace", "path": "/branches", "value": [{"result": "new"}]}]},
        {"edits": [{"op": "replace", "path": "/branches/1/result", "value": "new"}]},
    ):
        with pytest.raises(ValueError) as error:
            _apply_repair_edits(original, patch)
        assert "append" in str(error.value)
    assert "array has 1 items" in str(error.value)
    repaired = _apply_repair_edits(original, {
        "edits": [{"op": "append", "path": "/branches", "value": {"result": "new"}}],
    })
    assert [item["result"] for item in repaired["branches"]] == ["existing", "new"]
    assert original == {"branches": [{"result": "existing"}]}


def test_repair_uses_candidate_that_produced_schema_error():
    class Ledger(BaseModel):
        issues: list[str]
        outcome: str

        @model_validator(mode="after")
        def unique_issues(self):
            if len(self.issues) != len(set(self.issues)):
                raise ValueError("Duplicate issue_id")
            return self

    responses = iter([
        {"issues": ["I3"], "outcome": "bounded"},
        {"edits": [{"op": "append", "path": "/issues", "value": "I3"}]},
        {"edits": [{"op": "remove_indices", "path": "/issues", "value": [1]}]},
        {"edits": [{"op": "remove_indices", "path": "/issues", "value": [0]}]},
    ])
    prompts = []

    class FakeLLM:
        def complete(self, messages, **kwargs):
            prompts.append(messages)
            return LLMResponse(content=json.dumps(next(responses)), raw={})

    def validate(ledger):
        if ledger.outcome == "bounded" and ledger.issues:
            raise ValueError("Closed issue I3 is still present in issues")
        return ledger

    result, trace = StructuredLLMGenerator(
        FakeLLM(), temperature=0, max_tokens=1000, max_attempts=4,
    ).generate(
        schema_model=Ledger, schema_name="ledger", system_prompt="system", user_prompt="user",
        extra_validation=validate, repair_on_validation_error=True,
    )

    assert result.issues == []
    assert result.outcome == "bounded"
    assert json.loads(prompts[2][-2].content)["issues"] == ["I3", "I3"]
    assert json.loads(prompts[3][-2].content)["issues"] == ["I3"]
    assert "Duplicate issue_id" in trace["attempts"][1]["validation_error"]
    assert "Closed issue" in trace["attempts"][2]["validation_error"]
    assert trace["attempts"][3]["validated"]


def test_invalid_repair_preserves_result_error_and_current_candidate():
    from src.agents.common.team_synthesis import KeyEvidenceNeed

    class Result(BaseModel):
        evidence_needs: list[KeyEvidenceNeed]

    original = {"evidence_needs": [{
        "need_id": "N1", "problem_id": "P1", "information": "recorded finding",
        "source_refs": ["S1"], "status": "available", "available_information": "partial record",
        "missing_information": "detail absent", "action": "retain_boundary",
        "feasibility_and_burden": "no new request", "if_unavailable": "retain boundary",
    }]}
    responses = iter([
        original,
        {"edits": [{"op": "replace", "path": "/evidence_needs/0.status", "value": "partially_available"}]},
        {"edits": []},
        {"edits": [{"op": "replace", "path": "/evidence_needs/0/status", "value": "partially_available"}]},
    ])
    prompts = []

    class FakeLLM:
        def complete(self, messages, **kwargs):
            prompts.append(messages)
            return LLMResponse(content=json.dumps(next(responses)), raw={})

    result, trace = StructuredLLMGenerator(
        FakeLLM(), temperature=0, max_tokens=1000, max_attempts=4,
    ).generate(
        schema_model=Result, schema_name="result", system_prompt="system", user_prompt="user",
        repair_on_validation_error=True,
    )

    assert result.evidence_needs[0].status == "partially_available"
    assert result.evidence_needs[0].missing_information == "detail absent"
    for messages in prompts[2:]:
        assert "Available evidence cannot have missing information" in messages[-1].content
        assert "/evidence_needs/0" in messages[-1].content
        assert json.loads(messages[-2].content) == original
    assert "Repair edit path does not exist" in prompts[2][-1].content
    assert "Repair response must contain at least one edit" in prompts[3][-1].content
    assert trace["attempts"][-1]["validated"]


def test_semantic_retry_can_replace_atomic_claim_selection():
    from src.llm.structured import _apply_repair_edits

    original = {"assessment_boundaries": [{"atomic_claim_ids": ["T002-A001"]}]}
    repaired = _apply_repair_edits(original, {"edits": [{
        "op": "replace",
        "path": "/assessment_boundaries/0/atomic_claim_ids",
        "value": ["T002-A002"],
    }]})

    assert repaired["assessment_boundaries"][0]["atomic_claim_ids"] == ["T002-A002"]
    assert original["assessment_boundaries"][0]["atomic_claim_ids"] == ["T002-A001"]


def test_semantic_retry_can_clear_nullable_content_when_change_is_maintain():
    from src.llm.structured import _apply_repair_edits

    original = {"proposals": [{"change_type": "qualify", "proposed_content": {"statement": "旧判断"}}]}
    repaired = _apply_repair_edits(original, {"edits": [
        {"op": "replace", "path": "/proposals/0/change_type", "value": "maintain"},
        {"op": "replace", "path": "/proposals/0/proposed_content", "value": None},
    ]})

    assert repaired["proposals"][0] == {"change_type": "maintain", "proposed_content": None}
    assert original["proposals"][0]["proposed_content"] is not None


def test_semantic_retry_can_append_a_missing_route_without_changing_existing_routes():
    from src.llm.structured import _apply_repair_edits

    base = {"question_routes": [{"source_refs": ["S003"]}]}
    repaired = _apply_repair_edits(
        base,
        {"edits": [{
            "op": "append",
            "path": "/question_routes",
            "value": {"source_refs": ["S004"]},
        }]},
    )

    assert base["question_routes"] == [{"source_refs": ["S003"]}]
    assert repaired["question_routes"] == [
        {"source_refs": ["S003"]},
        {"source_refs": ["S004"]},
    ]


def test_semantic_retry_removes_duplicate_routes_without_losing_unique_questions():
    class Ledger(BaseModel):
        question_routes: list[dict]

    original = {"question_routes": [
        {"source_refs": ["S003"], "route": "mixed"},
        {"source_refs": ["S003"], "route": "evidence_need"},
        {"source_refs": ["S024"], "route": "question"},
        {"source_refs": ["S003"], "route": "evidence_need"},
        {"source_refs": ["S024"], "route": "evidence_need"},
    ]}
    patch = {"edits": [{
        "op": "remove_indices", "path": "/question_routes", "value": [1, 3, 4],
    }]}

    class FakeLLM:
        def __init__(self):
            self.calls = 0
            self.messages = []

        def complete(self, messages, *, temperature, max_tokens, response_format=None):
            self.calls += 1
            self.messages.append(messages)
            value = (
                original if self.calls == 1 else
                {"edits": [{"op": "replace", "path": "/question_routes/1/route", "value": "evidence_need"}]}
                if self.calls == 2 else patch
            )
            return LLMResponse(
                content=json.dumps(value),
                raw={"choices": [{"finish_reason": "stop"}]},
            )

    def validate(ledger):
        refs = [ref for route in ledger.question_routes for ref in route["source_refs"]]
        if len(refs) != len(set(refs)):
            raise ValueError("Every in-scope question must be classified exactly once; duplicates")
        return ledger

    llm = FakeLLM()
    result, trace = StructuredLLMGenerator(
        llm, temperature=0, max_tokens=24000, max_attempts=3,
    ).generate(
        schema_model=Ledger,
        schema_name="ledger",
        system_prompt="system",
        user_prompt="user",
        extra_validation=validate,
        repair_on_validation_error=True,
    )

    assert [route["source_refs"] for route in result.question_routes] == [
        ["S003"], ["S024"],
    ]
    assert len(original["question_routes"]) == 5
    assert trace["attempts"][1]["validated"] is False
    assert trace["attempts"][2]["validated"] is True
    assert "上一轮 edits 未消除错误" in llm.messages[2][-1].content


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


def test_ssl_eof_retries_the_same_structured_request():
    class Answer(BaseModel):
        value: str

    class FlakyLLM:
        calls = 0

        def complete(self, messages, *, temperature, max_tokens, response_format=None):
            self.calls += 1
            if self.calls == 1:
                cause = urllib.error.URLError(ssl.SSLEOFError("unexpected EOF"))
                raise RuntimeError(f"APIYI request failed: {cause}") from cause
            return LLMResponse(
                content='{"value":"ok"}',
                raw={"choices": [{"finish_reason": "stop"}]},
            )

    llm = FlakyLLM()
    result, trace = StructuredLLMGenerator(
        llm, temperature=0, max_tokens=100, max_attempts=2,
    ).generate(
        schema_model=Answer, schema_name="answer", system_prompt="system", user_prompt="user",
    )

    assert result.value == "ok"
    assert llm.calls == len(trace["attempts"]) == 2


def test_schema_response_name_is_bounded_without_changing_schema():
    from pydantic import BaseModel
    class Result(BaseModel):
        value: str
    long_name = 'thoracic_radiology_discussion_' + 'R01-issue-pathology-' * 3
    result = json_schema_response_format(Result, long_name)['json_schema']
    assert len(result['name']) <= 64
    assert result['name'] == long_name[:64]
    assert result['schema'] == json_schema_response_format(Result, 'short')['json_schema']['schema']
    assert json_schema_response_format(Result, 'short')['json_schema']['name'] == 'short'
