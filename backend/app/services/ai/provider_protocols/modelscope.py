from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import ProviderProtocol


class ModelScopeProtocol(ProviderProtocol):
    key = "modelscope"
    label = "ModelScope"
    description = "ModelScope org/name models (BYO key)."
    model_types = ("llm",)
    is_chat_key = True

    def build_chat_adapter(self, model: str, creds: dict[str, Any]) -> Any:
        # DB-only like every other provider (2026-07-07 follow-up): a missing
        # key raises here instead of building a keyless adapter that dies
        # upstream with ModelScope's opaque auth error.
        from app.services.ai.adapters.modelscope import (
            MODELSCOPE_DEFAULT_URL,
            ModelScopeAdapter,
        )
        from app.services.ai.provider_protocols.base import (
            ProviderNotConfiguredError,
        )

        api_key = (creds.get("api_key") or "").strip()
        if not api_key:
            raise ProviderNotConfiguredError("modelscope", model)
        base_url = (creds.get("base_url") or "").strip()
        return ModelScopeAdapter(
            api_url=base_url or MODELSCOPE_DEFAULT_URL,
            api_key=api_key,
            default_model=model,
        )
