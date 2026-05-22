"""get_queue_status counts via the asyncpg pool, not supabase-py/httpx.

The 30s system-status collection used to COUNT task_tracking over Kong/
PostgREST (httpx), leaking a CLOSE_WAIT connection per call (known
httpcore bug, Issue #199 Bug C / 2026-05-22 incident). Migrated to the
asyncpg pool (direct PG, clean connection handling).
"""

from __future__ import annotations

from app.services.infra import system_monitor_service as sms


class _FakeConn:
    def __init__(self, values: list[int]) -> None:
        self._values = values
        self._i = 0

    async def fetchval(self, query: str) -> int:
        v = self._values[self._i]
        self._i += 1
        return v


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


async def test_get_queue_status_counts_via_pg_pool(monkeypatch):
    # Clear the 5s cache so the query actually runs.
    sms._queue_cache.update(data=None, timestamp=0)

    from app.db import pg_pool

    conn = _FakeConn([3, 7])  # active (processing), pending (queued)
    monkeypatch.setattr(pg_pool, "is_configured", lambda: True)

    async def _fake_get_pool() -> _FakePool:
        return _FakePool(conn)

    monkeypatch.setattr(pg_pool, "get_pool", _fake_get_pool)

    result = await sms.get_queue_status()
    assert result == {
        "active": 3,
        "pending": 7,
        "scheduled": 0,
        "status": "online",
    }


async def test_get_queue_status_offline_on_pool_error(monkeypatch):
    sms._queue_cache.update(data=None, timestamp=0)

    from app.db import pg_pool

    monkeypatch.setattr(pg_pool, "is_configured", lambda: True)

    async def _boom() -> _FakePool:
        raise RuntimeError("pool down")

    monkeypatch.setattr(pg_pool, "get_pool", _boom)

    result = await sms.get_queue_status()
    assert result["status"] == "offline"
    assert result["active"] == 0 and result["pending"] == 0
