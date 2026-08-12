"""Shared fallback-chain composer.

The ONE composition point shared by chat, summarize, and visual-analyze
(spec §2). Any semantic change here must pass all three callers' tests —
this module is not chat-specific even though its logic was originally
inlined in ``ai_library_chat_wiring.build_agent_runner_stack``.
"""

from __future__ import annotations

import os
from typing import Optional

from app.services.ai.adapters.base import AIAdapter
from app.services.ai.adapters.factory import (
    get_adapter_for_key,
    get_adapter_for_user,
    provider_key_for_model,
    resolve_provider_key,
)
from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter
from app.services.ai.llm.llm_fallback_chain import LLMFallbackChain


async def resolve_mediahub_model(model_name: str, module: str):
    """Deferred re-export of :func:`ai_provider_helpers.resolve_mediahub_model`.

    Re-imports the source function on every call instead of binding it once
    at module-import time. Two reasons this matters, both bitten in practice:
    1. legacy chat-wiring tests patch the SOURCE module attribute
       (``app.services.ai.providers.ai_provider_helpers.resolve_mediahub_model``),
       expecting each call to observe whatever is currently patched there;
    2. a plain top-level ``from ... import resolve_mediahub_model`` freezes
       whatever value was active the FIRST time this module got imported
       (which, in a shared test process, may be a PREVIOUS test's mock) —
       every later call would silently keep using that stale mock instead
       of the real function or the CURRENT test's patch. This module-level
       name is still directly patchable via
       ``unittest.mock.patch.object(fallback_wiring, "resolve_mediahub_model", ...)``
       for this module's own tests and Task 2/3 callers.
    """
    from app.services.ai.providers.ai_provider_helpers import (
        resolve_mediahub_model as _impl,
    )

    return await _impl(model_name, module)


def llm_total_deadline_s() -> Optional[float]:
    """AI-007: chain-wide wall-time ceiling for LLM retry + fallback.

    Without it, primary retries + each fallback's retries + backoffs can spin
    7-15 min on a flaky upstream. Default 120s bounds the worst case while
    staying well above a normal multi-attempt recovery. ``LLM_TOTAL_DEADLINE_S=0``
    disables it (legacy unbounded behavior).
    """
    raw = os.getenv("LLM_TOTAL_DEADLINE_S", "120")
    try:
        val = float(raw)
    except ValueError:
        return 120.0
    return None if val <= 0 else val


