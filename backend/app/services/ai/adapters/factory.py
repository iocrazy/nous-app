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

from typing import Any, Dict, Optional

from app.services.ai.adapters.base import AIAdapter
from app.services.ai.provider_protocols import chat_provider_keys as _chat_keys
from app.services.ai.provider_protocols.base import (  # noqa: F401
    ProviderNotConfiguredError,
)

_KNOWN_PREFIXES = (
    "qwen-*, tongyi-*, deepseek-*, doubao-*, ep-*, claude-*, gpt-*, o1-*, o3-*, "
    "org/name (ModelScope)"
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


# Every provider key get_adapter_for_user can build. Used to validate an
# admin-named actual_provider before dispatching on it. DERIVED from the
# provider-protocol registry (single source of truth, 2026-07-13) — the
# contract test in tests/test_provider_protocols_contract.py fails if this
# and the registry disagree.
_PROVIDER_KEYS = _chat_keys()


def resolve_provider_key(actual_provider: str, model: str) -> str:
    """Dispatch key for a PLATFORM catalog row (``mediahub_models``).

    The admin explicitly named the provider on the row, so that wins when it
    matches a buildable adapter key. Unknown labels fall back to the
    model-prefix rule; a model no prefix rule knows (e.g. ``qwen3-6-35b``,
    2026-07-12 real-machine bug) falls back to ``qwen`` — the OpenAI-compatible
    chat-completions adapter, the SAME contract the admin health probe
    validates against the row's base_url. Never raises: a catalog row always
    carries base_url + api_key, which is all the fallback adapter needs.
    """
    key = (actual_provider or "").strip().lower()
    if key in _PROVIDER_KEYS:
        return key
    try:
        return provider_key_for_model(model)
    except ValueError:
        return "qwen"


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
    return _build_adapter_for_key(
        provider_key_for_model(model), model, user_provider_config, fallback_settings
    )


def get_adapter_for_key(
    provider_key: str,
    model: str,
    user_provider_config: Dict[str, Any],
    *,
    user_id: Optional[str] = None,
) -> AIAdapter:
    """Build an adapter for an EXPLICITLY named provider key.

    Platform catalog rows name their provider (``actual_provider``) — dispatch
    on that instead of guessing from the model prefix, which breaks for models
    like ``qwen3-6-35b`` that no prefix rule knows. ``provider_key`` must be
    one of the buildable keys (see ``resolve_provider_key``).

    ``user_id`` is NOT a credential — it is the routing target for protocols
    that dispatch per-user (``codex-local`` runs the turn on THAT user's own
    paired machine). Every other protocol ignores it. Callers with no user in
    hand may omit it; ``codex-local`` then refuses to build rather than dial
    an arbitrary daemon.
    """
    return _build_adapter_for_key(
        provider_key, model, user_provider_config, None, user_id=user_id
    )


def _build_adapter_for_key(
    provider_key: str,
    model: str,
    user_provider_config: Dict[str, Any],
    fallback_settings: Any,
    *,
    user_id: Optional[str] = None,
) -> AIAdapter:
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
            # builds the right per-provider adapter. Recurse on the SAME
            # explicit provider_key — re-deriving it from the model prefix
            # would break explicit-key callers (get_adapter_for_key).
            return _build_adapter_for_key(
                provider_key,
                model,
                {
                    **(user_provider_config or {}),
                    provider_key: {**user_cfg, "api_key": key},
                },
                fallback_settings,
                user_id=user_id,
            )

        return RotatingAdapter(rotator, _build_with_key)

    user_key = user_keys[0] if user_keys else ""
    user_base = (user_cfg.get("base_url") or "").strip()

    from app.services.ai.provider_protocols import get_chat_protocol

    protocol = get_chat_protocol(provider_key)
    return protocol.build_chat_adapter(
        model, {"api_key": user_key, "base_url": user_base}, user_id=user_id
    )
