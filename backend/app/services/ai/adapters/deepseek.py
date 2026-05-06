"""DeepSeekAdapter — DeepSeek provider.

OpenAI-compatible API. See https://api-docs.deepseek.com/
"""

from __future__ import annotations

from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter

DEEPSEEK_DEFAULT_URL = "https://api.deepseek.com/v1/chat/completions"


class DeepSeekAdapter(OpenAICompatibleAdapter):
    """DeepSeek chat completions."""

    def __init__(
        self,
        api_url: str = DEEPSEEK_DEFAULT_URL,
        api_key: str = "",
        default_model: str = "deepseek-chat",
    ) -> None:
        super().__init__(
            api_url=api_url,
            api_key=api_key,
            default_model=default_model,
        )
