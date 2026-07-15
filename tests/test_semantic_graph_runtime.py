from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

import src.agents.semantic_graphing.agent as agent_module
from src.agents.semantic_graphing.agent import SemanticGraphingAgent
from src.agents.semantic_graphing.document_classifier import (
    DocumentClassifier,
    UnitRangeClassifiedSegment,
    UnitRangeDocumentClassification,
    build_source_units,
    rebuild_document_classification,
)
from src.agents.semantic_graphing.graph_unit_extractor import (
    ExtractedSegmentGraphUnits,
    normalize_and_validate_graph_units,
)
from src.agents.semantic_graphing.primary_frame_selector import PrimaryFrameSelector
from src.llm.base import LLMMessage, LLMResponse
from src.llm.chatanywhere_client import ChatAnywhereClient
from src.llm.deepseek_client import DeepSeekClient
from src.llm.factory import build_llm_client
from src.llm.structured import StructuredGenerationError, StructuredLLMGenerator
from src.schemas.semantic_graphing.clinical_proposition import (
    ClinicalProposition,
    DocumentClinicalPropositions,
    EvidenceBlock,
    EvidenceReference,
    GraphUnitClinicalPropositions,
    PropositionType,
    SegmentClinicalPropositions,
    render_clinical_proposition_catalog,
)
from src.schemas.semantic_graphing.document import (
    ClassifiedSegment,
    DiscourseUnitType,
    DocumentClassification,
    SourceType,
)
from src.schemas.semantic_graphing.graph_unit import (
    DocumentGraphUnits,
    GraphUnit,
    MdtSpecialty,
    SegmentGraphUnits,
)
from src.schemas.semantic_graphing.primary_frame import (
    GraphUnitPrimaryFrame,
    PrimaryFrame,
    render_primary_frame_catalog,
)
from scripts.run.run_semantic_graph_agent import (
    build_run_signature,
    choose_input_files,
    output_file,
    require_complete_output_offsets,
)


class ResultSchema(BaseModel):
    value: str


def test_mdt_specialty_contains_only_the_five_supported_categories():
    assert {specialty.value for specialty in MdtSpecialty} == {
        "pulmonology",
        "thoracic_radiology",
        "pathology",
        "rheumatology",
        "shared_context",
    }


def test_graph_unit_prompt_keeps_event_boundary_but_routes_only_chest_imaging():
    prompt = Path("src/prompts/semantic_graphing/graph_unit_extraction.md").read_text(
        encoding="utf-8"
    )

    assert "专科路由变化也不是切分依据" in prompt
    assert "肺功能、超声心动图、下肢血管超声及其他非胸部影像不属于胸部影像科" in prompt
    assert "CTPA 文字结论由影像科解释" in prompt


def test_semantic_prompts_have_bounded_repeated_instruction_payloads():
    root = Path("src/prompts/semantic_graphing")
    classification = (root / "document_classification.md").read_text(encoding="utf-8")
    graph = (root / "graph_unit_extraction.md").read_text(encoding="utf-8")
    propositions = (root / "clinical_proposition_extraction.md").read_text(
        encoding="utf-8"
    )

    assert len(classification) < 2_000
    assert len(graph) + len(render_primary_frame_catalog()) < 3_500
    assert len(propositions) + len(render_clinical_proposition_catalog()) < 3_000
    assert "{{ unit_text }}" not in propositions


def test_graph_unit_validation_rejects_non_thoracic_tests_routed_to_radiology():
    text = "术前肺功能提示通气障碍，超声心动图示肺动脉压升高，双下肢超声未见血栓。"
    segment = ClassifiedSegment(
        segment_id="seg_001",
        text=text,
        unit_type=DiscourseUnitType.CLINICAL_EPISODE,
        clinical_frame="preoperative_assessment",
        start_char=0,
        end_char=len(text),
        confidence=1,
        rationale="test",
    )
    result = SegmentGraphUnits(
        segment_id=segment.segment_id,
        graph_units=[
            GraphUnit(
                graph_unit_id="seg_001_gu_001",
                segment_id=segment.segment_id,
                text=text,
                source_type=SourceType.PULMONARY_FUNCTION_FINDINGS,
                mdt_specialty=[
                    MdtSpecialty.PULMONOLOGY,
                    MdtSpecialty.THORACIC_RADIOLOGY,
                ],
                rationale="test",
            )
        ],
    )

    with pytest.raises(ValueError, match="non-thoracic tests"):
        normalize_and_validate_graph_units(result, segment)


