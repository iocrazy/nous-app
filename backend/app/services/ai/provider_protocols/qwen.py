from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import ProviderProtocol


class QwenProtocol(ProviderProtocol):
    key = "qwen"
    label = "OpenAI-Compatible (generic)"
    description = (
        "Standard OpenAI /chat/completions contract. The fail-open default: "
        "any self-hosted or aggregated endpoint (vLLM, Nous, etc.) works here "
        "— the base_url + key is the whole credential."
    )
    model_types = ("llm", "embedding", "asr")
    is_chat_key = True
    is_default = True

    def build_chat_adapter(self, model: str, creds: dict[str, Any]) -> Any:
        # Qwen-compatible endpoints have no universal public URL — the
        # base_url IS part of the credential set, so it must come from the
        # DB (platform catalog or BYOK). Unlike every other provider, qwen
        # requires base_url rather than api_key.
        from app.services.ai.adapters.qwen import QwenAdapter
        from app.services.ai.provider_protocols.base import (
            ProviderNotConfiguredError,
        )

        base_url = (creds.get("base_url") or "").strip()
        if not base_url:
            raise ProviderNotConfiguredError("qwen", model)
        api_key = (creds.get("api_key") or "").strip()
        return QwenAdapter(
            api_url=base_url,
            api_key=api_key,
            default_model=model,
        )