async def build_fallback_llm(
    *,
    primary_model: str,
    fallback_models: list[str],
    user_provider_config: Optional[dict],
    provider_key: Optional[str] = None,
    module: str = "chat",
) -> LLMFallbackChain:
    """Build the fallback-wrapped adapter chain (DB-only credentials).

    Chat resolution is unified through resolve_chat_config (Phase A4). Chat's
    governance is a pure toggle: locked → platform providers only; allowed →
    the user's BYOK ai_providers merged over them. The agent owns the model
    (primary_model); the resolver only tags the credential origin. The caller
    is responsible for resolving ``user_provider_config`` on the allowed path
    only (a locked module must skip the user BYOK read entirely).

    ``provider_key`` / ``module`` (final-review C1/I1 fix, 2026-08-11): chat
    passes neither (module defaults ``"chat"``, matching legacy behavior
    byte-for-byte) and ``user_provider_config`` stays the FULL provider-KEYED
    dict (``{"qwen": {...}, "doubao": {...}}``) that ``get_adapter_for_user``
    expects.

    Summarize / visual-analyze resolve credentials differently: their
    ``user_provider_config`` is a NARROWED FLAT single-provider dict
    (``{"model", "api_key", "base_url", "app_id"}``) — handing that straight
    to ``get_adapter_for_user`` makes its ``.get(provider_key, {})`` always
    find an empty dict (the flat dict has no provider-name keys), silently
    dropping real credentials. Those two callers pass ``provider_key``
    (their resolved provider, possibly ``""`` when unresolved — the *passing
    of the kwarg itself*, not its truthiness, is the signal; ``None`` stays
    reserved for "not a batch caller") and their MODULE'S governance key
    (``"summarization"`` / ``"visual_analysis"``) so the platform-catalog
    pre-resolution below gates against the right module instead of a
    hardcoded ``"chat"``. When the sentinel fires, the adapter factory wraps
    the flat config under the (explicit-or-derived) provider key and, on an
    unknown model prefix, degrades to a generic
    :class:`OpenAICompatibleAdapter` — mirroring the deleted
    ``SummarizeService``/``VisualAnalysisService._build_adapter`` (see
    ``git show d48cd956~1`` for the pre-fallback-chain original) faithfully,
    so a primary whose provider the factory can't derive doesn't get treated
    as ``adapter_init_failed`` and skipped straight to a fallback model.
    """
    # Pre-resolve every model the fallback chain may dial against the platform
    # ``mediahub_models`` catalog (async — the factory below must stay sync for
    # LLMFallbackChain). A catalog hit is served by admin-managed credentials
    # under the catalog's ``actual_model``; a found-but-disabled/gated model
    # raises here (fail-closed) instead of silently 401-ing through BYOK.
    # Credentials are DB-only (铁律 2026-07-07): a miss on both the catalog and
    # the BYOK/platform-provider dict raises ProviderNotConfiguredError at dial
    # time — there is no env fallback anymore.
    _platform_adapters: dict = {}
    for _m in dict.fromkeys([primary_model, *fallback_models]):
        _hit = await resolve_mediahub_model(_m, module)
        if _hit:
            _prov, _pcfg, _actual = _hit
            _creds = {"api_key": _pcfg["api_key"], "base_url": _pcfg["base_url"]}
            # Dispatch on the row's admin-named actual_provider (#1279
            # contract) — a prefix guess on actual_model raises for catalog
            # models like ``qwen3-6-35b`` and killed EVERY chat turn at
            # stack-build time (prod 2026-07-06→13).
            _key = resolve_provider_key(_prov, _actual)
            _platform_adapters[_m] = get_adapter_for_key(_key, _actual, {_key: _creds})

    # Captured as a local now (not re-looked-up from the module global at
    # call time) so the returned chain's ``adapter_factory`` keeps working
    # correctly regardless of when the caller invokes it relative to this
    # coroutine's own execution window (e.g. a test's mock.patch context).
    _get_adapter_for_user = get_adapter_for_user
    # ``provider_key is not None`` — NOT truthiness — is the batch-style
    # signal: summarize/visual always pass the kwarg (even "" when their own
    # resolution left it unset), chat never passes it at all. See docstring.
    _is_flat_config = provider_key is not None
    _flat_config: dict = dict(user_provider_config or {}) if _is_flat_config else {}
    _explicit_provider_key = (provider_key or "").strip()

    def _flat_degrade(model: str) -> AIAdapter:
        return OpenAICompatibleAdapter(
            api_url=_flat_config.get("base_url", "") or "",
            api_key=_flat_config.get("api_key", "") or "",
            default_model=model,
        )

    def _adapter_factory(model: str):
        pre_resolved = _platform_adapters.get(model)
        if pre_resolved is not None:
            return pre_resolved
        if not _is_flat_config:
            return _get_adapter_for_user(model, user_provider_config, None)

        # Batch style (summarize/visual-analyze): resolve THIS attempt's
        # provider key (explicit if the service was given one, else derived
        # per-model — a fallback model can sit on a different provider than
        # the primary), wrap the flat config under it, and degrade to a
        # generic OpenAI-compatible adapter rather than raise on an unknown
        # prefix / ValueError (mirrors the deleted ``_build_adapter``).
        key = _explicit_provider_key
        if not key and model:
            try:
                key = provider_key_for_model(model)
            except ValueError:
                key = ""
        if not key:
            return _flat_degrade(model)
        scoped = {
            key: {
                "api_key": _flat_config.get("api_key", ""),
                "base_url": _flat_config.get("base_url", "") or "",
                "app_id": _flat_config.get("app_id", ""),
            }
        }
        try:
            return _get_adapter_for_user(model, scoped, None)
        except ValueError:
            return _flat_degrade(model)

    # P1-5: pull the per-process ModelHealthRegistry off app.state if
    # available so cooled-down models are skipped on subsequent calls.
    # No registry → legacy linear behavior (try every model in order).
    health_registry = None
    try:
        from app.main import app as _app  # late import to avoid cycle

        health_registry = getattr(_app.state, "model_health", None)
    except Exception:
        health_registry = None

    return LLMFallbackChain(
        primary_model=primary_model,
        fallback_models=fallback_models,
        adapter_factory=_adapter_factory,
        health_registry=health_registry,
        total_deadline_seconds=llm_total_deadline_s(),
    )


__all__ = ["build_fallback_llm", "llm_total_deadline_s"]
