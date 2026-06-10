import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from src.llm.base import LLMClient, LLMMessage, LLMResponse


@dataclass
class ChatAnywhereClient(LLMClient):
    api_key: str
    model: str
    base_url: str
    timeout_seconds: int = 300
    response_format_json: bool = True

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "ChatAnywhereClient":
        api_key_env = str(config.get("api_key_env", "CHATANYWHERE_API_KEY"))
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(f"{api_key_env} is required for real LLM runs.")
        if not config.get("model"):
            raise ValueError("Agent config must define model.")
        if not config.get("base_url"):
            raise ValueError("Agent config must define base_url.")
        return cls(
            api_key=api_key,
            model=str(config["model"]),
            base_url=str(config["base_url"]),
            timeout_seconds=int(config.get("timeout_seconds", 300)),
        )

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        max_tokens: int,
        response_format: dict | None = None,
    ) -> LLMResponse:
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format
        elif self.response_format_json:
            payload["response_format"] = {"type": "json_object"}

        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ChatAnywhere HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"ChatAnywhere request failed: {exc}") from exc
        except TimeoutError as exc:
            raise RuntimeError(
                f"ChatAnywhere request timed out after {self.timeout_seconds}s"
            ) from exc
        except socket.timeout as exc:
            raise RuntimeError(
                f"ChatAnywhere request timed out after {self.timeout_seconds}s"
            ) from exc

        try:
            raw = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "ChatAnywhere returned a non-JSON response: "
                f"{response_text[:1000]!r}"
            ) from exc

        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected ChatAnywhere response shape: {raw}") from exc
        if not isinstance(content, str):
            raise RuntimeError(
                f"ChatAnywhere returned non-string message content: {content!r}; "
                f"response={raw}"
            )
        return LLMResponse(content=content, raw=raw)
