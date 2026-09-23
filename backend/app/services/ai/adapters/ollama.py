"""OllamaAdapter — a self-hosted Ollama server's OpenAI-compatible routes.

Ollama mounts them under ``/v1``; the base URL is normalized through
:func:`ensure_v1_base` so both the root (how Settings stores it) and the
``/v1`` base reach the same endpoint. No key is needed — an empty one sends
no ``Authorization`` header at all.
"""

from __future__ import annotations

from app.services.ai.adapters.openai_compat import (
    OpenAICompatibleAdapter,
    ensure_v1_base,
)

OLLAMA_DEFAULT_URL = "http://localhost:11434/v1"


class OllamaAdapter(OpenAICompatibleAdapter):
    """Ollama chat completions."""

    def __init__(
        self,
        api_url: str = OLLAMA_DEFAULT_URL,
        api_key: str = "",
        default_model: str = "",
    ) -> None:
        super().__init__(
            api_url=ensure_v1_base(api_url or OLLAMA_DEFAULT_URL),
            api_key=api_key,
            default_model=default_model,
        )
