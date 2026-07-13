from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class OpenAIProtocol(ProviderProtocol):
    key = "openai"
    label = "OpenAI (native)"
    description = "Native OpenAI API (multimodal gpt-*/o1/o3)."
    model_types = ("llm", "embedding", "asr")
    is_chat_key = True
