"""Adapter factory — dispatches by model prefix to the right adapter.

Used by ScriptAIService (and future AgentRunner callers) to pick the
correct provider adapter based on an agent's ``model`` field.

Known prefixes:
- qwen-* / tongyi-* / "" (empty)           → QwenAdapter  (DashScope)
- deepseek-*                                → DeepSeekAdapter
- doubao-* / ep-*                           → DoubaoAdapter
- claude-*                                  → ClaudeAdapter
- gpt-* / o1-* / o3-*                       → OpenAIAdapter (multimodal)
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

from app.services.ai.adapters.base import AIAdapter
from app.services.ai.adapters.claude import ClaudeAdapter
from app.services.ai.adapters.deepseek import DeepSeekAdapter
from app.services.ai.adapters.doubao import DoubaoAdapter
from app.services.ai.adapters.openai import OpenAIAdapter
from app.services.ai.adapters.qwen import QwenAdapter

_KNOWN_PREFIXES = (
    "qwen-*, tongyi-*, deepseek-*, doubao-*, ep-*, claude-*, gpt-*, o1-*, o3-*"
)


def _is_openai(model: str) -> bool:
    """gpt-4o / gpt-4 / gpt-3.5 / o1 / o3 — any native OpenAI model."""
    return (
        model.startswith("gpt-")
        or model.startswith("o1-")
        or model == "o1"
        or model.startswith("o3-")
        or model == "o3"
    )


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
    if _is_openai(m):
        return "openai"
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

    if _is_openai(m):
        return OpenAIAdapter(
            api_key=settings.OPENAI_API_KEY,
            default_model=model or settings.OPENAI_MODEL,
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

    # Sprint 2 #5: api_key may be str OR list[str] (multi-key BYO).
    # When the list has >= 2 non-empty keys, wrap N single-key adapters
    # in a RotatingAdapter so 429/auth-fail on one key automatically
    # rolls to the next instead of failing the request.
    raw_key = user_cfg.get("api_key")
    user_keys: list[str] = []
    if isinstance(raw_key, list):
        user_keys = [k.strip() for k in raw_key if isinstance(k, str) and k.strip()]
    elif isinstance(raw_key, str):
        single = raw_key.strip()
        if single:
            user_keys = [single]

    if len(user_keys) >= 2:
        from app.agent_framework import KeyRotator, RotatingAdapter

        rotator = KeyRotator(user_keys)

        def _build_with_key(key: str):
            # Recurse with a single-key config so the normal branch below
            # builds the right per-provider adapter.
            return get_adapter_for_user(
                model,
                {
                    **(user_provider_config or {}),
                    provider_key: {**user_cfg, "api_key": key},
                },
                fallback_settings,
            )

        return RotatingAdapter(rotator, _build_with_key)

    user_key = user_keys[0] if user_keys else ""
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

    if provider_key == "openai":
        return OpenAIAdapter(
            api_key=user_key or fallback_settings.OPENAI_API_KEY,
            default_model=model or fallback_settings.OPENAI_MODEL,
            api_url=user_base or None,
        )

    # provider_key == "qwen" (default / only remaining case)
    return QwenAdapter(
        api_url=user_base or fallback_settings.LLM_API_URL,
        api_key=user_key or fallback_settings.LLM_API_KEY,
        default_model=model or fallback_settings.LLM_MODEL,
    )
