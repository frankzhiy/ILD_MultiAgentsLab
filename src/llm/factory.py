from typing import Any

from src.llm.base import LLMClient
from src.llm.chatanywhere_client import ChatAnywhereClient
from src.llm.deepseek_client import DeepSeekClient


def build_llm_client(config: dict[str, Any]) -> LLMClient:
    provider = str(config.get("provider", "chatanywhere")).lower()
    if provider == "chatanywhere":
        return ChatAnywhereClient.from_config(config)
    if provider == "deepseek":
        return DeepSeekClient.from_config(config)
    raise ValueError("Agent config provider must be 'chatanywhere' or 'deepseek'.")
