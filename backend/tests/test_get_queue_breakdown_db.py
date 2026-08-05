"""get_queue_breakdown's DB-touching path (Phase B5 Task 2 fix2).

Previously untested: only the pure ``_aggregate_breakdown`` pivot had
coverage (tests/test_queue_breakdown.py); the async read path itself
(``db_engine.is_configured()`` branching between a raw ``eng.connect() +
text()`` "prod" path and an ORM ``read_scope()`` "dev" path) had none. Both
branches bound to the same ``get_engine()`` singleton, so the split was
collapsed to the one ORM path — this file closes the gap, patching
``app.db.session.read_scope``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from app.services.infra import system_monitor_service as sms


class _FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[dict]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(self._rows)


def _fake_read_scope(rows: list[dict]):
    session = _FakeSession(rows)

    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


async def test_get_queue_breakdown_reads_via_orm_and_pivots(monkeypatch):
    sms._breakdown_cache.update(data=None, timestamp=0.0)

    import app.db.session as db_session

    now = datetime.now(timezone.utc)
    rows = [
        {"task_type": "download", "phase": "processing", "created_at": now},
        {"task_type": "download", "phase": "queued", "created_at": now},
        {"task_type": "ai_summary", "phase": "queued", "created_at": now},
    ]
    monkeypatch.setattr(db_session, "read_scope", _fake_read_scope(rows))

    result = await sms.get_queue_breakdown()

    by_type = {r["task_type"]: r for r in result}
    assert by_type["download"]["running"] == 1
    assert by_type["download"]["pending"] == 1
    assert by_type["ai_summary"]["pending"] == 1


async def test_get_queue_breakdown_falls_back_to_stale_cache_on_error(monkeypatch):
    sms._breakdown_cache.update(data=[{"task_type": "stale"}], timestamp=0.0)

    import app.db.session as db_session

    @asynccontextmanager
    async def _boom_scope():
        raise RuntimeError("db down")
        yield  # pragma: no cover — unreachable, keeps this a generator

    monkeypatch.setattr(db_session, "read_scope", _boom_scope)

    result = await sms.get_queue_breakdown()
    assert result == [{"task_type": "stale"}]
