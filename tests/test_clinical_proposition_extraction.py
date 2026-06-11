import json

import pytest

from src.agents.semantic_graphing.clinical_proposition_extractor import (
    ClinicalPropositionExtractor,
    UnlocatedGraphUnitClinicalPropositions,
    normalize_clinical_proposition_spans,
    split_dense_unit_text,
    validate_clinical_propositions,
)
from src.llm.base import LLMResponse
from src.schemas.semantic_graphing import (
    ClinicalModifier,
    ClinicalProposition,
    EvidenceSpan,
    GraphUnit,
    GraphUnitClinicalPropositions,
    GraphUnitPrimaryFrame,
    MdtSpecialty,
    ModifierType,
    PrimaryFrame,
    PropositionType,
    SourceType,
)


class CapturingLLM:
    def __init__(self, response: dict):
        self.response = response
        self.response_format = None
        self.messages = None

    def complete(self, messages, *, temperature, max_tokens, response_format=None):
        self.messages = messages
        self.response_format = response_format
        return LLMResponse(content=json.dumps(self.response, ensure_ascii=False), raw={})


class SequencedLLM:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.messages_by_attempt = []

    def complete(self, messages, *, temperature, max_tokens, response_format=None):
        self.messages_by_attempt.append(messages)
        response = self.responses[len(self.messages_by_attempt) - 1]
        return LLMResponse(content=json.dumps(response, ensure_ascii=False), raw={})


def span(text: str, value: str, start: int = 0) -> EvidenceSpan:
    offset = text.index(value, start)
    return EvidenceSpan(text=value, start_char=offset, end_char=offset + len(value))


def make_unit(text: str) -> GraphUnit:
    return GraphUnit(
        graph_unit_id="seg_001_gu_001",
        segment_id="seg_001",
        text=text,
        source_type=SourceType.EXPOSURE_HISTORY,
        mdt_specialty=[MdtSpecialty.OCCUPATIONAL_ENVIRONMENTAL],
        rationale="test",
    )


def make_frame() -> GraphUnitPrimaryFrame:
    return GraphUnitPrimaryFrame(
        graph_unit_id="seg_001_gu_001",
        primary_frame=PrimaryFrame.BACKGROUND_CONTEXT,
        rationale="test",
    )


def test_proposition_modifiers_are_owned_by_the_correct_proposition():
    text = "从事面粉加工工作30余年。吸烟30余年，约20支/日。"
    second_duration_start = text.index("30余年", text.index("吸烟"))
    result = GraphUnitClinicalPropositions(
        graph_unit_id="seg_001_gu_001",
        primary_frame=PrimaryFrame.BACKGROUND_CONTEXT,
        propositions=[
            ClinicalProposition(
                proposition_id="prop_001",
                proposition_type=PropositionType.EXPOSURE,
                concept_text="面粉加工工作",
                source_span=span(text, "从事面粉加工工作30余年"),
                modifiers=[
                    ClinicalModifier(
                        modifier_id="mod_001",
                        modifier_type=ModifierType.DURATION,
                        value_text="30余年",
                        source_span=span(text, "30余年"),
                    )
                ],
                rationale="test",
            ),
            ClinicalProposition(
                proposition_id="prop_002",
                proposition_type=PropositionType.EXPOSURE,
                concept_text="吸烟",
                source_span=span(text, "吸烟30余年，约20支/日"),
                modifiers=[
                    ClinicalModifier(
                        modifier_id="mod_002",
                        modifier_type=ModifierType.DURATION,
                        value_text="30余年",
                        source_span=span(text, "30余年", second_duration_start),
                    ),
                    ClinicalModifier(
                        modifier_id="mod_003",
                        modifier_type=ModifierType.INTENSITY,
                        value_text="约20支/日",
                        source_span=span(text, "约20支/日"),
                    ),
                ],
                rationale="test",
            ),
        ],
    )

    validated = validate_clinical_propositions(result, make_unit(text), make_frame())

    assert [item.value_text for item in validated.propositions[0].modifiers] == ["30余年"]
    assert [item.value_text for item in validated.propositions[1].modifiers] == [
        "30余年",
        "约20支/日",
    ]


def test_validation_rejects_incorrect_source_span_and_duplicate_modifier_ids():
    text = "咳痰，量少。"
    bad_span = GraphUnitClinicalPropositions(
        graph_unit_id="seg_001_gu_001",
        primary_frame=PrimaryFrame.BACKGROUND_CONTEXT,
        event_modifiers=[
            ClinicalModifier(
                modifier_id="mod_001",
                modifier_type=ModifierType.QUANTITY,
                value_text="量少",
                source_span=EvidenceSpan(text="量少", start_char=0, end_char=2),
            )
        ],
        propositions=[
            ClinicalProposition(
                proposition_id="prop_001",
                proposition_type=PropositionType.SYMPTOM,
                concept_text="咳痰",
                source_span=span(text, "咳痰"),
                modifiers=[
                    ClinicalModifier(
                        modifier_id="mod_001",
                        modifier_type=ModifierType.QUANTITY,
                        value_text="量少",
                        source_span=span(text, "量少"),
                    )
                ],
                rationale="test",
            )
        ],
    )

    with pytest.raises(ValueError, match="Evidence span mismatch|Duplicate modifier_id"):
        validate_clinical_propositions(bad_span, make_unit(text), make_frame())


