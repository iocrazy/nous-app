from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import ProviderProtocol


class DeepSeekProtocol(ProviderProtocol):
    key = "deepseek"
    label = "DeepSeek"
    description = "DeepSeek chat-completions endpoint."
    model_types = ("llm",)
    is_chat_key = True

    def build_chat_adapter(self, model: str, creds: dict[str, Any]) -> Any:
        from app.services.ai.adapters.deepseek import (
            DEEPSEEK_DEFAULT_URL,
            DeepSeekAdapter,
        )
        from app.services.ai.provider_protocols.base import (
            ProviderNotConfiguredError,
        )

        api_key = (creds.get("api_key") or "").strip()
        if not api_key:
            raise ProviderNotConfiguredError("deepseek", model)
        base_url = (creds.get("base_url") or "").strip()
        return DeepSeekAdapter(
            api_url=base_url or DEEPSEEK_DEFAULT_URL,
            api_key=api_key,
            default_model=model or "deepseek-chat",
        )
