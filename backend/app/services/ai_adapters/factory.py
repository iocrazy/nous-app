"""Adapter factory — dispatches by model prefix to the right adapter.

Used by ScriptAIService (and future AgentRunner callers) to pick the
correct provider adapter based on an agent's ``model`` field.

Known prefixes:
- qwen-* / tongyi-* / "" (empty)           → QwenAdapter  (DashScope)
- deepseek-*                                → DeepSeekAdapter
- doubao-* / ep-*                           → DoubaoAdapter
- claude-*                                  → ClaudeAdapter
Unknown prefixes raise ValueError.

Two entry points:
- ``get_adapter(model, settings)``            — global settings only (legacy).
- ``get_adapter_for_user(model, user_cfg, fallback_settings)`` — per-user BYO
  api_key / base_url with a fallback to global settings when user hasn't
  configured the provider. Used by Celery tasks that resolve an agent slug
  to its configured model and need the end user's own keys.
"""

from __future__ import annotations

from typing import Any, Dict

from app.services.ai_adapters.base import AIAdapter
from app.services.ai_adapters.claude import ClaudeAdapter
from app.services.ai_adapters.deepseek import DeepSeekAdapter
from app.services.ai_adapters.doubao import DoubaoAdapter
from app.services.ai_adapters.qwen import QwenAdapter

_KNOWN_PREFIXES = "qwen-*, tongyi-*, deepseek-*, doubao-*, ep-*, claude-*"


def provider_key_for_model(model: str) -> str:
    """Map a model identifier to the provider key used in ``ai_providers``.

    Raises ValueError for unknown prefixes.
    """
    m = (model or "").lower()
    if m.startswith("claude-"):
        return "claude"
    if m.startswith("deepseek-"):
        return "deepseek"
    if m.startswith("doubao-") or m.startswith("ep-"):
        return "doubao"
    if m == "" or m.startswith("qwen-") or m.startswith("tongyi-"):
        return "qwen"
    raise ValueError(
        f"unsupported model: {model!r} — known prefixes: {_KNOWN_PREFIXES}"
    )


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
        f"unsupported model: {model!r} — known prefixes: {_KNOWN_PREFIXES}"
    )


def get_adapter_for_user(
    model: str,
    user_provider_config: Dict[str, Any],
    fallback_settings: Any,
) -> AIAdapter:
    """Build an adapter using per-user BYO credentials with a global fallback.

    ``user_provider_config`` is the user's full ``ai_providers`` dict, e.g.
    ``{"qwen": {"api_key": "...", "base_url": "..."}, "doubao": {...}}``.
    For the provider key derived from ``model``, its ``api_key``, ``base_url``
    (and where relevant ``app_id``) take precedence; if empty, we fall back to
    ``fallback_settings.*`` — preserving backwards compatibility for users who
    haven't configured the provider in the UI yet.

    Raises ValueError for unknown model prefixes.
    """
    provider_key = provider_key_for_model(model)
    user_cfg = (user_provider_config or {}).get(provider_key, {}) or {}

    user_key = (user_cfg.get("api_key") or "").strip()
    user_base = (user_cfg.get("base_url") or "").strip()

    if provider_key == "claude":
        return ClaudeAdapter(
            api_key=user_key or fallback_settings.CLAUDE_API_KEY,
            default_model=model or "claude-opus-4-5",
        )

    if provider_key == "deepseek":
        return DeepSeekAdapter(
            api_url=user_base or fallback_settings.DEEPSEEK_API_URL,
            api_key=user_key or fallback_settings.DEEPSEEK_API_KEY,
            default_model=model or "deepseek-chat",
        )

    if provider_key == "doubao":
        return DoubaoAdapter(
            api_url=user_base or fallback_settings.DOUBAO_API_URL,
            api_key=user_key or fallback_settings.DOUBAO_API_KEY,
            default_model=model,
        )

    # provider_key == "qwen" (default / only remaining case)
    return QwenAdapter(
        api_url=user_base or fallback_settings.LLM_API_URL,
        api_key=user_key or fallback_settings.LLM_API_KEY,
        default_model=model or fallback_settings.LLM_MODEL,
    )
