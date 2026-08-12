"""Shared fallback-chain composer.

The ONE composition point shared by chat, summarize, and visual-analyze
(spec §2). Any semantic change here must pass all three callers' tests —
this module is not chat-specific even though its logic was originally
inlined in ``ai_library_chat_wiring.build_agent_runner_stack``.
"""

from __future__ import annotations

import os
from typing import Optional

from app.services.ai.adapters.factory import (
    get_adapter_for_key,
    get_adapter_for_user,
    resolve_provider_key,
)
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
) -> LLMFallbackChain:
    """Build the fallback-wrapped adapter chain (DB-only credentials).

    Chat resolution is unified through resolve_chat_config (Phase A4). Chat's
    governance is a pure toggle: locked → platform providers only; allowed →
    the user's BYOK ai_providers merged over them. The agent owns the model
    (primary_model); the resolver only tags the credential origin. The caller
    is responsible for resolving ``user_provider_config`` on the allowed path
    only (a locked module must skip the user BYOK read entirely).
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
        _hit = await resolve_mediahub_model(_m, "chat")
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

    def _adapter_factory(model: str):
        pre_resolved = _platform_adapters.get(model)
        if pre_resolved is not None:
            return pre_resolved
        return _get_adapter_for_user(model, user_provider_config, None)

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
