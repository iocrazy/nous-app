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
from sqlalchemy.dialects import postgresql

import app.services.infra.worker_identity as wi


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
