"""SQLAlchemy 2.0 async engine over the asyncpg driver → Supavisor.

The Issue #199 backend data-access target: SQLAlchemy Core (composable,
injection-safe query building) on top of the asyncpg driver — replacing
both supabase-py REST (httpcore CLOSE_WAIT leak, 2026-05-22 incident) and
the raw-asyncpg ``pg_pool``. One engine, reused process-wide.

Why these settings (Supavisor transaction-pooling — same constraints the
raw asyncpg pool already documents):
  * ``NullPool`` — Supavisor *is* the pool. A second client-side pool over
    a transaction-mode pooler causes "prepared statement does not exist"
    when a checked-out connection lands on a different PG backend.
  * ``statement_cache_size=0`` on the asyncpg driver — disable client-side
    prepared-statement caching for the same reason.

This is the SQLAlchemy-layered successor to the retired raw-asyncpg
``pg_pool.py`` — the sole asyncpg connection layer in the backend.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

_engine: Optional[AsyncEngine] = None


def is_configured() -> bool:
    """True when the Supavisor DSN is set. Callers fall back to the legacy
    supabase-py path otherwise (and the repo factories use this to pick the
    asyncpg variant)."""
    return bool(settings.SUPAVISOR_DATABASE_URL)


def _sqla_url() -> str:
    """Coerce the asyncpg DSN to a SQLAlchemy async URL (``postgresql+asyncpg://``)."""
    dsn = settings.SUPAVISOR_DATABASE_URL
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    if dsn.startswith("postgresql://"):
        return "postgresql+asyncpg://" + dsn[len("postgresql://") :]
    if dsn.startswith("postgres://"):
        return "postgresql+asyncpg://" + dsn[len("postgres://") :]
    return dsn


def get_engine() -> AsyncEngine:
    """Return the singleton async engine, created on first use.

    ``create_async_engine`` is synchronous and connects lazily, so this
    needs no event loop. Raises ``RuntimeError`` if the DSN isn't set
    (caller is expected to fall back during the migration window).
    """
    global _engine
    if _engine is not None:
        return _engine
    if not is_configured():
        raise RuntimeError(
            "SUPAVISOR_DATABASE_URL is not configured — SQLAlchemy engine "
            "is disabled. Set the env var or use the legacy path."
        )
    # ⚠️ NullPool is LOAD-BEARING for run_async + ORM safety — do NOT swap it
    # for a real pool (QueuePool / AsyncAdaptedQueuePool) without rearchitecting.
    # asyncpg connections are event-loop-bound. `run_async` (app/tasks/utils.py)
    # spins a FRESH event loop per call (the sync→async bridge used by DBOS sync
    # steps). With NullPool each checkout opens a brand-new asyncpg connection on
    # the CURRENT loop and closes it on release → nothing loop-bound survives, so
    # the process-wide singleton engine is safe across those fresh loops. A real
    # pool would retain a connection created on one run_async loop and hand it to
    # a later, different loop → "got Future attached to a different loop" (SEV-1).
    # Supavisor is the real server-side pool; this client pool stays Null.
    _engine = create_async_engine(
        _sqla_url(),
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0},
        echo=False,
    )
    logger.info("[engine] SQLAlchemy async engine ready (NullPool + asyncpg)")
    return _engine


async def dispose_engine() -> None:
    """Drain the engine. FastAPI shutdown hook calls this so we don't leak
    connections at restart. Best-effort; never raises."""
    global _engine
    if _engine is None:
        return
    logger.info("[engine] disposing SQLAlchemy async engine")
    try:
        await _engine.dispose()
    except Exception:  # noqa: BLE001
        logger.exception("[engine] dispose raised — proceeding")
    finally:
        _engine = None


# ── Query convenience helpers (SQLAlchemy Core, :name params) ──────────
# Use named (:name) bind params, NOT asyncpg's $1 positional. Reads run on
# engine.connect() (no txn); writes on engine.begin() (auto-commit). These
# centralize the connect/begin + row→dict + rowcount handling so callers
# don't re-implement it. repository_base will delegate here once migrated.


