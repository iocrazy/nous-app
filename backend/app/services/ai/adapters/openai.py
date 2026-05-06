"""OpenAI adapter — talks to https://api.openai.com/v1/chat/completions by default.

Thin subclass of :class:`OpenAICompatibleAdapter` with the OpenAI endpoint
baked in as the default. Used for multimodal models (gpt-4o / o1 / o3) where
we need image_url content blocks — the base class already passes ``messages``
through to the wire format unchanged, so multimodal works as long as the
caller supplies OpenAI-shaped content arrays.

The ``api_url`` arg is overridable so users with an OpenAI-compatible proxy
(Azure OpenAI, OpenRouter, a self-hosted gateway, etc.) can point this
adapter at their own endpoint without losing the gpt-* / o1 / o3 routing.

Before V1 the ``analyze`` agent bypassed AgentRunner entirely and called
``AsyncOpenAI`` directly, because the factory only knew qwen / deepseek /
doubao / claude. Adding this adapter lets every AI service go through
the same runner + recorder pipeline.
"""

from __future__ import annotations

from typing import Optional

from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter

# Canonical endpoint — used when no custom ``api_url`` is supplied.
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIAdapter(OpenAICompatibleAdapter):
    """Native OpenAI endpoint adapter (GPT-4o, GPT-4, o1, o3, etc.)."""

    def __init__(
        self,
        api_key: str,
        default_model: str = "gpt-4o",
        timeout_seconds: float = 60.0,
        api_url: Optional[str] = None,
    ) -> None:
        super().__init__(
            api_url=api_url or OPENAI_API_URL,
            api_key=api_key,
            default_model=default_model,
            timeout_seconds=timeout_seconds,
        )
