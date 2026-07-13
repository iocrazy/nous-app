from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class DeepSeekProtocol(ProviderProtocol):
    key = "deepseek"
    label = "DeepSeek"
    description = "DeepSeek chat-completions endpoint."
    model_types = ("llm",)
    is_chat_key = True