async def fetch_all(sql: str, params: Optional[dict] = None) -> list[dict]:
    """SELECT → list of plain dicts."""
    from sqlalchemy import text

    eng = get_engine()
    async with eng.connect() as conn:
        result = await conn.execute(text(sql), params or {})
        return [dict(r) for r in result.mappings().all()]


async def fetch_one(sql: str, params: Optional[dict] = None) -> Optional[dict]:
    """SELECT → first row as a plain dict, or None."""
    from sqlalchemy import text

    eng = get_engine()
    async with eng.connect() as conn:
        result = await conn.execute(text(sql), params or {})
        row = result.mappings().first()
        return dict(row) if row else None


async def fetch_val(sql: str, params: Optional[dict] = None) -> Any:
    """SELECT one scalar (first column of first row), or None."""
    from sqlalchemy import text

    eng = get_engine()
    async with eng.connect() as conn:
        return (await conn.execute(text(sql), params or {})).scalar()


async def execute(sql: str, params: Optional[dict] = None) -> int:
    """INSERT / UPDATE / DELETE inside an auto-committing transaction.
    Returns the affected row count."""
    from sqlalchemy import text

    eng = get_engine()
    async with eng.begin() as conn:
        result = await conn.execute(text(sql), params or {})
        return result.rowcount


async def execute_as_service_role(sql: str, params: Optional[dict] = None) -> int:
    """Like ``execute`` but runs the statement with ``current_user =
    service_role`` (``SET LOCAL ROLE service_role`` inside the txn).

    Required for writes guarded by column-allowlist triggers that only
    service_role may perform — e.g. ``public.issues`` execution fields
    (``execution_locked_at`` / ``dbos_workflow_id`` / ``execution_state``),
    enforced by the ``issues_update_allowlist`` trigger (migration 170).
    Restores the ``SET ROLE service_role`` behavior the raw-psycopg DBOS path
    had before the SQLAlchemy-engine migration (#340) dropped it. The engine
    connects as ``postgres``, which is a member of ``service_role``, so the
    SET LOCAL succeeds; it is transaction-scoped and auto-resets on commit.
    """
    from sqlalchemy import text

    eng = get_engine()
    async with eng.begin() as conn:
        # SET LOCAL ROLE takes a role identifier, not a bound parameter; the
        # value is a fixed literal (no user input), so there is no injection.
        await conn.execute(text("SET LOCAL ROLE service_role"))
        result = await conn.execute(text(sql), params or {})
        return result.rowcount


async def execute_returning_val(sql: str, params: Optional[dict] = None) -> Any:
    """INSERT / UPDATE ... RETURNING <col> inside an auto-committing
    transaction → the first scalar of the RETURNING row, or None.

    Use this (not ``fetch_val``) when the statement writes: ``fetch_val``
    runs on ``engine.connect()`` and never commits, so an
    ``INSERT ... RETURNING`` there would roll back on connection close.
    """
    from sqlalchemy import text

    eng = get_engine()
    async with eng.begin() as conn:
        return (await conn.execute(text(sql), params or {})).scalar()


async def execute_returning_one(
    sql: str, params: Optional[dict] = None
) -> Optional[dict]:
    """INSERT / UPDATE ... RETURNING * inside an auto-committing transaction →
    the first RETURNING row as a plain dict, or None. Use for writes that need
    the inserted row back (fetch_one would run on connect() and never commit)."""
    from sqlalchemy import text

    eng = get_engine()
    async with eng.begin() as conn:
        result = await conn.execute(text(sql), params or {})
        row = result.mappings().first()
        return dict(row) if row else None


__all__ = [
    "dispose_engine",
    "execute",
    "execute_returning_one",
    "execute_returning_val",
    "fetch_all",
    "fetch_one",
    "fetch_val",
    "get_engine",
    "is_configured",
]
