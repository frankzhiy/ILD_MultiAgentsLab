import json
import socket
import ssl
import urllib.error

import pytest

from src.llm.apiyi_client import APIYIClient
from src.llm.base import LLMMessage
from src.llm.factory import build_llm_client
from src.llm.structured import StructuredGenerationError, StructuredLLMGenerator
from src.utils.config import load_yaml


class FakeHTTPResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return b'{"choices":[{"message":{"content":"ok"}}]}'


@pytest.mark.parametrize("config_path", [
    "configs/agents/multi_agent_system/llm.yaml",
    "configs/agents/pulmonology/agent.yaml",
    "configs/agents/thoracic_radiology/agent.yaml",
    "configs/agents/rheumatology/agent.yaml",
    "configs/agents/pathology/agent.yaml",
    "configs/agents/mdt_chair/agent.yaml",
])
def test_multi_agent_config_builds_apiyi_client(monkeypatch, config_path):
    monkeypatch.setenv("APIYI_API_KEY", "secret")

    config = load_yaml(config_path)
    client = build_llm_client(config)

    assert isinstance(client, APIYIClient)
    assert client.model == "gpt-6.1-sol"
    assert client.base_url == "https://api.apiyi.com/v1"
    assert client.request_options == {"reasoning_effort": "high"}


def test_semantic_graph_config_builds_apiyi_client(monkeypatch):
    monkeypatch.setenv("APIYI_API_KEY", "secret")

    config = load_yaml("configs/agents/semantic_graphing/agent.yaml")
    client = build_llm_client(config)

    assert isinstance(client, APIYIClient)
    assert client.model == "gpt-6.1-sol"
    assert client.request_options == {"reasoning_effort": "medium"}
    assert client.supports_json_schema is True


@pytest.mark.parametrize("effort", ["medium", "high", "none"])
def test_apiyi_gpt6_request_parameters(monkeypatch, effort):
    captured = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data))
        return FakeHTTPResponse()

    monkeypatch.setattr("src.llm.apiyi_client.urllib.request.urlopen", fake_urlopen)
    client = APIYIClient(
        api_key="secret",
        model="gpt-6-luna" if effort == "none" else "gpt-6.1-sol",
        base_url="https://api.apiyi.com/v1",
        request_options={"reasoning_effort": effort},
    )
    client.complete([LLMMessage(role="user", content="test")], temperature=0, max_tokens=100)

    assert captured["reasoning_effort"] == effort
    assert captured["max_completion_tokens"] == 100
    assert "max_tokens" not in captured
    assert ("temperature" in captured) == (effort == "none")


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


def test_apiyi_forwards_explicit_json_schema(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data)
        return FakeHTTPResponse()

    monkeypatch.setattr("src.llm.apiyi_client.urllib.request.urlopen", fake_urlopen)
    client = APIYIClient(
        api_key="secret",
        model="model",
        base_url="https://apiyi.example/v1",
        supports_json_schema=True,
    )
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "probe",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
        },
    }

    client.complete(
        [LLMMessage(role="user", content="test")],
        temperature=0,
        max_tokens=20,
        response_format=response_format,
    )

    assert captured["payload"]["response_format"] == response_format


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


@pytest.mark.parametrize(("error", "retryable", "recovers"), [
    (ssl.SSLEOFError(8, "unexpected EOF"), True, True),
    (urllib.error.URLError(ssl.SSLEOFError(8, "unexpected EOF")), True, True),
    (urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure")), True, True),
    (ssl.SSLEOFError(8, "unexpected EOF"), True, False),
    (urllib.error.HTTPError("https://apiyi.example/v1", 401, "Unauthorized", {}, None), False, False),
    (urllib.error.URLError(ssl.SSLCertVerificationError("invalid certificate")), False, False),
    (urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "unknown host")), False, False),
    (urllib.error.URLError(PermissionError("operation not permitted")), False, False),
])
def test_apiyi_transport_retry_recovers_or_stops_within_budget(monkeypatch, error, retryable, recovers):
    from pydantic import BaseModel

    class Result(BaseModel):
        ok: bool

    calls = []

    class Response(FakeHTTPResponse):
        def read(self):
            if len(calls) == 1 or not recovers:
                raise error
            return json.dumps({"choices": [{"message": {"content": '{"ok":true}'}}]}).encode()

    def urlopen(*args, **kwargs):
        calls.append(1)
        return Response()

    monkeypatch.setattr("src.llm.apiyi_client.urllib.request.urlopen", urlopen)
    generator = StructuredLLMGenerator(
        APIYIClient("secret", "model", "https://apiyi.example/v1"),
        temperature=0, max_tokens=20, max_attempts=2,
    )
    request = dict(schema_model=Result, schema_name="probe", system_prompt="test", user_prompt="test")
    if recovers:
        result, trace = generator.generate(**request)
        assert result.ok
        assert "transport_error" in trace["attempts"][0]
        assert trace["attempts"][1]["validated"]
    else:
        with pytest.raises(StructuredGenerationError) as failure:
            generator.generate(**request)
        assert len(failure.value.attempts) == len(calls)
    assert len(calls) == (2 if retryable else 1)
