from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class DoubaoProtocol(ProviderProtocol):
    key = "doubao"
    label = "Doubao (chat)"
    description = "Volcengine Doubao chat-completions (doubao-*/ep-*)."
    model_types = ("llm", "embedding", "asr")
    is_chat_key = True
