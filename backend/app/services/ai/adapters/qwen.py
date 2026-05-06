"""QwenAdapter — DashScope (Alibaba Tongyi Qwen) provider.

OpenAI-compatible shape; DashScope exposes a drop-in endpoint.
Default URL is the official DashScope compat path; override via settings.
"""

from __future__ import annotations

from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter

QWEN_DEFAULT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


class QwenAdapter(OpenAICompatibleAdapter):
    """Tongyi Qwen via DashScope."""

    def __init__(
        self,
        api_url: str = QWEN_DEFAULT_URL,
        api_key: str = "",
        default_model: str = "qwen-max",
    ) -> None:
        super().__init__(
            api_url=api_url,
            api_key=api_key,
            default_model=default_model,
        )