def test_extractor_uses_schema_response_and_validates_nested_modifier_ownership():
    text = "活动后气短明显。"
    response = {
        "graph_unit_id": "seg_001_gu_001",
        "primary_frame": "background_context",
        "event_modifiers": [],
        "propositions": [
            {
                "proposition_id": "prop_001",
                "proposition_type": "symptom",
                "concept_text": "气短",
                "status": "present",
                "certainty": "high",
                "attribution": None,
                "modifiers": [
                    {
                        "modifier_id": "mod_001",
                        "modifier_type": "context",
                        "value_text": "活动后",
                        "source_span": {
                            "text": "活动后",
                        },
                    },
                    {
                        "modifier_id": "mod_002",
                        "modifier_type": "severity",
                        "value_text": "明显",
                        "source_span": {
                            "text": "明显",
                        },
                    },
                ],
                "source_span": {
                    "text": "活动后气短明显",
                },
                "rationale": "test",
            }
        ],
        "notes": [],
        "metadata": {},
    }
    llm = CapturingLLM(response)
    extractor = ClinicalPropositionExtractor(
        llm,
        "src/prompts/semantic_graphing/clinical_proposition_extraction.md",
        temperature=0,
        max_tokens=1000,
    )

    result, _ = extractor.extract_unit(make_unit(text), make_frame())

    assert llm.response_format == {"type": "json_object"}
    assert '"concept_text"' in llm.messages[1].content
    assert '"value_text"' in llm.messages[1].content
    assert '"actor_text"' in llm.messages[1].content
    assert '"attribution": null' in llm.messages[1].content
    assert "attribution 表示当前 graph unit 原文明示的陈述来源" in llm.messages[1].content
    assert "不得仅因其主体是患者而填写 patient attribution" in llm.messages[1].content
    assert "来源仅在上级 segment 或其他 graph unit 出现时，必须输出 null" in llm.messages[1].content
    assert "两者不要求逐字相同" in llm.messages[1].content
    assert "不要把语义展开或规范化后的 `concept_text` 直接复制" in llm.messages[1].content
    assert result.propositions[0].modifiers[0].value_text == "活动后"
    assert result.propositions[0].source_span.start_char == 0
    assert result.propositions[0].source_span.end_char == 7
    assert result.propositions[0].modifiers[1].source_span.start_char == 5
    assert result.propositions[0].modifiers[1].source_span.end_char == 7


def test_ungrounded_attribution_error_explains_implicit_subjects_use_null():
    text = "吸烟30余年。"
    response = {
        "graph_unit_id": "seg_001_gu_001",
        "primary_frame": "background_context",
        "event_modifiers": [],
        "propositions": [
            {
                "proposition_id": "prop_001",
                "proposition_type": "exposure",
                "concept_text": "吸烟",
                "status": "historical",
                "certainty": "high",
                "attribution": {
                    "attribution_type": "patient",
                    "actor_text": "患者",
                    "source_span": {"text": "患者"},
                },
                "modifiers": [],
                "source_span": {"text": "吸烟30余年"},
                "rationale": "test",
            }
        ],
        "notes": [],
        "metadata": {},
    }

    with pytest.raises(
        ValueError,
        match="not explicitly grounded.*not an implicit proposition subject.*attribution to null",
    ):
        normalize_clinical_proposition_spans(
            UnlocatedGraphUnitClinicalPropositions.model_validate(response),
            make_unit(text),
        )


def test_actor_outside_attribution_span_error_explains_implicit_subjects_use_null():
    text = "患者吸烟30余年。"
    response = {
        "graph_unit_id": "seg_001_gu_001",
        "primary_frame": "background_context",
        "event_modifiers": [],
        "propositions": [
            {
                "proposition_id": "prop_001",
                "proposition_type": "exposure",
                "concept_text": "吸烟",
                "status": "historical",
                "certainty": "high",
                "attribution": {
                    "attribution_type": "patient",
                    "actor_text": "患者",
                    "source_span": {"text": "吸烟30余年"},
                },
                "modifiers": [],
                "source_span": {"text": "患者吸烟30余年"},
                "rationale": "test",
            }
        ],
        "notes": [],
        "metadata": {},
    }
    normalized = normalize_clinical_proposition_spans(
        UnlocatedGraphUnitClinicalPropositions.model_validate(response),
        make_unit(text),
    )

    with pytest.raises(
        ValueError,
        match="actor_text must occur.*not an implicit proposition subject.*attribution to null",
    ):
        validate_clinical_propositions(normalized, make_unit(text), make_frame())


