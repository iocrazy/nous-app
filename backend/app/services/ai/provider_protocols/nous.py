from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import ProviderProtocol


class NousProtocol(ProviderProtocol):
    """The self-hosted nous-engine gateway (vLLM behind an OpenAI-compatible
    front door).

    Behaviourally this is the ``qwen`` protocol — both build an
    ``OpenAICompatibleAdapter`` over the row's base_url. It is a separate key
    because ``actual_provider`` is what Admin → AI Models shows on the card,
    and the engine's rows used to sit under ``openai`` ("OpenAI (native)"),
    which named a vendor we do not call and hid the one fact that matters
    about these rows: they run on our own hardware.

    Generic third-party OpenAI-compatible endpoints still belong on ``qwen``
    (label "OpenAI-Compatible (generic)"). The split is about provenance, not
    wire format: ``nous`` means we operate the machine.
    """

    key = "nous"
    label = "Nous Engine"
    description = (
        "Self-hosted nous-engine gateway (OpenAI-compatible /chat/completions). "
        "The base_url is part of the credential — the engine has no public "
        "default endpoint. Model deployment lives in nous-engine, not here."
    )
    model_types = ("llm", "embedding", "asr")
    credential_kind = "api_key"
    is_chat_key = True

    def build_chat_adapter(
        self, model: str, creds: dict[str, Any], **context: Any
    ) -> Any:
        from app.services.ai.adapters.nous import NousAdapter
        from app.services.ai.provider_protocols.base import (
            ProviderNotConfiguredError,
        )

        # base_url, not api_key: a private gateway may legitimately run without
        # one, but it can never be reached without an address.
        base_url = (creds.get("base_url") or "").strip()
        if not base_url:
            raise ProviderNotConfiguredError("nous", model)
        return NousAdapter(
            api_url=base_url,
            api_key=(creds.get("api_key") or "").strip(),
            default_model=model,
        )
