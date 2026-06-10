from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor

import pytest
from pydantic import BaseModel

import src.agents.semantic_graphing.agent as agent_module
from src.agents.semantic_graphing.agent import SemanticGraphingAgent
from src.llm.base import LLMResponse
from src.llm.chatanywhere_client import ChatAnywhereClient
from src.llm.structured import StructuredGenerationError, StructuredLLMGenerator
from src.schemas.semantic_graphing import (
    ClassifiedSegment,
    DiscourseUnitType,
    DocumentClassification,
    DocumentGraphUnits,
    GraphUnit,
    GraphUnitPrimaryFrame,
    MdtSpecialty,
    PrimaryFrame,
    SegmentGraphUnits,
    SourceType,
)


class ResultSchema(BaseModel):
    value: str


class EmptyResponseLLM:
    def complete(self, messages, *, temperature, max_tokens, response_format=None):
        return LLMResponse(
            content="",
            raw={"choices": [{"finish_reason": "length", "message": {"content": ""}}]},
        )


class FakeGraphUnitExtractor:
    def extract(self, segment):
        unit = GraphUnit(
            graph_unit_id=f"{segment.segment_id}_gu_001",
            segment_id=segment.segment_id,
            text=segment.text,
            source_type=SourceType.OTHER,
            mdt_specialty=[MdtSpecialty.OTHER],
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


def test_chatanywhere_model_and_endpoint_come_from_config(monkeypatch):
    monkeypatch.setenv("TEST_CHATANYWHERE_KEY", "secret")
    monkeypatch.setenv("CHATANYWHERE_MODEL", "ignored-model")
    monkeypatch.setenv("CHATANYWHERE_BASE_URL", "https://ignored.example")

    client = ChatAnywhereClient.from_config(
        {
            "api_key_env": "TEST_CHATANYWHERE_KEY",
            "model": "yaml-model",
            "base_url": "https://yaml.example/v1",
            "timeout_seconds": 42,
        }
    )

    assert client.model == "yaml-model"
    assert client.base_url == "https://yaml.example/v1"
    assert client.timeout_seconds == 42


def test_structured_failure_keeps_each_raw_empty_response():
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

    assert len(raised.value.attempts) == 2
    assert raised.value.attempts[0]["content"] == ""
    assert raised.value.attempts[0]["raw_response"]["choices"][0]["finish_reason"] == "length"
    assert "content_length=0" in str(raised.value)
    assert "finish_reason='length'" in str(raised.value)


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
    _, primary_frame_trace = agent.select_primary_frames(graph_units)

    assert worker_counts == [3, 4]
    assert graph_trace["concurrent_tasks"] == 3
    assert primary_frame_trace["concurrent_tasks"] == 4