def test_graph_unit_validation_accepts_mixed_event_with_ctpa():
    text = "术后低氧，超声心动图示肺动脉压升高，CTPA未见中央型肺栓塞。"
    segment = ClassifiedSegment(
        segment_id="seg_001",
        text=text,
        unit_type=DiscourseUnitType.CLINICAL_EPISODE,
        clinical_frame="postoperative_hypoxemia",
        start_char=0,
        end_char=len(text),
        confidence=1,
        rationale="test",
    )
    result = SegmentGraphUnits(
        segment_id=segment.segment_id,
        graph_units=[
            GraphUnit(
                graph_unit_id="seg_001_gu_001",
                segment_id=segment.segment_id,
                text=text,
                source_type=SourceType.PRESENT_ILLNESS,
                mdt_specialty=[
                    MdtSpecialty.PULMONOLOGY,
                    MdtSpecialty.THORACIC_RADIOLOGY,
                ],
                rationale="test",
            )
        ],
    )

    normalized = normalize_and_validate_graph_units(result, segment)

    assert normalized.graph_units[0].text == text


def test_graph_unit_validation_rejects_omitted_clinical_text():
    text = "活动后气短。ANA阳性。"
    segment = ClassifiedSegment(
        segment_id="seg_001",
        text=text,
        unit_type=DiscourseUnitType.CLINICAL_EPISODE,
        clinical_frame="present_illness",
        start_char=0,
        end_char=len(text),
        confidence=1,
        rationale="test",
    )
    result = SegmentGraphUnits(
        segment_id=segment.segment_id,
        graph_units=[
            GraphUnit(
                graph_unit_id="seg_001_gu_001",
                segment_id=segment.segment_id,
                text="活动后气短。",
                source_type=SourceType.PRESENT_ILLNESS,
                mdt_specialty=[MdtSpecialty.PULMONOLOGY],
                rationale="test",
            )
        ],
    )

    with pytest.raises(ValueError, match="omit non-whitespace source text"):
        normalize_and_validate_graph_units(result, segment)


def test_document_validation_rejects_omitted_clinical_text():
    text = "活动后气短。ANA阳性。"
    source_units = build_source_units(text)
    classification = UnitRangeDocumentClassification(
        segments=[
            UnitRangeClassifiedSegment(
                end_unit=1,
                unit_type=DiscourseUnitType.CLINICAL_EPISODE,
                contained_source_types=[SourceType.PRESENT_ILLNESS],
                clinical_frame="present_illness",
                confidence=1,
                rationale="test",
            )
        ]
    )

    with pytest.raises(ValueError, match="last segment must end at source unit 2"):
        rebuild_document_classification(classification, text, source_units)


def test_document_llm_schema_excludes_program_owned_fields():
    schema = UnitRangeDocumentClassification.model_json_schema()
    segment_schema = schema["$defs"]["UnitRangeClassifiedSegment"]

    assert set(schema["properties"]) == {"segments"}
    assert "end_unit" in segment_schema["properties"]
    assert "text" not in segment_schema["properties"]
    assert "segment_id" not in segment_schema["properties"]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        UnitRangeDocumentClassification.model_validate(
            {
                "segments": [
                    {
                        "end_unit": 1,
                        "text": "模型不应复制原文。",
                        "unit_type": "other",
                        "clinical_frame": "other",
                        "confidence": 1,
                        "rationale": "test",
                    }
                ]
            }
        )


def test_source_units_preserve_exact_input_and_program_rebuilds_punctuation():
    text = (
        "一般健康状况：良好。\n"
        "既往冠心病病史，口服普罗布考片5g 每日2次，每日一次。"
        "传染病史：否认。"
    )
    source_units = build_source_units(text)
    classification = UnitRangeDocumentClassification(
        segments=[
            UnitRangeClassifiedSegment(
                end_unit=2,
                unit_type=DiscourseUnitType.CURRENT_MEDICATION,
                contained_source_types=[SourceType.MEDICATION_HISTORY],
                clinical_frame="medication_history",
                confidence=1,
                rationale="test",
            ),
            UnitRangeClassifiedSegment(
                end_unit=3,
                unit_type=DiscourseUnitType.PAST_MEDICAL_HISTORY,
                contained_source_types=[SourceType.PAST_MEDICAL_HISTORY],
                clinical_frame="past_history_summary",
                confidence=1,
                rationale="test",
            ),
        ]
    )

    rebuilt = rebuild_document_classification(classification, text, source_units)

    assert "".join(unit.text for unit in source_units) == text
    assert rebuilt.segments[0].text.endswith("每日一次。")
    assert rebuilt.segments[1].text == "传染病史：否认。"
    assert rebuilt.segments[1].start_char == text.index("传染病史")


