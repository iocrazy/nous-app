"""Self-hosted OpenAI-compatible servers the user runs: Ollama and LM Studio.

Both are ``credential_kind = "endpoint"``: the address is what grants access,
a key is optional, and — unlike ``codex-local`` — nothing is reached through
the paired daemon. The request is made BY THIS PROCESS, so the address has to
be reachable from the backend; ``localhost`` there means the backend's own
host, not the user's browser machine.

Model ids from these servers carry no vendor prefix (``qwen2.5:7b``), so there
is no ``provider_key_for_model`` rule for them — they are only ever routed by
a row's ``actual_provider`` or an explicit provider key.
"""

from __future__ import annotations

from typing import Any

from app.services.ai.provider_protocols.base import ProviderProtocol


class OllamaProtocol(ProviderProtocol):
    key = "ollama"
    label = "Ollama"
    description = (
        "A self-hosted Ollama server (OpenAI-compatible /v1). The base_url is "
        "the credential; a key is optional. Root or /v1 base both work."
    )
    model_types = ("llm",)
    credential_kind = "endpoint"
    ai_provider_name = "OllamaProvider"
    is_chat_key = True

    def build_chat_adapter(
        self, model: str, creds: dict[str, Any], **context: Any
    ) -> Any:
        from app.services.ai.adapters.ollama import OllamaAdapter

        return OllamaAdapter(
            api_url=(creds.get("base_url") or "").strip(),
            api_key=(creds.get("api_key") or "").strip(),
            default_model=model,
        )


class LMStudioProtocol(ProviderProtocol):
    key = "lmstudio"
    label = "LM Studio"
    description = (
        "A self-hosted LM Studio server (OpenAI-compatible /v1). The base_url "
        "is the credential; a key is optional. Root or /v1 base both work."
    )
    model_types = ("llm",)
    credential_kind = "endpoint"
    ai_provider_name = "LMStudioProvider"
    is_chat_key = True

    def build_chat_adapter(
        self, model: str, creds: dict[str, Any], **context: Any
    ) -> Any:
        from app.services.ai.adapters.lmstudio import LMStudioAdapter

        return LMStudioAdapter(
            api_url=(creds.get("base_url") or "").strip(),
            api_key=(creds.get("api_key") or "").strip(),
            default_model=model,
        )
