"""Async session scopes for the SQLAlchemy 2.0 ORM layer.

Binds to the EXISTING engine (app/db/engine.py::get_engine) — same asyncpg
driver, same Supavisor transaction-mode pooler, same NullPool +
statement_cache_size=0. No new engine, no new transport.

Three scopes (see plan §2.3 / §2.4):

  read_scope()   — read-only session, no commit needed.
  write_scope()  — repo write boundary. Joins an ambient unit_of_work() if one
                   is active (that block owns the single commit); otherwise
                   opens + commits its own transaction. This is the commit
                   boundary the old fetch_one("UPDATE…RETURNING")-on-connect()
                   path silently lacked.
  unit_of_work() — service/request-scoped session+transaction. Repo writes
                   called inside it join the SAME transaction, so multi-repo
                   writes are atomic (one rollback undoes all). Default (no
                   ambient UoW) keeps per-method commits — identical to the
                   pre-ORM behaviour, safe for the 106 direct callers.

ASCII — how write_scope decides:

    repo.write()
        │
        ├─ ambient UoW set?  ──yes──▶ yield caller's session (NO nested commit;
        │                              unit_of_work() commits once at the end)
        └──no──▶ open session + begin() ──▶ yield ──▶ commit on clean exit
                                                       rollback on raise
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.engine import get_engine

# Active service/request-scoped session, if a unit_of_work() block is open on
# this task. write_scope() joins it instead of opening a competing transaction.
_request_session: ContextVar[AsyncSession | None] = ContextVar(
    "_request_session", default=None
)

_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Lazily build the async session factory bound to the existing engine.

    expire_on_commit=False: required for async — otherwise attribute access
    after commit triggers an implicit (and on AsyncSession, illegal) lazy
    refresh. We return plain dicts at the repo boundary anyway."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _sessionmaker


def dispose_sessionmaker() -> None:
    """Reset the session factory singleton so the next get_sessionmaker()
    rebuilds against a fresh engine. Call after dispose_engine() in teardown
    to keep the engine/session lifecycle symmetric (avoids a stale factory
    pointing at a disposed engine on startup→teardown→startup re-entry)."""
    global _sessionmaker
    _sessionmaker = None


@asynccontextmanager
async def read_scope() -> AsyncIterator[AsyncSession]:
    """Read-only session — no commit. Joins an ambient unit_of_work if present
    so reads see the in-flight (uncommitted) writes of the same transaction."""
    existing = _request_session.get()
    if existing is not None:
        yield existing
        return
    async with get_sessionmaker()() as session:
        yield session


@asynccontextmanager
async def write_scope() -> AsyncIterator[AsyncSession]:
    """Repo write boundary. Joins the ambient unit_of_work() if active (no
    nested commit — the UoW owns it), else opens + commits its own
    transaction. ALWAYS use this for writes; never run UPDATE/INSERT on a
    bare read_scope/connect() (that silently rolls back).

    ⚠️ Known limitation (Phase B1 review, 2026-08-04): an open-ended (and
    growing across migration batches) set of callers run
    ``session.execute(text("SET LOCAL ROLE service_role"))`` as the first
    statement in a ``write_scope()`` block, to satisfy the mig-170
    column-allowlist trigger on ``public.issues`` execution fields
    (``dbos_workflow_id`` / ``execution_locked_at`` / ``execution_state``).
    Don't rely on an enumerated list here — it will go stale again the next
    time a batch adds call sites without circling back to this docstring
    (exactly what happened between Phase B1 Task 1 and Task 2, where a
    3-caller list silently became a stale undercount once Task 2 landed).
    Get the current full set with ``grep -rn 'SET LOCAL ROLE' app/`` instead.

    ``SET LOCAL`` is scoped to the CURRENT transaction — if such a call is
    ever wrapped in an ambient ``unit_of_work()`` (this ``write_scope()``
    would then join that outer session/transaction instead of opening its
    own), the role escalation would persist for every OTHER statement in that
    same transaction until commit, not just the one write it was meant to
    guard. No current caller does this (``unit_of_work()``/
    ``maybe_unit_of_work()`` today only wraps ``inspiration/notes_service.py``,
    unrelated to the issues domain) — but a future refactor that folds one of
    these functions into a shared multi-repo transaction needs to keep this
    in mind, or move the privileged write to its own independent
    ``write_scope()`` deliberately kept OUTSIDE the shared UoW.
    """
    existing = _request_session.get()
    if existing is not None:
        # Inside a unit_of_work: reuse its session/transaction. The outer
        # block commits once; do not begin()/commit() here.
        yield existing
        return
    async with get_sessionmaker()() as session:
        async with session.begin():
            yield session


@asynccontextmanager
async def maybe_unit_of_work(enabled: bool) -> AsyncIterator[AsyncSession | None]:
    """Conditionally open a ``unit_of_work()``.

    Application-site helper for wiring atomicity into a multi-repo write path
    WITHOUT changing behaviour until the path's domain is actually on the ORM.

      enabled=True  → open a real ``unit_of_work()`` (the repo writes inside
                      share one transaction → atomic).
      enabled=False → no-op (yields None); each repo write commits on its own,
                      exactly the legacy per-method behaviour. Zero overhead —
                      NO session/connection is opened.

    Pass ``is_configured()`` for ``enabled`` so the unit-of-work activates only
    when the SQLAlchemy engine exists (opening a UoW calls ``get_engine()``;
    without the engine it would raise). The repos are ORM-backed unconditionally
    now — they use ``write_scope()`` and therefore join the ambient transaction —
    so ``is_configured()`` is the only remaining gate. With it False the path is
    a no-op (yields None) and byte-for-byte unchanged.
    """
    if enabled:
        async with unit_of_work() as session:
            yield session
    else:
        yield None


@asynccontextmanager
async def unit_of_work() -> AsyncIterator[AsyncSession]:
    """Open a service/request-scoped session + transaction. Repo writes called
    inside this block share ONE transaction (atomic: any raise rolls back all).

    Use at a service entry point (or FastAPI dependency) when several repo
    writes must commit together. Without it, each repo write commits on its
    own (legacy behaviour).

    Note: nesting unit_of_work() inside an already-active unit_of_work() opens a
    SEPARATE transaction (REQUIRES_NEW) — the inner block commits independently and
    is NOT rolled back if the outer raises. Open the UoW once at the request/service
    entry point; repo writes join it via write_scope(). (write_scope()/read_scope()
    DO join an ambient UoW; unit_of_work() itself does not.)"""
    async with get_sessionmaker()() as session:
        async with session.begin():
            token = _request_session.set(session)
            try:
                yield session
            finally:
                _request_session.reset(token)
