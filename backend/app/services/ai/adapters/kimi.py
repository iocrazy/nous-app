"""KimiAdapter — Moonshot AI (Kimi) provider.

OpenAI-compatible API. See https://platform.moonshot.cn/docs
"""

from __future__ import annotations

from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter

KIMI_DEFAULT_URL = "https://api.moonshot.cn/v1/chat/completions"
KIMI_DEFAULT_MODEL = "kimi-k2.5"


class KimiAdapter(OpenAICompatibleAdapter):
    """Kimi (Moonshot) chat completions."""

    def __init__(
        self,
        api_url: str = KIMI_DEFAULT_URL,
        api_key: str = "",
        default_model: str = KIMI_DEFAULT_MODEL,
    ) -> None:
        super().__init__(
            api_url=api_url,
            api_key=api_key,
            default_model=default_model,
        )