def test_document_range_validation_rejects_non_increasing_end_units():
    text = "活动后气短。ANA阳性。"
    source_units = build_source_units(text)
    classification = UnitRangeDocumentClassification(
        segments=[
            UnitRangeClassifiedSegment(
                end_unit=1,
                unit_type=DiscourseUnitType.CLINICAL_EPISODE,
                clinical_frame="present_illness",
                confidence=1,
                rationale="test",
            ),
            UnitRangeClassifiedSegment(
                end_unit=1,
                unit_type=DiscourseUnitType.STANDALONE_LAB_PANEL,
                clinical_frame="standalone_report",
                confidence=1,
                rationale="test",
            ),
        ]
    )

    with pytest.raises(ValueError, match="segment 2 end_unit must be greater than 1"):
        rebuild_document_classification(classification, text, source_units)


def test_graph_unit_llm_schema_requires_frame_but_not_program_owned_ids():
    schema = ExtractedSegmentGraphUnits.model_json_schema()
    unit_schema = schema["$defs"]["ExtractedGraphUnit"]

    assert "primary_frame" in unit_schema["required"]
    assert "primary_frame_rationale" in unit_schema["required"]
    assert "graph_unit_id" not in unit_schema["properties"]
    assert "segment_id" not in unit_schema["properties"]
    assert "start_char" not in unit_schema["properties"]


class FakeHTTPResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return b'{"choices":[{"message":{"content":"{}"}}]}'


class EmptyResponseLLM:
    def complete(self, messages, *, temperature, max_tokens, response_format=None):
        return LLMResponse(
            content="",
            raw={"choices": [{"finish_reason": "length", "message": {"content": ""}}]},
        )


class FailIfCalledLLM:
    def complete(self, messages, *, temperature, max_tokens, response_format=None):
        raise AssertionError("LLM must not be called")


class StaticResponseLLM:
    def __init__(self, response):
        self.response = response
        self.messages = None

    def complete(self, messages, *, temperature, max_tokens, response_format=None):
        self.messages = messages
        return LLMResponse(content=json.dumps(self.response, ensure_ascii=False), raw={})


def test_primary_frame_selector_reuses_graph_unit_decision_without_llm_call():
    unit = GraphUnit(
        graph_unit_id="seg_001_gu_001",
        segment_id="seg_001",
        text="活动后气短。",
        source_type=SourceType.PRESENT_ILLNESS,
        mdt_specialty=[MdtSpecialty.PULMONOLOGY],
        primary_frame=PrimaryFrame.SYMPTOM_EPISODE,
        primary_frame_rationale="同一症状事件核",
        rationale="test",
    )
    selector = PrimaryFrameSelector(
        FailIfCalledLLM(),
        "src/prompts/semantic_graphing/primary_frame_selection.md",
        temperature=0,
        max_tokens=100,
    )

    selected, trace = selector.select_unit(unit)

    assert selected.primary_frame == PrimaryFrame.SYMPTOM_EPISODE
    assert selected.rationale == "同一症状事件核"
    assert trace == {"derived_from_graph_unit": True, "attempts": []}


class FakeGraphUnitExtractor:
    def extract(self, segment):
        unit = GraphUnit(
            graph_unit_id=f"{segment.segment_id}_gu_001",
            segment_id=segment.segment_id,
            text=segment.text,
            source_type=SourceType.OTHER,
            mdt_specialty=[MdtSpecialty.SHARED_CONTEXT],
            start_char=segment.start_char,
            end_char=segment.end_char,
            segment_start_char=0,
            segment_end_char=len(segment.text),
            rationale="test",
        )
        return SegmentGraphUnits(segment_id=segment.segment_id, graph_units=[unit]), {}


class FakePrimaryFrameSelector:
    def select_unit(self, unit):
        result = GraphUnitPrimaryFrame(
            graph_unit_id=unit.graph_unit_id,
            primary_frame=PrimaryFrame.BACKGROUND_CONTEXT,
            rationale="test",
        )
        return result, {}


