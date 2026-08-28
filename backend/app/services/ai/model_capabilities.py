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

# Platform-catalog providers whose models accept images despite having no
# ``ai_model_prices`` row and matching no prefix below. ``codex exec --image``
# is a real capability of the user's local CLI, so answering False here does
# not "degrade safely" — it silently strips the images off the request before
# the adapter ever sees them (spec 2026-08-27 真链验收第 4 项).
#
# codex-local ONLY. ``jimeng-local`` is deliberately absent: it is an image
# GENERATOR, not a chat model, so it never reaches this gate, and claiming
# "accepts image input" for it would be a different claim entirely.
_LOCAL_VISION_PROVIDERS = frozenset({"codex-local"})

# Lowercased ``mediahub_models.name`` values served by one of those providers.
# Cached beside _cache so the common path stays zero extra roundtrips — the
# gate runs once per chat turn, and a per-turn catalog SELECT for EVERY model
# would be a real cost paid by every user to fix one provider.
_local_vision_models: set[str] = set()

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
    # Doubao Seed 1.6/2.0 are natively multimodal VLMs (text+image input).
    "doubao-seed",
)


def _capabilities_select_stmt():
    """The ai_model_prices capability-map statement itself, column-level (not
    entity-level — the B4 row-shape lesson) so ``_ensure_loaded``'s
    ``row.get("model")``/``row.get("provider")``/``row.get("supports_vision")``
    reads below get real column values. DISTINCT ON (model, provider) picks
    the latest ``effective_at`` row per pair — Postgres-only syntax; other
    dialects (e.g. the sqlite row-shape test) silently degrade to a plain
    DISTINCT over the selected columns, which is fine for a row-SHAPE check
    (not a semantics check of "latest per pair"). Factored out so a real-
    aiosqlite row-shape test can import and exercise the exact production
    statement."""
    from sqlalchemy import select

    from app.models import AiModelPrices

    return (
        select(
            AiModelPrices.model,
            AiModelPrices.provider,
            AiModelPrices.supports_vision,
        )
        .distinct(AiModelPrices.model, AiModelPrices.provider)
        .order_by(
            AiModelPrices.model,
            AiModelPrices.provider,
            AiModelPrices.effective_at.desc(),
        )
    )


async def _fetch_capabilities() -> list[dict]:
    """Pull the capability map from ai_model_prices. One row per (model,
    provider) pair — we collapse duplicate effective_at rows by picking
    the latest. Returns a list of {model, provider, supports_vision} dicts."""
    from app.db.session import read_scope  # deferred — matches codebase convention

    async with read_scope() as session:
        rows = (await session.execute(_capabilities_select_stmt())).mappings().all()
    return [dict(r) for r in rows]


def _matches_fallback_prefix(model_lower: str) -> bool:
    return any(model_lower.startswith(p) for p in _FALLBACK_PREFIXES)


async def _fetch_local_vision_models() -> set[str]:
    """Names of the catalog rows whose provider runs on the user's own machine
    and accepts images. ORM select, mirroring _capabilities_select_stmt (no raw
    SQL, 2026-08-04 立约).

    Not filtered on ``is_enabled``: a disabled row can't be picked as a model
    in the first place, so filtering here would only add a way for the two
    caches to disagree.
    """
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models.ai import MediahubModels

    stmt = select(MediahubModels.name).where(
        MediahubModels.actual_provider.in_(tuple(_LOCAL_VISION_PROVIDERS))
    )
    async with read_scope() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return {(n or "").lower() for n in rows if n}


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

    # Its own try: a failure here must not cost us the capability map that
    # already loaded above, and vice versa.
    try:
        _local_vision_models.update(await _fetch_local_vision_models())
    except Exception as exc:  # noqa: BLE001 — degrade to the prefix heuristic
        logger.warning(
            f"[model_capabilities] failed to load local-vision catalog rows: "
            f"{exc}; local models will be treated as text-only"
        )

    _cache_loaded = True
    logger.info(
        f"[model_capabilities] loaded {len(_cache)} model capability rows, "
        f"{len(_local_vision_models)} local-vision catalog rows"
    )


async def refresh_capabilities() -> None:
    """Force a fresh DB reload. Call after admin edits the model registry."""
    global _cache_loaded
    _cache.clear()
    _local_vision_models.clear()
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
      4. Catalog rows served by a local vision provider (_LOCAL_VISION_PROVIDERS).
      5. Prefix heuristic (_FALLBACK_PREFIXES).
      6. False.

    Step 4 sits AFTER the explicit lookups on purpose: an admin who inserts an
    ``ai_model_prices`` row with ``supports_vision=FALSE`` is still opting that
    model out, exactly as the module docstring promises. It sits BEFORE the
    prefix heuristic because a catalog name like ``Codex (Local)`` matches no
    prefix and would otherwise return False.
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

    # 4: a catalog row whose provider runs on the user's own machine.
    if model_lower in _local_vision_models:
        return True

    # 5 + 6: prefix heuristic, else False.
    return _matches_fallback_prefix(model_lower)
