import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from src.llm.base import LLMClient, LLMMessage, LLMResponse


@dataclass
class ChatAnywhereClient(LLMClient):
    api_key: str
    model: str = "gpt-5.5"
    base_url: str = "https://api.chatanywhere.tech/v1"
    timeout_seconds: int = 300
    response_format_json: bool = True

    @classmethod
    def from_env(cls) -> "ChatAnywhereClient":
        api_key = os.environ.get("CHATANYWHERE_API_KEY")
        if not api_key:
            raise RuntimeError("CHATANYWHERE_API_KEY is required for real LLM runs.")
        return cls(
            api_key=api_key,
            model=os.environ.get("CHATANYWHERE_MODEL", "gpt-5.5"),
            base_url=os.environ.get("CHATANYWHERE_BASE_URL", "https://api.chatanywhere.tech/v1"),
            timeout_seconds=int(os.environ.get("CHATANYWHERE_TIMEOUT_SECONDS", "300")),
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
                raw = json.loads(response.read().decode("utf-8"))
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
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected ChatAnywhere response shape: {raw}") from exc
        return LLMResponse(content=content, raw=raw)
