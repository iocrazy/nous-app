from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import ProviderProtocol


class ClaudeProtocol(ProviderProtocol):
    key = "claude"
    label = "Claude (Anthropic)"
    description = "Native Anthropic Messages API (claude-*)."
    model_types = ("llm",)
    is_chat_key = True

    def build_chat_adapter(self, model: str, creds: dict[str, Any]) -> Any:
        from app.services.ai.adapters.claude import ClaudeAdapter
        from app.services.ai.provider_protocols.base import (
            ProviderNotConfiguredError,
        )

        api_key = (creds.get("api_key") or "").strip()
        if not api_key:
            raise ProviderNotConfiguredError("claude", model)
        return ClaudeAdapter(api_key=api_key, default_model=model or "claude-opus-4-5")
