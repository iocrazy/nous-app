from __future__ import annotations

from app.services.ai.provider_protocols.base import ProviderProtocol


class ClaudeProtocol(ProviderProtocol):
    key = "claude"
    label = "Claude (Anthropic)"
    description = "Native Anthropic Messages API (claude-*)."
    model_types = ("llm",)
    is_chat_key = True
