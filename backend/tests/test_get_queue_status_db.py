"""get_queue_status counts via the SQLAlchemy async engine, not supabase-py.

The 30s system-status collection used to COUNT task_tracking over Kong/
PostgREST (httpx), leaking a CLOSE_WAIT connection per call (known httpcore
bug, Issue #199 Bug C / 2026-05-22 incident). Migrated to SQLAlchemy Core
over the asyncpg driver (Issue #199 backend data-layer target).

Phase B5 Task 2 fix2: get_queue_status used to branch on
``db_engine.is_configured()`` — a raw ``eng.connect() + text()`` path for
"prod" and an ORM ``read_scope()`` path for "dev" — but both branches bound
to the SAME ``get_engine()`` singleton, so the split was dead weight. Now
there is only the ORM path; tests patch ``app.db.session.read_scope``
instead of ``app.db.engine.get_engine``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from app.services.infra import system_monitor_service as sms


class _FakeResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar(self) -> int:
        return self._value


class _FakeSession:
    """Stands in for the ORM AsyncSession — returns queued scalar values in
    call order (active/processing count first, then pending/queued)."""

    def __init__(self, values: list[int]) -> None:
        self._values = values
        self._i = 0

    async def execute(self, _stmt: object) -> _FakeResult:
        v = self._values[self._i]
        self._i += 1
        return _FakeResult(v)


def _fake_read_scope(values: list[int]):
    session = _FakeSession(values)

    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


async def test_get_queue_status_counts_via_sqlalchemy_engine(monkeypatch):
    # Clear the 5s cache so the query actually runs.
    sms._queue_cache.update(data=None, timestamp=0)

    import app.db.session as db_session

    monkeypatch.setattr(
        db_session, "read_scope", _fake_read_scope([3, 7])
    )  # active (processing), pending (queued)

    result = await sms.get_queue_status()
    assert result == {
        "active": 3,
        "pending": 7,
        "scheduled": 0,
        "status": "online",
    }


async def test_get_queue_status_offline_on_engine_error(monkeypatch):
    sms._queue_cache.update(data=None, timestamp=0)

    import app.db.session as db_session

    @asynccontextmanager
    async def _boom_scope():
        raise RuntimeError("engine down")
        yield  # pragma: no cover — unreachable, keeps this a generator

    monkeypatch.setattr(db_session, "read_scope", _boom_scope)

    result = await sms.get_queue_status()
    assert result["status"] == "offline"
    assert result["active"] == 0 and result["pending"] == 0
