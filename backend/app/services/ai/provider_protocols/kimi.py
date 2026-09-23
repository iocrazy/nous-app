from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import ProviderProtocol


class KimiProtocol(ProviderProtocol):
    key = "kimi"
    label = "Kimi (Moonshot)"
    description = "Moonshot AI chat-completions (kimi-* / moonshot-*)."
    model_types = ("llm",)
    credential_kind = "api_key"
    ai_provider_name = "KimiProvider"
    is_chat_key = True

    def build_chat_adapter(
        self, model: str, creds: dict[str, Any], **context: Any
    ) -> Any:
        from app.services.ai.adapters.kimi import (
            KIMI_DEFAULT_MODEL,
            KIMI_DEFAULT_URL,
            KimiAdapter,
        )
        from app.services.ai.provider_protocols.base import (
            ProviderNotConfiguredError,
        )

        api_key = (creds.get("api_key") or "").strip()
        if not api_key:
            raise ProviderNotConfiguredError("kimi", model)
        base_url = (creds.get("base_url") or "").strip()
        return KimiAdapter(
            api_url=base_url or KIMI_DEFAULT_URL,
            api_key=api_key,
            default_model=model or KIMI_DEFAULT_MODEL,
        )
