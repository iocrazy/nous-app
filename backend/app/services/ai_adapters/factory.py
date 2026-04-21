"""Adapter factory — dispatches by model prefix to the right adapter.

Used by ScriptAIService (and future AgentRunner callers) to pick the
correct provider adapter based on an agent's ``model`` field.

Known prefixes:
- qwen-* / tongyi-* / "" (empty)           → QwenAdapter  (DashScope)
- deepseek-*                                → DeepSeekAdapter
- doubao-* / ep-*                           → DoubaoAdapter
- claude-*                                  → ClaudeAdapter
Unknown prefixes raise ValueError.
"""

from __future__ import annotations

from typing import Any

from app.services.ai_adapters.base import AIAdapter
from app.services.ai_adapters.claude import ClaudeAdapter
from app.services.ai_adapters.deepseek import DeepSeekAdapter
from app.services.ai_adapters.doubao import DoubaoAdapter
from app.services.ai_adapters.qwen import QwenAdapter


def get_adapter(model: str, settings: Any) -> AIAdapter:
    """Dispatch by model prefix. ``settings`` is the app settings object
    (duck-typed — must expose LLM_*/DEEPSEEK_*/DOUBAO_*/CLAUDE_API_KEY)."""
    m = (model or "").lower()

    if m.startswith("claude-"):
        return ClaudeAdapter(
            api_key=settings.CLAUDE_API_KEY,
            default_model=model or "claude-opus-4-5",
        )

    if m.startswith("deepseek-"):
        return DeepSeekAdapter(
            api_url=settings.DEEPSEEK_API_URL,
            api_key=settings.DEEPSEEK_API_KEY,
            default_model=model or "deepseek-chat",
        )

    if m.startswith("doubao-") or m.startswith("ep-"):
        return DoubaoAdapter(
            api_url=settings.DOUBAO_API_URL,
            api_key=settings.DOUBAO_API_KEY,
            default_model=model,
        )

    if m == "" or m.startswith("qwen-") or m.startswith("tongyi-"):
        return QwenAdapter(
            api_url=settings.LLM_API_URL,
            api_key=settings.LLM_API_KEY,
            default_model=model or settings.LLM_MODEL,
        )

    raise ValueError(
        f"unsupported model: {model!r} — known prefixes: "
        "qwen-*, tongyi-*, deepseek-*, doubao-*, ep-*, claude-*"
    )