def test_nonverbatim_modifier_evidence_is_rejected_without_semantic_reconstruction():
    text = "活动后气短。"
    response = {
        "graph_unit_id": "seg_001_gu_001",
        "primary_frame": "background_context",
        "event_modifiers": [],
        "propositions": [
            {
                "proposition_id": "prop_001",
                "proposition_type": "symptom",
                "concept_text": "气短",
                "status": "present",
                "certainty": "high",
                "attribution": None,
                "modifiers": [
                    {
                        "modifier_id": "mod_001",
                        "modifier_type": "context",
                        "value_text": "活动时",
                        "source_span": {"text": "活动时"},
                    }
                ],
                "source_span": {"text": "活动后气短"},
                "rationale": "test",
            }
        ],
        "notes": [],
        "metadata": {},
    }

    with pytest.raises(
        ValueError,
        match="Evidence for mod_001 cannot be located.*exact continuous source text",
    ):
        normalize_clinical_proposition_spans(
            UnlocatedGraphUnitClinicalPropositions.model_validate(response),
            make_unit(text),
        )


def test_program_computes_incorrect_model_offsets():
    text = "癌胚抗原:CEA 5.73ng/mL。"
    result = GraphUnitClinicalPropositions(
        graph_unit_id="seg_001_gu_001",
        primary_frame=PrimaryFrame.BACKGROUND_CONTEXT,
        propositions=[
            ClinicalProposition(
                proposition_id="prop_001",
                proposition_type=PropositionType.MEASUREMENT,
                concept_text="CEA 5.73ng/mL",
                source_span=EvidenceSpan(
                    text="CEA 5.73ng/mL",
                    start_char=0,
                    end_char=1,
                ),
                rationale="test",
            )
        ],
    )

    normalized = normalize_clinical_proposition_spans(result, make_unit(text))

    assert normalized.propositions[0].source_span.start_char == text.index("CEA")
    assert normalized.propositions[0].source_span.end_char == text.index("CEA") + len("CEA 5.73ng/mL")


def test_extractor_retries_nonverbatim_proposition_evidence_with_actionable_guidance():
    text = (
        "术前完善肺功能；呼吸储备功能、肺容量及气道阻力正常，"
        "常规超声心动图六项：二尖瓣、三尖瓣返流。"
    )
    invalid_response = {
        "graph_unit_id": "seg_001_gu_001",
        "primary_frame": "background_context",
        "event_modifiers": [],
        "propositions": [
            {
                "proposition_id": "prop_001",
                "proposition_type": "finding",
                "concept_text": "呼吸储备功能正常",
                "status": "present",
                "certainty": "high",
                "attribution": None,
                "modifiers": [],
                "source_span": {"text": "呼吸储备功能正常"},
                "rationale": "展开共享状态",
            },
            {
                "proposition_id": "prop_002",
                "proposition_type": "finding",
                "concept_text": "二尖瓣返流",
                "status": "present",
                "certainty": "high",
                "attribution": None,
                "modifiers": [],
                "source_span": {"text": "二尖瓣返流"},
                "rationale": "展开共享谓词",
            },
        ],
        "notes": [],
        "metadata": {},
    }
    corrected_response = {
        **invalid_response,
        "propositions": [
            {
                **invalid_response["propositions"][0],
                "source_span": {"text": "呼吸储备功能、肺容量及气道阻力正常"},
            },
            {
                **invalid_response["propositions"][1],
                "source_span": {"text": "二尖瓣、三尖瓣返流"},
            },
        ],
    }
    llm = SequencedLLM([invalid_response, corrected_response])
    extractor = ClinicalPropositionExtractor(
        llm,
        "src/prompts/semantic_graphing/clinical_proposition_extraction.md",
        temperature=0,
        max_tokens=1000,
    )

    result, trace = extractor.extract_unit(make_unit(text), make_frame())

    retry_prompt = llm.messages_by_attempt[1][1].content
    assert "concept_text may be a normalized or coordination-expanded clinical statement" in retry_prompt
    assert "source_span.text must quote the complete continuous source evidence" in retry_prompt
    assert "Select that verbatim evidence span instead of copying concept_text" in retry_prompt
    assert len(trace["attempts"]) == 2
    assert trace["attempts"][0]["validated"] is False
    assert trace["attempts"][1]["validated"] is True
    assert result.propositions[0].source_span.text == "呼吸储备功能、肺容量及气道阻力正常"
    assert result.propositions[1].source_span.text == "二尖瓣、三尖瓣返流"


def test_dense_unit_text_is_split_into_exact_contiguous_chunks():
    text = "检查：" + ("项目1，项目2，项目3；" * 100) + "结束。"

    chunks = split_dense_unit_text(text, max_chunk_chars=120)

    assert len(chunks) > 1
    assert "".join(chunk for _, chunk in chunks) == text
    assert all(text[start : start + len(chunk)] == chunk for start, chunk in chunks)


def test_dense_unit_without_strong_boundaries_is_not_split():
    text = "咳嗽，伴胸闷，量少，夜间明显，" * 100

    assert split_dense_unit_text(text, max_chunk_chars=120) == [(0, text)]