class FakeClinicalPropositionExtractor:
    def extract_unit(self, unit, primary_frame, chunk_cache_dir=None):
        result = GraphUnitClinicalPropositions(
            graph_unit_id=unit.graph_unit_id,
            primary_frame=primary_frame.primary_frame,
            evidence_blocks=[
                EvidenceBlock(evidence_id=f"{unit.graph_unit_id}_ev_001", text=unit.text)
            ],
            propositions=[
                ClinicalProposition(
                    proposition_id="prop_001",
                    proposition_type=PropositionType.OTHER,
                    concept_text=unit.text,
                    evidence=EvidenceReference(
                        evidence_ids=[f"{unit.graph_unit_id}_ev_001"],
                        quote=unit.text,
                    ),
                    rationale="test",
                )
            ],
        )
        return result, {}


def test_choose_input_files_can_select_all(tmp_path, monkeypatch):
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("hidden", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _: "0")

    selected = choose_input_files(str(tmp_path))

    assert [path.name for path in selected] == ["a.txt", "b.txt"]


def test_output_file_prefixes_input_stem(tmp_path):
    assert output_file(tmp_path, Path("case_001.txt"), "report.html") == (
        tmp_path / "case_001_report.html"
    )


def test_deepseek_settings_come_from_config(monkeypatch):
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "secret")

    client = DeepSeekClient.from_config(
        {
            "api_key_env": "TEST_DEEPSEEK_KEY",
            "model": "yaml-model",
            "base_url": "https://yaml.example/v1",
            "timeout_seconds": 42,
            "thinking": "disabled",
        }
    )

    assert client.model == "yaml-model"
    assert client.base_url == "https://yaml.example/v1"
    assert client.timeout_seconds == 42
    assert client.thinking == "disabled"


def test_deepseek_rejects_invalid_thinking_config(monkeypatch):
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "secret")

    with pytest.raises(ValueError, match="thinking"):
        DeepSeekClient.from_config(
            {
                "api_key_env": "TEST_DEEPSEEK_KEY",
                "model": "deepseek-v4-pro",
                "base_url": "https://api.deepseek.com",
                "thinking": "off",
            }
        )


