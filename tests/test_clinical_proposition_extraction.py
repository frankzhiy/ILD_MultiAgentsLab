import json

import pytest

from src.agents.semantic_graphing.clinical_proposition_extractor import (
    ClinicalPropositionExtractor,
    build_evidence_blocks,
    split_dense_unit_evidence_blocks,
    split_dense_unit_text,
    validate_clinical_propositions,
)
from src.agents.semantic_graphing.clinical_proposition_validator import ClinicalPropositionValidator
from src.llm.base import LLMResponse
from src.schemas.semantic_graphing import (
    ClinicalModifier,
    ClinicalProposition,
    EvidenceReference,
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


def span(text: str, value: str) -> EvidenceReference:
    assert value in text
    blocks = build_evidence_blocks(make_unit(text))
    evidence_ids = [block.evidence_id for block in blocks if value in block.text]
    assert evidence_ids
    return EvidenceReference(evidence_ids=[evidence_ids[0]], quote=value)


def ref(quote: str, evidence_number: int = 1) -> dict:
    return {
        "evidence_ids": [f"seg_001_gu_001_ev_{evidence_number:03d}"],
        "quote": quote,
    }


def result_from(response: dict, text: str) -> GraphUnitClinicalPropositions:
    return GraphUnitClinicalPropositions.model_validate(
        {**response, "evidence_blocks": [item.model_dump() for item in build_evidence_blocks(make_unit(text))]}
    )


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
    result = GraphUnitClinicalPropositions(
        graph_unit_id="seg_001_gu_001",
        primary_frame=PrimaryFrame.BACKGROUND_CONTEXT,
        evidence_blocks=build_evidence_blocks(make_unit(text)),
        propositions=[
            ClinicalProposition(
                proposition_id="prop_001",
                proposition_type=PropositionType.EXPOSURE,
                concept_text="面粉加工工作",
                evidence=span(text, "从事面粉加工工作30余年"),
                modifiers=[
                    ClinicalModifier(
                        modifier_id="mod_001",
                        modifier_type=ModifierType.DURATION,
                        value_text="30余年",
                        evidence=span(text, "30余年"),
                    )
                ],
                rationale="test",
            ),
            ClinicalProposition(
                proposition_id="prop_002",
                proposition_type=PropositionType.EXPOSURE,
                concept_text="吸烟",
                evidence=span(text, "吸烟30余年，约20支/日"),
                modifiers=[
                    ClinicalModifier(
                        modifier_id="mod_002",
                        modifier_type=ModifierType.DURATION,
                        value_text="30余年",
                        evidence=EvidenceReference(
                            evidence_ids=["seg_001_gu_001_ev_002"],
                            quote="30余年",
                        ),
                    ),
                    ClinicalModifier(
                        modifier_id="mod_003",
                        modifier_type=ModifierType.INTENSITY,
                        value_text="约20支/日",
                        evidence=span(text, "约20支/日"),
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


def test_validation_rejects_duplicate_modifier_ids():
    text = "咳痰，量少。"
    bad_span = GraphUnitClinicalPropositions(
        graph_unit_id="seg_001_gu_001",
        primary_frame=PrimaryFrame.BACKGROUND_CONTEXT,
        evidence_blocks=build_evidence_blocks(make_unit(text)),
        event_modifiers=[
            ClinicalModifier(
                modifier_id="mod_001",
                modifier_type=ModifierType.QUANTITY,
                value_text="量少",
                evidence=span(text, "量少"),
            )
        ],
        propositions=[
            ClinicalProposition(
                proposition_id="prop_001",
                proposition_type=PropositionType.SYMPTOM,
                concept_text="咳痰",
                evidence=span(text, "咳痰"),
                modifiers=[
                    ClinicalModifier(
                        modifier_id="mod_001",
                        modifier_type=ModifierType.QUANTITY,
                        value_text="量少",
                        evidence=span(text, "量少"),
                    )
                ],
                rationale="test",
            )
        ],
    )

    with pytest.raises(ValueError, match="Duplicate modifier_id"):
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
                        "evidence": ref("活动后"),
                    },
                    {
                        "modifier_id": "mod_002",
                        "modifier_type": "severity",
                        "value_text": "明显",
                        "evidence": ref("明显"),
                    },
                ],
                "evidence": ref("活动后气短明显"),
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
                    "evidence": ref("患者"),
                },
                "modifiers": [],
                "evidence": ref("吸烟30余年"),
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
        validate_clinical_propositions(
            result_from(response, text),
            make_unit(text),
            make_frame(),
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
                    "evidence": ref("吸烟30余年"),
                },
                "modifiers": [],
                "evidence": ref("患者吸烟30余年"),
                "rationale": "test",
            }
        ],
        "notes": [],
        "metadata": {},
    }
    result = result_from(response, text)

    with pytest.raises(
        ValueError,
        match="actor_text must occur.*not an implicit proposition subject.*attribution to null",
    ):
        validate_clinical_propositions(result, make_unit(text), make_frame())


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
                        "evidence": ref("活动时"),
                    }
                ],
                "evidence": ref("活动后气短"),
                "rationale": "test",
            }
        ],
        "notes": [],
        "metadata": {},
    }

    with pytest.raises(
        ValueError,
        match="Evidence for mod_001 cannot be located.*exact continuous substring",
    ):
        validate_clinical_propositions(
            result_from(response, text),
            make_unit(text),
            make_frame(),
        )


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
                "evidence": ref("呼吸储备功能正常", 2),
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
                "evidence": ref("二尖瓣返流", 2),
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
                "evidence": ref("呼吸储备功能、肺容量及气道阻力正常", 2),
            },
            {
                **invalid_response["propositions"][1],
                "evidence": ref("二尖瓣、三尖瓣返流", 2),
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
    assert "evidence.quote must quote the complete continuous source evidence" in retry_prompt
    assert "Select that verbatim evidence span instead of copying concept_text" in retry_prompt
    assert len(trace["attempts"]) == 2
    assert trace["attempts"][0]["validated"] is False
    assert trace["attempts"][1]["validated"] is True
    assert result.propositions[0].evidence.quote == "呼吸储备功能、肺容量及气道阻力正常"
    assert result.propositions[1].evidence.quote == "二尖瓣、三尖瓣返流"


def test_dense_unit_text_is_split_into_exact_contiguous_chunks():
    text = "检查：" + ("项目1，项目2，项目3；" * 100) + "结束。"

    chunks = split_dense_unit_text(text, max_chunk_chars=120)

    assert len(chunks) > 1
    assert "".join(chunks) == text


def test_dense_unit_without_strong_boundaries_is_not_split():
    text = "咳嗽，伴胸闷，量少，夜间明显，" * 100

    assert split_dense_unit_text(text, max_chunk_chars=120) == [text]


def test_evidence_blocks_preserve_text_and_disambiguate_repeated_sentences():
    text = "气短。气短。检查提示异常；建议复查。"

    blocks = build_evidence_blocks(make_unit(text))

    assert "".join(block.text for block in blocks) == text
    assert [block.evidence_id for block in blocks] == [
        "seg_001_gu_001_ev_001",
        "seg_001_gu_001_ev_002",
        "seg_001_gu_001_ev_003",
        "seg_001_gu_001_ev_004",
    ]
    assert blocks[0].text == blocks[1].text == "气短。"


def test_dense_chunks_keep_evidence_blocks_whole_and_ids_stable():
    text = "检查：" + ("项目1，项目2，项目3；" * 20) + "结束。"
    blocks = build_evidence_blocks(make_unit(text))

    chunks = split_dense_unit_evidence_blocks(blocks, max_chunk_chars=60)

    assert [block for chunk in chunks for block in chunk] == blocks
    assert "".join(block.text for chunk in chunks for block in chunk) == text


def test_validation_rejects_unknown_and_disconnected_evidence_references():
    text = "活动后气短明显。否认发热。"
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
                        "modifier_type": "severity",
                        "value_text": "明显",
                        "evidence": ref("否认发热", 2),
                    }
                ],
                "evidence": ref("活动后气短明显", 1),
                "rationale": "test",
            }
        ],
        "notes": [],
        "metadata": {},
    }

    with pytest.raises(ValueError, match="must share at least one evidence block"):
        validate_clinical_propositions(result_from(response, text), make_unit(text), make_frame())

    response["propositions"][0]["modifiers"] = []
    response["propositions"][0]["evidence"] = {
        "evidence_ids": ["seg_001_gu_001_ev_999"],
        "quote": "活动后气短明显",
    }
    with pytest.raises(ValueError, match="unknown evidence_ids"):
        validate_clinical_propositions(result_from(response, text), make_unit(text), make_frame())


def test_quality_gate_reports_evidence_block_coverage():
    text = "活动后气短明显。否认发热。"
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
                "modifiers": [],
                "evidence": ref("活动后气短明显", 1),
                "rationale": "test",
            }
        ],
        "notes": [],
        "metadata": {},
    }

    validation = ClinicalPropositionValidator().validate_unit(
        make_unit(text),
        make_frame(),
        result_from(response, text),
    )

    assert validation.metrics.evidence_block_count == 2
    assert validation.metrics.referenced_evidence_block_count == 1
    assert validation.metrics.evidence_block_coverage == 0.5
