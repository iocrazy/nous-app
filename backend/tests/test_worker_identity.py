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
