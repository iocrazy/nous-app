from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import ProviderProtocol


class MiniMaxProtocol(ProviderProtocol):
    key = "minimax"
    label = "MiniMax"
    description = "MiniMax chat-completions (MiniMax-*)."
    model_types = ("llm",)
    credential_kind = "api_key"
    ai_provider_name = "MiniMaxProvider"
    is_chat_key = True

    def build_chat_adapter(
        self, model: str, creds: dict[str, Any], **context: Any
    ) -> Any:
        from app.services.ai.adapters.minimax import (
            MINIMAX_DEFAULT_MODEL,
            MINIMAX_DEFAULT_URL,
            MiniMaxAdapter,
        )
        from app.services.ai.provider_protocols.base import (
            ProviderNotConfiguredError,
        )

        api_key = (creds.get("api_key") or "").strip()
        if not api_key:
            raise ProviderNotConfiguredError("minimax", model)
        base_url = (creds.get("base_url") or "").strip()
        return MiniMaxAdapter(
            api_url=base_url or MINIMAX_DEFAULT_URL,
            api_key=api_key,
            default_model=model or MINIMAX_DEFAULT_MODEL,
        )
