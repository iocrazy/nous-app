"""ModelScopeAdapter — ModelScope (魔搭) community inference.

OpenAI-compatible API with a generous free tier. Model IDs are
``org/name`` (e.g. ``Qwen/Qwen3-235B-A22B``) — the slash is what routes
a model here (see factory.provider_key_for_model).
See https://modelscope.cn/docs/model-service/API-Inference/intro
"""

from __future__ import annotations

from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter

MODELSCOPE_DEFAULT_URL = "https://api-inference.modelscope.cn/v1/chat/completions"


class ModelScopeAdapter(OpenAICompatibleAdapter):
    """ModelScope API-Inference chat completions."""

    def __init__(
        self,
        api_url: str = MODELSCOPE_DEFAULT_URL,
        api_key: str = "",
        default_model: str = "Qwen/Qwen3-235B-A22B",
    ) -> None:
        super().__init__(
            api_url=api_url,
            api_key=api_key,
            default_model=default_model,
        )
