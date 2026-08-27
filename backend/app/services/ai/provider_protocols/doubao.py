from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import ProviderProtocol


class DoubaoProtocol(ProviderProtocol):
    key = "doubao"
    label = "Doubao (chat)"
    description = "Volcengine Doubao chat-completions (doubao-*/ep-*)."
    model_types = ("llm", "embedding", "asr")
    is_chat_key = True

    def build_chat_adapter(
        self, model: str, creds: dict[str, Any], **context: Any
    ) -> Any:
        from app.services.ai.adapters.doubao import DOUBAO_DEFAULT_URL, DoubaoAdapter
        from app.services.ai.provider_protocols.base import (
            ProviderNotConfiguredError,
        )

        api_key = (creds.get("api_key") or "").strip()
        if not api_key:
            raise ProviderNotConfiguredError("doubao", model)
        base_url = (creds.get("base_url") or "").strip()
        return DoubaoAdapter(
            api_url=base_url or DOUBAO_DEFAULT_URL,
            api_key=api_key,
            default_model=model,
        )
