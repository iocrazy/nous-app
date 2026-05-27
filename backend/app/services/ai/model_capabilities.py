"""Per-model capability registry — replaces the hardcoded
_VISION_MODEL_PREFIXES whitelist in app/agent_framework/multimodal.py.

Reads ``ai_model_prices.supports_vision`` (migration 226) into a
process-local cache on first call. Subsequent calls hit the cache; the
DB roundtrip happens only at first use OR after ``refresh_capabilities()``.

Falls back to a prefix heuristic for models not in the DB, so unseeded
models stay usable. Admin can explicitly opt out of vision by inserting
a row with ``supports_vision=FALSE`` — the DB row wins over the prefix
match.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

# Cache: {(model_lower, provider_lower_or_empty): supports_vision_bool}
_cache: dict[tuple[str, str], bool] = {}
_cache_loaded: bool = False

# Legacy fallback for models not in ai_model_prices. Identical to the
# prior _VISION_MODEL_PREFIXES list in app/agent_framework/multimodal.py.
# Kept here so removing the helper's DB dependency still degrades safely.
_FALLBACK_PREFIXES = (
    "gpt-4o",
    "gpt-4-vision",
    "gpt-4-turbo",
    "claude-3",
    "claude-sonnet-4",
    "claude-opus-4",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "gemini-",
    "doubao-vision",
)


async def _fetch_capabilities() -> list[dict]:
    """Pull the capability map from ai_model_prices. One row per (model,
    provider) pair — we collapse duplicate effective_at rows by picking
    the latest. Returns a list of {model, provider, supports_vision} dicts."""
    from app.db import engine as db_engine  # deferred — matches codebase convention

    sql = (
        "SELECT DISTINCT ON (model, provider) "
        "model, provider, supports_vision "
        "FROM public.ai_model_prices "
        "ORDER BY model, provider, effective_at DESC"
    )
    rows = await db_engine.fetch_all(sql, {})
    return rows or []


def _matches_fallback_prefix(model_lower: str) -> bool:
    return any(model_lower.startswith(p) for p in _FALLBACK_PREFIXES)


async def _ensure_loaded() -> None:
    """Populate the cache from the DB once. Subsequent calls are a no-op
    until ``refresh_capabilities`` is invoked."""
    global _cache_loaded
    if _cache_loaded:
        return
    try:
        rows = await _fetch_capabilities()
    except Exception as exc:
        # DB unavailable / transient error — leave cache empty and degrade
        # to the prefix heuristic. Mark loaded so we don't pound the DB
        # on every call until refresh.
        logger.warning(
            f"[model_capabilities] failed to load from ai_model_prices: {exc}; "
            "falling back to prefix heuristic"
        )
        _cache_loaded = True
        return
    for row in rows:
        model = (row.get("model") or "").lower()
        provider = (row.get("provider") or "").lower()
        if not model:
            continue
        _cache[(model, provider)] = bool(row.get("supports_vision", False))
    _cache_loaded = True
    logger.info(f"[model_capabilities] loaded {len(_cache)} model capability rows")


async def refresh_capabilities() -> None:
    """Force a fresh DB reload. Call after admin edits the model registry."""
    global _cache_loaded
    _cache.clear()
    _cache_loaded = False
    await _ensure_loaded()


async def model_supports_vision(
    model: Optional[str], provider: Optional[str] = None
) -> bool:
    """Return True iff the model can accept image_url multipart parts.

    Lookup order:
      1. Exact (model, provider) match in the DB cache.
      2. Exact (model, "") match (provider unknown) — only used when the
         caller didn't supply provider.
      3. Unique-model fallback: if exactly one provider has this model
         and the caller didn't specify, use that row.
      4. Prefix heuristic (_FALLBACK_PREFIXES).
      5. False.
    """
    if not model:
        return False
    model_lower = model.lower()
    provider_lower = (provider or "").lower()

    await _ensure_loaded()

    # 1: (model, provider) exact match — when provider given.
    if provider_lower:
        hit = _cache.get((model_lower, provider_lower))
        if hit is not None:
            return hit
    else:
        # 2: caller didn't pass provider — try keyless first.
        hit = _cache.get((model_lower, ""))
        if hit is not None:
            return hit
        # 3: unique-provider fallback.
        matching = [v for (m, _), v in _cache.items() if m == model_lower]
        if len(matching) == 1:
            return matching[0]

    # 4 + 5: prefix heuristic, else False.
    return _matches_fallback_prefix(model_lower)