def test_deepseek_sends_configured_thinking_mode(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr("src.llm.deepseek_client.urllib.request.urlopen", fake_urlopen)
    client = DeepSeekClient(
        api_key="secret",
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        timeout_seconds=42,
        thinking="disabled",
    )

    client.complete(
        [LLMMessage(role="user", content="test")],
        temperature=0,
        max_tokens=100,
    )

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["timeout"] == 42


def test_chatanywhere_settings_come_from_config(monkeypatch):
    monkeypatch.setenv("TEST_CHATANYWHERE_KEY", "secret")

    client = ChatAnywhereClient.from_config(
        {
            "api_key_env": "TEST_CHATANYWHERE_KEY",
            "model": "gpt-5.5",
            "base_url": "https://api.chatanywhere.tech/v1",
            "timeout_seconds": 42,
            "reasoning_effort": "low",
        }
    )

    assert client.model == "gpt-5.5"
    assert client.base_url == "https://api.chatanywhere.tech/v1"
    assert client.timeout_seconds == 42
    assert client.reasoning_effort == "low"


def test_chatanywhere_rejects_invalid_reasoning_effort(monkeypatch):
    monkeypatch.setenv("TEST_CHATANYWHERE_KEY", "secret")

    with pytest.raises(ValueError, match="reasoning_effort"):
        ChatAnywhereClient.from_config(
            {
                "api_key_env": "TEST_CHATANYWHERE_KEY",
                "model": "gpt-5.5",
                "base_url": "https://api.chatanywhere.tech/v1",
                "reasoning_effort": "disabled",
            }
        )


def test_chatanywhere_sends_configured_reasoning_effort(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return FakeHTTPResponse()

    monkeypatch.setattr("src.llm.chatanywhere_client.urllib.request.urlopen", fake_urlopen)
    client = ChatAnywhereClient(
        api_key="secret",
        model="gpt-5.5",
        base_url="https://api.chatanywhere.tech/v1",
        reasoning_effort="low",
    )

    client.complete(
        [LLMMessage(role="user", content="test")],
        temperature=0,
        max_tokens=100,
    )

    assert captured["payload"]["reasoning_effort"] == "low"


def test_provider_selects_deepseek_or_chatanywhere(monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "secret")
    base_config = {
        "api_key_env": "TEST_PROVIDER_KEY",
        "model": "model",
        "base_url": "https://example.test/v1",
    }

    assert isinstance(build_llm_client({"provider": "deepseek", **base_config}), DeepSeekClient)
    assert isinstance(
        build_llm_client({"provider": "chatanywhere", **base_config}),
        ChatAnywhereClient,
    )
    assert build_llm_client({"provider": "deepseek", **base_config}).supports_json_schema is False
    assert (
        build_llm_client({"provider": "chatanywhere", **base_config}).supports_json_schema is True
    )


def test_provider_rejects_unknown_value():
    with pytest.raises(ValueError, match="provider"):
        build_llm_client({"provider": "unknown"})


def test_structured_empty_length_response_stops_without_repeating():
    generator = StructuredLLMGenerator(
        EmptyResponseLLM(),
        temperature=0,
        max_tokens=10,
        max_attempts=2,
    )

    with pytest.raises(StructuredGenerationError) as raised:
        generator.generate(
            schema_model=ResultSchema,
            schema_name="result",
            system_prompt="system",
            user_prompt="user",
        )

    assert len(raised.value.attempts) == 1
    assert raised.value.attempts[0]["content"] == ""
    assert raised.value.attempts[0]["raw_response"]["choices"][0]["finish_reason"] == "length"
    assert "exhausted its output budget" in str(raised.value)


def test_agent_config_uses_shared_model_and_attempt_limits(tmp_path):
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        "\n".join(
            [
                "model: fallback",
                "classification_prompt: src/prompts/semantic_graphing/document_classification.md",
                "max_attempts: 2",
                "classification_max_attempts: 1",
            ]
        ),
        encoding="utf-8",
    )
    base_client = DeepSeekClient(
        api_key="secret",
        model="fallback",
        base_url="https://example.test/v1",
    )

    agent = SemanticGraphingAgent.from_config(config_path, base_client)

    assert agent.classifier.llm.model == "fallback"
    assert agent.graph_unit_extractor.llm.model == "fallback"
    assert agent.primary_frame_selector.generator.llm.model == "fallback"
    assert agent.clinical_proposition_extractor.generator.llm.model == "fallback"
    assert agent.clinical_proposition_extractor.generator.response_format_mode == "json_object"
    assert agent.classifier.generator.max_attempts == 1
    assert agent.graph_unit_extractor.generator.max_attempts == 2
    assert agent.clinical_proposition_extractor.enable_chunking is False

    chatanywhere_agent = SemanticGraphingAgent.from_config(
        config_path,
        ChatAnywhereClient(
            api_key="secret",
            model="fallback",
            base_url="https://example.test/v1",
        ),
    )
    assert (
        chatanywhere_agent.clinical_proposition_extractor.generator.response_format_mode
        == "json_schema"
    )


def test_classifier_program_computes_offsets_without_asking_model_to_count():
    text = "主诉：活动后气短。既往高血压。"
    llm = StaticResponseLLM(
        {
            "segments": [
                {
                    "end_unit": 1,
                    "unit_type": "demographics_chief_complaint",
                    "contained_source_types": ["chief_complaint"],
                    "clinical_frame": "chief_complaint",
                    "temporal_anchor": None,
                    "confidence": 1,
                    "rationale": "test",
                },
                {
                    "end_unit": 2,
                    "unit_type": "past_medical_history",
                    "contained_source_types": ["past_medical_history"],
                    "clinical_frame": "past_medical_history",
                    "temporal_anchor": None,
                    "confidence": 1,
                    "rationale": "test",
                },
            ],
        }
    )
    classifier = DocumentClassifier(
        llm,
        "src/prompts/semantic_graphing/document_classification.md",
        temperature=0,
        max_tokens=1000,
    )

    classification, _ = classifier.classify(text)

    assert "text" not in llm.response["segments"][0]
    assert "逐字连续原文" not in llm.messages[1].content
    assert '[1] "主诉：活动后气短。"' in llm.messages[1].content
    assert classification.segments[0].start_char == 0
    assert classification.segments[0].end_char == len("主诉：活动后气短。")
    assert classification.segments[1].start_char == text.index("既往高血压。")
    assert classification.segments[1].end_char == len(text)


def test_classifier_accepts_standardized_ild_presentation_categories():
    text = "父亲患肺纤维化。ANA阳性。BALF淋巴细胞比例升高。"
    llm = StaticResponseLLM(
        {
            "segments": [
                {
                    "end_unit": 1,
                    "unit_type": "past_medical_history",
                    "contained_source_types": ["family_history"],
                    "clinical_frame": "family_history",
                    "temporal_anchor": None,
                    "confidence": 1,
                    "rationale": "test",
                },
                {
                    "end_unit": 2,
                    "unit_type": "standalone_lab_panel",
                    "contained_source_types": ["ctd_related_findings"],
                    "clinical_frame": "standalone_report",
                    "temporal_anchor": None,
                    "confidence": 1,
                    "rationale": "test",
                },
                {
                    "end_unit": 3,
                    "unit_type": "standalone_lab_panel",
                    "contained_source_types": ["bronchoscopy_findings"],
                    "clinical_frame": "standalone_report",
                    "temporal_anchor": None,
                    "confidence": 1,
                    "rationale": "test",
                },
            ],
        }
    )
    classifier = DocumentClassifier(
        llm,
        "src/prompts/semantic_graphing/document_classification.md",
        temperature=0,
        max_tokens=1000,
    )

    classification, _ = classifier.classify(text)

    assert classification.detected_contained_source_types == [
        SourceType.FAMILY_HISTORY,
        SourceType.CTD_RELATED_FINDINGS,
        SourceType.BRONCHOSCOPY_FINDINGS,
    ]


def test_each_stage_runs_all_tasks_concurrently(monkeypatch):
    worker_counts = []

    class RecordingExecutor(RealThreadPoolExecutor):
        def __init__(self, max_workers):
            worker_counts.append(max_workers)
            super().__init__(max_workers=max_workers)

    monkeypatch.setattr(agent_module, "ThreadPoolExecutor", RecordingExecutor)
    agent = SemanticGraphingAgent(
        classifier=None,
        graph_unit_extractor=FakeGraphUnitExtractor(),
        primary_frame_selector=FakePrimaryFrameSelector(),
        clinical_proposition_extractor=FakeClinicalPropositionExtractor(),
    )
    classification = DocumentClassification(
        segments=[
            ClassifiedSegment(
                segment_id=f"seg_{index:03d}",
                text=f"text {index}",
                unit_type=DiscourseUnitType.OTHER,
                clinical_frame="test",
                start_char=index,
                end_char=index + 1,
                confidence=1,
                rationale="test",
            )
            for index in range(1, 4)
        ]
    )

    graph_units, graph_trace = agent.extract_graph_units(classification)
    extra_unit = graph_units.segments[0].graph_units[0].model_copy(
        update={"graph_unit_id": "seg_001_gu_002"}
    )
    graph_units = DocumentGraphUnits(
        segments=[
            graph_units.segments[0].model_copy(
                update={"graph_units": [graph_units.segments[0].graph_units[0], extra_unit]}
            ),
            *graph_units.segments[1:],
        ]
    )
    primary_frames, primary_frame_trace = agent.select_primary_frames(graph_units)
    propositions, proposition_trace = agent.extract_clinical_propositions(
        graph_units,
        primary_frames,
    )

    assert isinstance(propositions, DocumentClinicalPropositions)
    assert isinstance(propositions.segments[0], SegmentClinicalPropositions)
    assert worker_counts == [3, 4, 4]
    assert graph_trace["concurrent_tasks"] == 3
    assert primary_frame_trace["concurrent_tasks"] == 4
    assert proposition_trace["concurrent_tasks"] == 4


def test_each_stage_respects_max_concurrency(monkeypatch):
    worker_counts = []

    class RecordingExecutor(RealThreadPoolExecutor):
        def __init__(self, max_workers):
            worker_counts.append(max_workers)
            super().__init__(max_workers=max_workers)

    monkeypatch.setattr(agent_module, "ThreadPoolExecutor", RecordingExecutor)
    agent = SemanticGraphingAgent(
        classifier=None,
        graph_unit_extractor=FakeGraphUnitExtractor(),
        primary_frame_selector=FakePrimaryFrameSelector(),
        clinical_proposition_extractor=FakeClinicalPropositionExtractor(),
        max_concurrency=2,
    )
    classification = DocumentClassification(
        segments=[
            ClassifiedSegment(
                segment_id=f"seg_{index:03d}",
                text=f"text {index}",
                unit_type=DiscourseUnitType.OTHER,
                clinical_frame="test",
                start_char=index,
                end_char=index + 1,
                confidence=1,
                rationale="test",
            )
            for index in range(1, 4)
        ]
    )

    graph_units, graph_trace = agent.extract_graph_units(classification)
    primary_frames, primary_frame_trace = agent.select_primary_frames(graph_units)
    _, proposition_trace = agent.extract_clinical_propositions(graph_units, primary_frames)

    assert worker_counts == [2, 2, 2]
    assert graph_trace["concurrent_tasks"] == 2
    assert primary_frame_trace["concurrent_tasks"] == 2
    assert proposition_trace["concurrent_tasks"] == 2


def test_completed_tasks_are_reused_from_cache(tmp_path):
    agent = SemanticGraphingAgent(
        classifier=None,
        graph_unit_extractor=FakeGraphUnitExtractor(),
        primary_frame_selector=FakePrimaryFrameSelector(),
        clinical_proposition_extractor=FakeClinicalPropositionExtractor(),
    )
    classification = DocumentClassification(
        segments=[
            ClassifiedSegment(
                segment_id="seg_001",
                text="text",
                unit_type=DiscourseUnitType.OTHER,
                clinical_frame="test",
                start_char=0,
                end_char=4,
                confidence=1,
                rationale="test",
            )
        ]
    )

    graph_cache = tmp_path / "graph"
    frame_cache = tmp_path / "frame"
    proposition_cache = tmp_path / "proposition"
    graph_units, _ = agent.extract_graph_units(classification, cache_dir=graph_cache)
    primary_frames, _ = agent.select_primary_frames(graph_units, cache_dir=frame_cache)
    propositions, _ = agent.extract_clinical_propositions(
        graph_units,
        primary_frames,
        cache_dir=proposition_cache,
    )

    class FailingExtractor:
        def __getattr__(self, name):
            raise AssertionError(f"Cache miss called {name}")

    cached_agent = SemanticGraphingAgent(
        classifier=None,
        graph_unit_extractor=FailingExtractor(),
        primary_frame_selector=FailingExtractor(),
        clinical_proposition_extractor=FailingExtractor(),
    )
    cached_graph_units, _ = cached_agent.extract_graph_units(classification, cache_dir=graph_cache)
    cached_primary_frames, _ = cached_agent.select_primary_frames(
        cached_graph_units,
        cache_dir=frame_cache,
    )
    cached_propositions, _ = cached_agent.extract_clinical_propositions(
        cached_graph_units,
        cached_primary_frames,
        cache_dir=proposition_cache,
    )

    assert cached_graph_units == graph_units
    assert cached_primary_frames == primary_frames
    assert cached_propositions == propositions
    require_complete_output_offsets(classification, graph_units)


def test_final_output_contract_rejects_missing_offsets():
    with pytest.raises(ValidationError, match="start_char|end_char"):
        ClassifiedSegment(
            segment_id="seg_001",
            text="text",
            unit_type=DiscourseUnitType.OTHER,
            clinical_frame="test",
            confidence=1,
            rationale="test",
        )
    classification = DocumentClassification(
        segments=[
            ClassifiedSegment(
                segment_id="seg_001",
                text="text",
                unit_type=DiscourseUnitType.OTHER,
                clinical_frame="test",
                start_char=0,
                end_char=4,
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
                        text="text",
                        source_type=SourceType.OTHER,
                        mdt_specialty=[MdtSpecialty.SHARED_CONTEXT],
                        rationale="test",
                    )
                ],
            )
        ]
    )
    with pytest.raises(ValueError, match="missing offsets"):
        require_complete_output_offsets(classification, graph_units)


def test_run_signature_changes_when_prompt_content_changes(tmp_path):
    prompt_paths = {}
    for key in (
        "classification_prompt",
        "graph_unit_prompt",
        "primary_frame_prompt",
        "clinical_proposition_prompt",
    ):
        path = tmp_path / f"{key}.md"
        path.write_text("version one", encoding="utf-8")
        prompt_paths[key] = str(path)

    first = build_run_signature({"model": "gpt-4.1-mini", **prompt_paths})
    Path(prompt_paths["classification_prompt"]).write_text("version two", encoding="utf-8")
    second = build_run_signature({"model": "gpt-4.1-mini", **prompt_paths})

    assert first != second
