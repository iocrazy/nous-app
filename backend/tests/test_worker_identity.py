"""Worker Foundation P1 — worker_identity helpers (observe-only).

boot_generation is a stable per-process fencing token; the registry writers are
best-effort and must never raise into the sweeper tick.

Phase B5 Task 1: upsert_registry/stale_executor_ids were migrated from a
dependency-injected ``db_engine`` (still accepted — unused — for caller
compatibility) to the SQLAlchemy ORM (``app.db.session.read_scope``/
``write_scope``). Tests patch those scopes and assert against the COMPILED
statement rather than a fake engine's captured SQL string.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import insert as _sa_insert
from sqlalchemy import select as _sa_select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker as _async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine

import app.services.infra.worker_identity as wi
from app.models import t_worker_registry


def test_boot_generation_is_stable_uuid():
    a = wi.boot_generation()
    b = wi.boot_generation()
    assert a == b, "boot_generation must be stable for the process lifetime"
    uuid.UUID(a)  # raises if not a valid uuid


def test_current_executor_id_is_a_role_value():
    from app.agent_framework.role import ProcessRole

    eid = wi.current_executor_id()
    assert eid in {r.value for r in ProcessRole}


# ── resolve_executor_id: HA per-replica identity (flag-gated) ───────────


def test_resolve_executor_id_flag_off_is_role_name(monkeypatch):
    from app.agent_framework.role import ProcessRole

    monkeypatch.delenv("FEATURE_MULTI_WORKER_ID", raising=False)
    monkeypatch.setenv("WORKER_REPLICA_INDEX", "3")  # ignored while flag off
    assert wi.resolve_executor_id(ProcessRole.WORKER) == "worker"
    assert wi.resolve_executor_id(ProcessRole.GATEWAY) == "gateway"
    assert wi.resolve_executor_id(ProcessRole.COMBINED) == "combined"


def test_resolve_executor_id_flag_on_worker_gets_replica(monkeypatch):
    from app.agent_framework.role import ProcessRole

    monkeypatch.setenv("FEATURE_MULTI_WORKER_ID", "true")
    # Lone worker (no index env) → stable 'worker-0'.
    monkeypatch.delenv("WORKER_REPLICA_INDEX", raising=False)
    assert wi.resolve_executor_id(ProcessRole.WORKER) == "worker-0"
    # Numbered replica.
    monkeypatch.setenv("WORKER_REPLICA_INDEX", "1")
    assert wi.resolve_executor_id(ProcessRole.WORKER) == "worker-1"


def test_resolve_executor_id_flag_on_leaves_gateway_and_combined(monkeypatch):
    from app.agent_framework.role import ProcessRole

    monkeypatch.setenv("FEATURE_MULTI_WORKER_ID", "true")
    monkeypatch.setenv("WORKER_REPLICA_INDEX", "2")
    # Only the worker role is per-replica; gateway/combined are singletons.
    assert wi.resolve_executor_id(ProcessRole.GATEWAY) == "gateway"
    assert wi.resolve_executor_id(ProcessRole.COMBINED) == "combined"


def test_multi_worker_enabled_tracks_flag(monkeypatch):
    monkeypatch.delenv("FEATURE_MULTI_WORKER_ID", raising=False)
    assert wi.multi_worker_enabled() is False
    for on in ("1", "true", "yes", "on", "TRUE"):
        monkeypatch.setenv("FEATURE_MULTI_WORKER_ID", on)
        assert wi.multi_worker_enabled() is True
    for off in ("0", "false", "no", ""):
        monkeypatch.setenv("FEATURE_MULTI_WORKER_ID", off)
        assert wi.multi_worker_enabled() is False


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows if rows is not None else []

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _RecordingSession:
    def __init__(self, result: _FakeResult | None = None, raise_exc=None) -> None:
        self.calls: list[Any] = []
        self._result = result or _FakeResult()
        self._raise = raise_exc

    async def execute(self, stmt: Any) -> _FakeResult:
        if self._raise is not None:
            raise self._raise
        self.calls.append(stmt)
        return self._result


def _patch_scope(monkeypatch, attr: str, result=None, raise_exc=None):
    session = _RecordingSession(result, raise_exc)

    @asynccontextmanager
    async def fake_scope():
        yield session

    monkeypatch.setattr(f"app.services.infra.worker_identity.{attr}", fake_scope)
    return session


@pytest.mark.asyncio
async def test_upsert_registry_writes_and_returns_true(monkeypatch):
    session = _patch_scope(monkeypatch, "write_scope")
    ok = await wi.upsert_registry(db_engine=None)
    assert ok is True

    assert len(session.calls) == 1
    sql, binds = _compile(session.calls[0])
    assert "INSERT INTO public.worker_registry" in sql
    assert "ON CONFLICT (executor_id) DO UPDATE SET" in sql
    assert "heartbeat_at = now()" in sql
    assert "updated_at = now()" in sql
    assert binds["executor_id"] == wi.current_executor_id()
    assert binds["boot_generation"] == wi.boot_generation()


@pytest.mark.asyncio
async def test_upsert_registry_swallows_errors_returns_false(monkeypatch):
    # Table missing pre-migration / pool saturated must NOT raise into the tick.
    _patch_scope(monkeypatch, "write_scope", raise_exc=RuntimeError("pool exhausted"))
    ok = await wi.upsert_registry(db_engine=None)
    assert ok is False


@pytest.mark.asyncio
async def test_stale_executor_ids_returns_names(monkeypatch):
    rows = [{"executor_id": "worker"}, {"executor_id": "w2"}]
    session = _patch_scope(monkeypatch, "read_scope", _FakeResult(rows))
    stale = await wi.stale_executor_ids(None, 360)
    assert stale == ["worker", "w2"]

    _sql, binds = _compile(session.calls[0])
    # app-side cutoff (no server-side make_interval — see docstring) bound as
    # a plain timestamptz literal a little over 360s in the past.
    cutoff = binds["heartbeat_at_1"]
    now = datetime.now(timezone.utc)
    assert now - cutoff >= timedelta(seconds=360)
    assert now - cutoff < timedelta(seconds=365)


@pytest.mark.asyncio
async def test_stale_executor_ids_swallows_errors_returns_empty(monkeypatch):
    _patch_scope(monkeypatch, "read_scope", raise_exc=RuntimeError("db down"))
    assert await wi.stale_executor_ids(None, 360) == []


# ── Real-aiosqlite row-shape regression (B5 review leftover — deferred
# minors batch, Minor 4) ─────────────────────────────────────────────────
#
# test_stale_executor_ids_returns_names above only ever fakes the Result
# (_FakeResult), so the B4 row-shape bug class has no coverage for
# _stale_executor_ids_stmt's own fetch->consume chain (``.mappings().all()``
# -> ``r["executor_id"]`` in stale_executor_ids). Mirrors
# tests/test_orm_b5_task1_row_shape_e2e.py's positive/negative-control pair
# with a genuine aiosqlite engine.

_WORKER_REGISTRY_DDL = """
CREATE TABLE worker_registry (
    executor_id TEXT NOT NULL, boot_generation TEXT NOT NULL,
    app_version TEXT, pid INTEGER, started_at TIMESTAMP,
    heartbeat_at TIMESTAMP, updated_at TIMESTAMP
)
"""


async def _seeded_worker_registry_engine(*, heartbeat_at):
    engine = _create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_WORKER_REGISTRY_DDL)
        await conn.execute(
            _sa_insert(t_worker_registry).values(
                executor_id="worker-dead",
                boot_generation=uuid.uuid4(),
                pid=12345,
                heartbeat_at=heartbeat_at,
            )
        )
    return engine


@pytest.mark.asyncio
async def test_stale_executor_ids_stmt_yields_column_keyed_row_against_real_sqlite():
    """The REAL production statement (``wi._stale_executor_ids_stmt``,
    imported — not reconstructed here) round-tripped through a genuine
    aiosqlite ``Result`` gives a column-keyed RowMapping matching
    ``stale_executor_ids``'s ``r["executor_id"]`` read."""
    stale_heartbeat = datetime.now(timezone.utc) - timedelta(hours=1)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    engine = await _seeded_worker_registry_engine(heartbeat_at=stale_heartbeat)

    sessionmaker = _async_sessionmaker(
        engine, class_=_AsyncSession, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            rows = (
                (await session.execute(wi._stale_executor_ids_stmt(cutoff)))
                .mappings()
                .all()
            )
    finally:
        await engine.dispose()

    assert len(rows) == 1
    assert rows[0]["executor_id"] == "worker-dead"  # matches stale_executor_ids' read


@pytest.mark.asyncio
async def test_stale_executor_ids_stmt_entity_level_negative_control_proves_sensitivity():
    """Negative control: selecting the bare ``t_worker_registry`` Table
    (whole-row, still column-keyed since it's a Core ``Table`` not a mapped
    entity) vs. hypothetically mis-labeling the column would both still be
    column-keyed for a Core Table select — so instead this proves sensitivity
    the OTHER way a single-column select can silently break: selecting a
    DIFFERENT column name than ``executor_id`` produces a row that raises
    exactly the ``KeyError`` ``stale_executor_ids``'s ``r["executor_id"]``
    would hit if the statement's selected column were ever wrong."""
    stale_heartbeat = datetime.now(timezone.utc) - timedelta(hours=1)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    engine = await _seeded_worker_registry_engine(heartbeat_at=stale_heartbeat)

    sessionmaker = _async_sessionmaker(
        engine, class_=_AsyncSession, expire_on_commit=False
    )
    try:
        bad_stmt = _sa_select(t_worker_registry.c.pid).where(
            t_worker_registry.c.heartbeat_at < cutoff
        )
        async with sessionmaker() as session:
            bad_rows = (await session.execute(bad_stmt)).mappings().all()
    finally:
        await engine.dispose()

    assert len(bad_rows) == 1
    with pytest.raises(KeyError):
        bad_rows[0]["executor_id"]  # proves the test above is column-name-sensitive
