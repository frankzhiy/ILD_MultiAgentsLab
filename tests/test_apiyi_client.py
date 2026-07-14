import json

import pytest

from src.llm.apiyi_client import APIYIClient
from src.llm.base import LLMMessage
from src.llm.factory import build_llm_client
from src.utils.config import load_yaml


class FakeHTTPResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return b'{"choices":[{"message":{"content":"ok"}}]}'


def test_multi_agent_config_builds_apiyi_client(monkeypatch):
    monkeypatch.setenv("APIYI_API_KEY", "secret")

    config = load_yaml("configs/agents/multi_agent_system/llm.yaml")
    client = build_llm_client(config)

    assert isinstance(client, APIYIClient)
    assert client.model == "deepseek-v4-flash"
    assert client.base_url == "https://api.apiyi.com/v1"
    assert client.request_options == {"thinking": {"type": "disabled"}}


def test_semantic_graph_config_builds_apiyi_client(monkeypatch):
    monkeypatch.setenv("APIYI_API_KEY", "secret")

    config = load_yaml("configs/agents/semantic_graphing/agent.yaml")
    client = build_llm_client(config)

    assert isinstance(client, APIYIClient)
    assert client.model == "deepseek-v4-flash"
    assert client.request_options == {"thinking": {"type": "disabled"}}


def test_apiyi_sends_provider_options_without_implicit_json_mode(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeHTTPResponse()

    monkeypatch.setattr("src.llm.apiyi_client.urllib.request.urlopen", fake_urlopen)
    client = APIYIClient(
        api_key="secret",
        model="deepseek-v4-flash",
        base_url="https://api.apiyi.com/v1",
        timeout_seconds=42,
        request_options={"thinking": {"type": "disabled"}},
    )

    response = client.complete(
        [LLMMessage(role="user", content="test")],
        temperature=0,
        max_tokens=100,
    )

    assert captured["url"] == "https://api.apiyi.com/v1/chat/completions"
    assert captured["payload"]["model"] == "deepseek-v4-flash"
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert "response_format" not in captured["payload"]
    assert captured["timeout"] == 42
    assert response.content == "ok"


def test_apiyi_rejects_non_mapping_request_options(monkeypatch):
    monkeypatch.setenv("APIYI_API_KEY", "secret")

    with pytest.raises(ValueError, match="request_options"):
        APIYIClient.from_config(
            {
                "model": "deepseek-v4-flash",
                "base_url": "https://api.apiyi.com/v1",
                "request_options": ["thinking"],
            }
        )
