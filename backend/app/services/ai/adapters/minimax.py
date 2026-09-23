"""MiniMaxAdapter — MiniMax provider.

OpenAI-compatible API. See https://platform.minimaxi.com/document
"""

from __future__ import annotations

from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter

MINIMAX_DEFAULT_URL = "https://api.minimax.chat/v1/chat/completions"
MINIMAX_DEFAULT_MODEL = "MiniMax-M2.5"


class MiniMaxAdapter(OpenAICompatibleAdapter):
    """MiniMax chat completions."""

    def __init__(
        self,
        api_url: str = MINIMAX_DEFAULT_URL,
        api_key: str = "",
        default_model: str = MINIMAX_DEFAULT_MODEL,
    ) -> None:
        super().__init__(
            api_url=api_url,
            api_key=api_key,
            default_model=default_model,
        )
