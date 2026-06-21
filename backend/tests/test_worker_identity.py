"""Worker Foundation P1 — worker_identity helpers (observe-only).

boot_generation is a stable per-process fencing token; the registry writers are
best-effort and must never raise into the sweeper tick. No DB — the writers'
SQL is captured via a fake engine.
"""

import uuid

import pytest

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


class _FakeEngine:
    def __init__(self, *, fetch_rows=None, raise_on=None):
        self.executed: list[str] = []
        self.fetched: list[str] = []
        self._fetch_rows = fetch_rows or []
        self._raise_on = raise_on  # "execute" | "fetch" | None

    async def execute(self, sql, params=None):
        if self._raise_on == "execute":
            raise RuntimeError("pool exhausted")
        self.executed.append(sql)
        return None

    async def fetch_all(self, sql, params=None):
        if self._raise_on == "fetch":
            raise RuntimeError("db down")
        self.fetched.append(sql)
        return self._fetch_rows


@pytest.mark.asyncio
async def test_upsert_registry_writes_and_returns_true():
    eng = _FakeEngine()
    ok = await wi.upsert_registry(eng)
    assert ok is True
    sql = " ".join(eng.executed)
    assert "INSERT INTO public.worker_registry" in sql
    assert "ON CONFLICT (executor_id) DO UPDATE" in sql
    assert "heartbeat_at = now()" in sql


@pytest.mark.asyncio
async def test_upsert_registry_swallows_errors_returns_false():
    # Table missing pre-migration / pool saturated must NOT raise into the tick.
    eng = _FakeEngine(raise_on="execute")
    ok = await wi.upsert_registry(eng)
    assert ok is False


@pytest.mark.asyncio
async def test_stale_executor_ids_returns_names():
    eng = _FakeEngine(fetch_rows=[{"executor_id": "worker"}, {"executor_id": "w2"}])
    stale = await wi.stale_executor_ids(eng, 360)
    assert stale == ["worker", "w2"]


@pytest.mark.asyncio
async def test_stale_executor_ids_swallows_errors_returns_empty():
    eng = _FakeEngine(raise_on="fetch")
    assert await wi.stale_executor_ids(eng, 360) == []
