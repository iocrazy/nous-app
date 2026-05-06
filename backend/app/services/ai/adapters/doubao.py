"""DoubaoAdapter — Volcengine Ark (Doubao) provider.

OpenAI-compatible API. Models are typically invoked by endpoint ID
(e.g. "ep-20240101-abcdef") rather than friendly name.
See https://www.volcengine.com/docs/82379/1262616
"""

from __future__ import annotations

from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter

DOUBAO_DEFAULT_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"


class DoubaoAdapter(OpenAICompatibleAdapter):
    """Doubao (Volcengine Ark) chat completions."""

    def __init__(
        self,
        api_url: str = DOUBAO_DEFAULT_URL,
        api_key: str = "",
        default_model: str = "",
    ) -> None:
        super().__init__(
            api_url=api_url,
            api_key=api_key,
            default_model=default_model,
        )
