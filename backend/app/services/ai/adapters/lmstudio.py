"""LMStudioAdapter — a self-hosted LM Studio server's OpenAI-compatible routes.

LM Studio mounts them under ``/v1``; see :mod:`.ollama` for why the base URL
goes through :func:`ensure_v1_base`. The key is optional (LM Studio can run
with auth off), and an empty one sends no ``Authorization`` header.
"""

from __future__ import annotations

from app.services.ai.adapters.openai_compat import (
    OpenAICompatibleAdapter,
    ensure_v1_base,
)

LMSTUDIO_DEFAULT_URL = "http://localhost:1234/v1"


class LMStudioAdapter(OpenAICompatibleAdapter):
    """LM Studio chat completions."""

    def __init__(
        self,
        api_url: str = LMSTUDIO_DEFAULT_URL,
        api_key: str = "",
        default_model: str = "",
    ) -> None:
        super().__init__(
            api_url=ensure_v1_base(api_url or LMSTUDIO_DEFAULT_URL),
            api_key=api_key,
            default_model=default_model,
        )
