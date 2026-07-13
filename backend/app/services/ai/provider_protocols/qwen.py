from __future__ import annotations

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
