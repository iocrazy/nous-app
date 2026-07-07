"""Adapter factory — dispatches by model prefix to the right adapter.

Known prefixes:
- qwen-* / tongyi-* / "" (empty)           → QwenAdapter  (OpenAI-compatible)
- deepseek-*                                → DeepSeekAdapter
- doubao-* / ep-*                           → DoubaoAdapter
- claude-*                                  → ClaudeAdapter
- gpt-* / o1-* / o3-*                       → OpenAIAdapter (multimodal)
- org/name                                  → ModelScopeAdapter (BYO-only)
Unknown prefixes raise ValueError.

CREDENTIALS ARE DB-ONLY (铁律, 2026-07-07): environment variables no longer
supply LLM keys or endpoints. The resolution order everywhere is
  platform ``mediahub_models`` catalog (admin-managed, encrypted at rest)
  → user BYOK (``user_settings.ai_settings.ai_providers``)
  → ProviderNotConfiguredError.
Use :func:`app.services.ai.providers.ai_provider_helpers.resolve_db_adapter`
for the full DB-first resolution; call ``get_adapter_for_user`` directly only
when the caller has already resolved credentials into the user-config shape.
``get_adapter(model, settings)`` survives as a deprecated shim (the settings
argument is ignored) so legacy call sites fail fast instead of silently
reading env.
"""

from __future__ import annotations

from typing import Any, Dict

from app.services.ai.adapters.base import AIAdapter
from app.services.ai.adapters.claude import ClaudeAdapter
from app.services.ai.adapters.deepseek import DeepSeekAdapter
from app.services.ai.adapters.doubao import DoubaoAdapter
from app.services.ai.adapters.modelscope import ModelScopeAdapter
from app.services.ai.adapters.openai import OpenAIAdapter
from app.services.ai.adapters.qwen import QwenAdapter

_KNOWN_PREFIXES = (
    "qwen-*, tongyi-*, deepseek-*, doubao-*, ep-*, claude-*, gpt-*, o1-*, o3-*, "
    "org/name (ModelScope)"
)

# Official public endpoints — an ENDPOINT is not a credential, so these may
# live in code. Keys never may.
DEEPSEEK_DEFAULT_URL = "https://api.deepseek.com/v1/chat/completions"
DOUBAO_DEFAULT_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"


class ProviderNotConfiguredError(ValueError):
    """No credential resolved for the provider serving ``model``.

    Raised instead of building a keyless adapter (opaque upstream 401) or —
    the pre-2026-07-07 behavior — silently falling back to env vars. Users
    configure their own keys in Settings → AI Providers; platform models are
    managed by the admin in Admin → AI Models.
    """

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model
        super().__init__(
            f"AI provider '{provider}' is not configured for model {model!r}. "
            "Add your API key in Settings → AI Providers, or ask the admin "
            "to enable a platform model (Admin → AI Models)."
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
    # ModelScope model IDs are ``org/name`` — the slash is the
    # discriminator, and it must be checked BEFORE any prefix rule
    # (``deepseek-ai/DeepSeek-V3`` starts with ``deepseek-``).
    if "/" in m:
        return "modelscope"
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
    """DEPRECATED shim — ``settings`` is IGNORED (env credentials retired
    2026-07-07, 铁律). Delegates to :func:`get_adapter_for_user` with no user
    config: callers that never resolved DB credentials now fail fast with
    ProviderNotConfiguredError instead of silently reading env vars. Migrate
    call sites to ``resolve_db_adapter`` (ai_provider_helpers)."""
    return get_adapter_for_user(model, {}, None)


def get_adapter_for_user(
    model: str,
    user_provider_config: Dict[str, Any],
    fallback_settings: Any,
) -> AIAdapter:
    """Build an adapter using per-user BYO credentials with a global fallback.

    ``user_provider_config`` is the user's full ``ai_providers`` dict, e.g.
    ``{"qwen": {"api_key": "...", "base_url": "..."}, "doubao": {...}}`` —
    OR a platform-credential dict in the same shape, as produced by
    ``resolve_db_adapter`` from the ``mediahub_models`` catalog.

    ``fallback_settings`` is DEPRECATED AND IGNORED (env credentials retired
    2026-07-07, 铁律): a provider with no resolved key raises
    :class:`ProviderNotConfiguredError` instead of reading env vars.

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
        if not user_key:
            raise ProviderNotConfiguredError("claude", model)
        return ClaudeAdapter(
            api_key=user_key,
            default_model=model or "claude-opus-4-5",
        )

    if provider_key == "deepseek":
        if not user_key:
            raise ProviderNotConfiguredError("deepseek", model)
        return DeepSeekAdapter(
            api_url=user_base or DEEPSEEK_DEFAULT_URL,
            api_key=user_key,
            default_model=model or "deepseek-chat",
        )

    if provider_key == "doubao":
        if not user_key:
            raise ProviderNotConfiguredError("doubao", model)
        return DoubaoAdapter(
            api_url=user_base or DOUBAO_DEFAULT_URL,
            api_key=user_key,
            default_model=model,
        )

    if provider_key == "openai":
        if not user_key:
            raise ProviderNotConfiguredError("openai", model)
        return OpenAIAdapter(
            api_key=user_key,
            default_model=model or "gpt-4o",
            api_url=user_base or None,
        )

    if provider_key == "modelscope":
        # BYO-only provider — no global MODELSCOPE_* settings exist, so a
        # missing user config builds a keyless adapter (request then fails
        # with ModelScope's own auth error instead of an AttributeError).
        from app.services.ai.adapters.modelscope import MODELSCOPE_DEFAULT_URL

        return ModelScopeAdapter(
            api_url=user_base or MODELSCOPE_DEFAULT_URL,
            api_key=user_key,
            default_model=model,
        )

    # provider_key == "qwen" (default / only remaining case). Qwen-compatible
    # endpoints have no universal public URL — the base_url IS part of the
    # credential set, so it must come from the DB (platform catalog or BYOK).
    if not user_base:
        raise ProviderNotConfiguredError("qwen", model)
    return QwenAdapter(
        api_url=user_base,
        api_key=user_key,
        default_model=model,
    )
