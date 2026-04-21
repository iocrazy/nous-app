"""Provider adapters for AI Library agents (Phase 2 PR 2.3).

Each adapter normalizes its provider's response into OpenAI-compatible
tool_calls shape so AgentRunner can stay provider-agnostic.
"""

from app.services.ai_adapters.base import AIAdapter
from app.services.ai_adapters.claude import ClaudeAdapter
from app.services.ai_adapters.deepseek import DeepSeekAdapter
from app.services.ai_adapters.doubao import DoubaoAdapter
from app.services.ai_adapters.factory import (
    get_adapter,
    get_adapter_for_user,
    provider_key_for_model,
)
from app.services.ai_adapters.openai_compat import OpenAICompatibleAdapter
from app.services.ai_adapters.qwen import QwenAdapter

__all__ = [
    "AIAdapter",
    "ClaudeAdapter",
    "DeepSeekAdapter",
    "DoubaoAdapter",
    "get_adapter",
    "get_adapter_for_user",
    "provider_key_for_model",
    "OpenAICompatibleAdapter",
    "QwenAdapter",
]
