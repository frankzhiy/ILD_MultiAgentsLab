from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

import src.agents.semantic_graphing.agent as agent_module
from src.agents.semantic_graphing.agent import SemanticGraphingAgent
from src.agents.semantic_graphing.document_classifier import DocumentClassifier
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
)
from scripts.run.run_semantic_graph_agent import build_run_signature, require_complete_output_offsets


class ResultSchema(BaseModel):
    value: str


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


class StaticResponseLLM:
    def __init__(self, response):
        self.response = response
        self.messages = None

    def complete(self, messages, *, temperature, max_tokens, response_format=None):
        self.messages = messages
        return LLMResponse(content=json.dumps(self.response, ensure_ascii=False), raw={})


class FakeGraphUnitExtractor:
    def extract(self, segment):
        unit = GraphUnit(
            graph_unit_id=f"{segment.segment_id}_gu_001",
            segment_id=segment.segment_id,
            text=segment.text,
            source_type=SourceType.OTHER,
            mdt_specialty=[MdtSpecialty.OTHER],
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
                    "segment_id": "seg_001",
                    "text": "主诉：活动后气短。",
                    "unit_type": "demographics_chief_complaint",
                    "contained_source_types": ["chief_complaint"],
                    "clinical_frame": "chief_complaint",
                    "temporal_anchor": None,
                    "confidence": 1,
                    "rationale": "test",
                    "metadata": {},
                },
                {
                    "segment_id": "seg_002",
                    "text": "既往高血压。",
                    "unit_type": "past_medical_history",
                    "contained_source_types": ["past_medical_history"],
                    "clinical_frame": "past_medical_history",
                    "temporal_anchor": None,
                    "confidence": 1,
                    "rationale": "test",
                    "metadata": {},
                },
            ],
            "detected_contained_source_types": [
                "chief_complaint",
                "past_medical_history",
            ],
            "notes": [],
        }
    )
    classifier = DocumentClassifier(
        llm,
        "src/prompts/semantic_graphing/document_classification.md",
        temperature=0,
        max_tokens=1000,
    )

    classification, _ = classifier.classify(text)

    assert "start_char" not in llm.messages[1].content
    assert "end_char" not in llm.messages[1].content
    assert classification.segments[0].start_char == 0
    assert classification.segments[0].end_char == len("主诉：活动后气短。")
    assert classification.segments[1].start_char == text.index("既往高血压。")
    assert classification.segments[1].end_char == len(text)


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
                        mdt_specialty=[MdtSpecialty.OTHER],
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
