"""get_queue_status counts via the SQLAlchemy async engine, not supabase-py.

The 30s system-status collection used to COUNT task_tracking over Kong/
PostgREST (httpx), leaking a CLOSE_WAIT connection per call (known httpcore
bug, Issue #199 Bug C / 2026-05-22 incident). Migrated to SQLAlchemy Core
over the asyncpg driver (Issue #199 backend data-layer target).
"""

from __future__ import annotations

from app.services.infra import system_monitor_service as sms


class _FakeResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar(self) -> int:
        return self._value


class _FakeConn:
    """Stands in for a SQLAlchemy AsyncConnection used as an async CM."""

    def __init__(self, values: list[int]) -> None:
        self._values = values
        self._i = 0

    async def execute(self, _stmt: object) -> _FakeResult:
        v = self._values[self._i]
        self._i += 1
        return _FakeResult(v)

    async def __aenter__(self) -> "_FakeConn":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakeEngine:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def connect(self) -> _FakeConn:
        return self._conn


async def test_get_queue_status_counts_via_sqlalchemy_engine(monkeypatch):
    # Clear the 5s cache so the query actually runs.
    sms._queue_cache.update(data=None, timestamp=0)

    from app.db import engine as db_engine

    conn = _FakeConn([3, 7])  # active (processing), pending (queued)
    monkeypatch.setattr(db_engine, "is_configured", lambda: True)
    monkeypatch.setattr(db_engine, "get_engine", lambda: _FakeEngine(conn))

    result = await sms.get_queue_status()
    assert result == {
        "active": 3,
        "pending": 7,
        "scheduled": 0,
        "status": "online",
    }


async def test_get_queue_status_offline_on_engine_error(monkeypatch):
    sms._queue_cache.update(data=None, timestamp=0)

    from app.db import engine as db_engine

    monkeypatch.setattr(db_engine, "is_configured", lambda: True)

    def _boom() -> _FakeEngine:
        raise RuntimeError("engine down")

    monkeypatch.setattr(db_engine, "get_engine", _boom)

    result = await sms.get_queue_status()
    assert result["status"] == "offline"
    assert result["active"] == 0 and result["pending"] == 0
