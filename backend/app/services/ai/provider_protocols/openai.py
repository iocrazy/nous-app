from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import ProviderProtocol


class OpenAIProtocol(ProviderProtocol):
    key = "openai"
    label = "OpenAI (native)"
    description = "Native OpenAI API (multimodal gpt-*/o1/o3)."
    model_types = ("llm", "embedding", "asr")
    is_chat_key = True

    def build_chat_adapter(self, model: str, creds: dict[str, Any]) -> Any:
        from app.services.ai.adapters.openai import OpenAIAdapter
        from app.services.ai.provider_protocols.base import (
            ProviderNotConfiguredError,
        )

        api_key = (creds.get("api_key") or "").strip()
        if not api_key:
            raise ProviderNotConfiguredError("openai", model)
        base_url = (creds.get("base_url") or "").strip()
        return OpenAIAdapter(
            api_key=api_key,
            default_model=model or "gpt-4o",
            api_url=base_url or None,
        )
