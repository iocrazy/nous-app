"""Process cache of model context windows from the provider catalog.

``nous_models.context_window_tokens`` (migration 500) is the first layer of
:func:`app.agent_framework.context_window.resolve_model_window`; the hardcoded
``_MODEL_WINDOWS`` table is the second, ``LLM_MAX_CONTEXT_TOKENS`` the last.

Why a cache and not a query: the resolver is synchronous (called from the sync
``check_context_budget`` / ``derive_output_budget``) and runs on every step.
Modelled on ``app.services.ai.model_capabilities``, with two additions that
module lacks:

* **TTL reload** — agent turns run on the worker process while the admin edit
  lands on the gateway, so a refresh hook in the admin router alone would
  never reach the process that uses the value. The runner's async preflight
  calls :func:`ensure_catalog_windows_loaded`, which reloads once the cache is
  older than :data:`CATALOG_WINDOW_TTL_S`.
* **Keep-last-good** — a failed reload keeps the previous windows instead of
  dropping every model back to the fallback denominator.

Keys are the lower-cased ``actual_model`` (what ``ai_agents.model`` and
``agent_runs.model`` store) and the lower-cased catalog ``name``. When two
rows disagree about one key (owner-scoped rows can share an ``actual_model``)
the smaller window wins: compacting early is the safe side of a disagreement,
overflowing the provider is not.
"""

from __future__ import annotations

from time import monotonic as _clock
from typing import Any, Iterable, Mapping

from loguru import logger

from app.core.catalog_names import catalog_name_candidates

# How long a loaded catalog is trusted before the next async checkpoint
# reloads it. Bounds how long an admin edit takes to reach the worker.
CATALOG_WINDOW_TTL_S: float = 300.0

# Replaced wholesale on every load, never mutated in place — readers on the
# sync path always see one complete snapshot.
_windows: Mapping[str, int] = {}
_loaded_at: float | None = None
# ``_clock`` is bound at import on purpose: runner tests script
# ``time.monotonic`` call-by-call to drive the turn deadline, and a cache
# check that consumed one of those calls would shift their timeline.


def _reset_for_tests() -> None:
    global _windows, _loaded_at
    _windows = {}
    _loaded_at = None


def _catalog_windows_select_stmt():
    """Column-level ORM select of the catalog rows that carry a window."""
    from sqlalchemy import select

    from app.models.ai import NousModels

    return select(
        NousModels.name,
        NousModels.actual_model,
        NousModels.context_window_tokens,
    ).where(NousModels.context_window_tokens.is_not(None))


async def _fetch_catalog_rows() -> list[dict[str, Any]]:
    from app.db.session import read_scope  # deferred — matches codebase convention

    async with read_scope() as session:
        rows = (await session.execute(_catalog_windows_select_stmt())).mappings().all()
    return [dict(r) for r in rows]


def _build_index(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    index: dict[str, int] = {}
    for row in rows:
        window = row.get("context_window_tokens")
        if not isinstance(window, int) or window <= 0:
            continue
        for raw in (row.get("actual_model"), row.get("name")):
            key = (raw or "").strip().lower()
            if not key:
                continue
            index[key] = min(window, index.get(key, window))
    return index


async def refresh_catalog_windows() -> None:
    """Reload now. Called at startup and after an admin catalog edit.

    Never raises: on failure the previous snapshot stays and the attempt is
    stamped, so a dead DB is retried once per TTL rather than every step.

    Deliberately lock-free: two concurrent reloads each publish a complete
    snapshot and the last one wins, and an ``asyncio.Lock`` at module level
    would bind to whichever event loop touched it first.
    """
    global _windows, _loaded_at
    try:
        rows = await _fetch_catalog_rows()
    except Exception as exc:  # noqa: BLE001 — degrade to the table layer
        logger.warning(
            f"[catalog_windows] reload from nous_models failed: {exc!r}; "
            f"keeping {len(_windows)} cached window(s)"
        )
        _loaded_at = _clock()
        return
    _windows = _build_index(rows)
    _loaded_at = _clock()
    logger.info(f"[catalog_windows] loaded {len(_windows)} catalog window key(s)")


async def ensure_catalog_windows_loaded() -> None:
    """Load if never loaded or older than :data:`CATALOG_WINDOW_TTL_S`."""
    loaded_at = _loaded_at
    if loaded_at is not None and _clock() - loaded_at < CATALOG_WINDOW_TTL_S:
        return
    await refresh_catalog_windows()


def catalog_window(model: str | None) -> int | None:
    """The catalog's window for ``model`` (actual model id or catalog name,
    either rename spelling, case-insensitive), or ``None`` if it has none."""
    key = (model or "").strip().lower()
    if not key:
        return None
    snapshot = _windows
    for candidate in catalog_name_candidates(key):
        hit = snapshot.get(candidate)
        if hit is not None:
            return hit
    return None
