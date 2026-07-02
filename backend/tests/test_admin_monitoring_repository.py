"""Unit tests for MonitoringRepository (ORM 2.0, model-backed).

Post-rollout the log reads (``request_logs_between`` / ``app_logs_between`` /
``frontend_error_count``) are the SQLAlchemy 2.0 implementation — they go through
``read_scope()`` with ``select`` statements and build SELECT-subset-shaped dicts
by hand. These tests mock ``read_scope`` with a fake session that captures every
emitted ``(compiled_sql, binds)`` pair and returns in-memory rows, so the compiled
SQL shape (order / count) + bind params (window datetimes + limit) AND the
strategy-C value-type parity (timestamp / logged_at → ISO str; status_code /
response_time_ms → native int; COUNT → native int) are asserted WITHOUT a live
database (the DSN-gated integration suite in
``tests/integration/test_monitoring_repository_orm.py`` exercises the real
round-trip). This keeps fast, always-run coverage of the collapsed ORM bodies.

``monitoring_stats`` stays on the supabase-py ``rpc_monitoring_stats`` RPC path
and is covered by ``tests/test_admin_stats_rpc_mapping.py`` (unchanged).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

import app.repositories.admin.monitoring_repository as mod
from app.repositories.admin.monitoring_repository import MonitoringRepository


class _FakeResult:
    """Supports ``.all()`` (execute → row tuples)."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Captures execute/scalar (compiled sql, binds); returns configured rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows: list[Any] = []
        self.scalar_value: int | None = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append((str(stmt), stmt.compile().params))
        return _FakeResult(self.rows)

    async def scalar(self, stmt: Any) -> Any:
        self.calls.append((str(stmt), stmt.compile().params))
        return self.scalar_value


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def repo() -> MonitoringRepository:
    return MonitoringRepository()


def _bind_values(session: _FakeSession) -> list[Any]:
    values: list[Any] = []
    for _sql, params in session.calls:
        values.extend(params.values())
    return values


@pytest.mark.asyncio
async def test_request_logs_between_applies_window(
    repo: MonitoringRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [
        ("/x", "GET", 200, 42, datetime(2026, 4, 16, 12, tzinfo=timezone.utc))
    ]
    start = datetime(2026, 4, 16, tzinfo=timezone.utc)
    end = datetime(2026, 4, 17, tzinfo=timezone.utc)
    rows = await repo.request_logs_between(start, end)

    assert rows == [
        {
            "path": "/x",
            "method": "GET",
            "status_code": 200,
            "response_time_ms": 42,
            "timestamp": datetime(2026, 4, 16, 12, tzinfo=timezone.utc).isoformat(),
        }
    ]
    binds = _bind_values(fake_session)
    assert start in binds and end in binds


@pytest.mark.asyncio
async def test_request_logs_between_orders_ascending(
    repo: MonitoringRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.request_logs_between(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 2, tzinfo=timezone.utc),
    )
    sql = fake_session.calls[0][0]
    assert "ORDER BY" in sql and "timestamp ASC" in sql


@pytest.mark.asyncio
async def test_request_logs_between_default_limit(
    repo: MonitoringRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.request_logs_between(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 2, tzinfo=timezone.utc),
    )
    assert 10000 in _bind_values(fake_session)


@pytest.mark.asyncio
async def test_request_logs_between_custom_limit(
    repo: MonitoringRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.request_logs_between(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 2, tzinfo=timezone.utc),
        limit=500,
    )
    assert 500 in _bind_values(fake_session)


@pytest.mark.asyncio
async def test_request_logs_between_parity_types(
    repo: MonitoringRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [
        ("/p", "POST", 503, 999, datetime(2026, 4, 16, 9, tzinfo=timezone.utc))
    ]
    rows = await repo.request_logs_between(
        datetime(2026, 4, 16, tzinfo=timezone.utc),
        datetime(2026, 4, 17, tzinfo=timezone.utc),
    )
    r = rows[0]
    assert type(r["timestamp"]) is str
    assert datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
    assert type(r["status_code"]) is int and r["status_code"] == 503
    assert type(r["response_time_ms"]) is int and r["response_time_ms"] == 999
    assert set(r.keys()) == {
        "path",
        "method",
        "status_code",
        "response_time_ms",
        "timestamp",
    }


@pytest.mark.asyncio
async def test_app_logs_between_orders_desc_and_default_limit(
    repo: MonitoringRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.app_logs_between(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 2, tzinfo=timezone.utc),
    )
    sql = fake_session.calls[0][0]
    assert "logged_at DESC" in sql
    assert 5000 in _bind_values(fake_session)


@pytest.mark.asyncio
async def test_app_logs_between_parity_types(
    repo: MonitoringRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [
        ("ERROR", "app.mod", "boom", datetime(2026, 4, 16, 9, tzinfo=timezone.utc))
    ]
    rows = await repo.app_logs_between(
        datetime(2026, 4, 16, tzinfo=timezone.utc),
        datetime(2026, 4, 17, tzinfo=timezone.utc),
    )
    r = rows[0]
    assert type(r["logged_at"]) is str
    assert datetime.fromisoformat(r["logged_at"])
    assert set(r.keys()) == {"level", "module", "message", "logged_at"}


@pytest.mark.asyncio
async def test_frontend_error_count_uses_count(
    repo: MonitoringRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 9
    count = await repo.frontend_error_count(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 2, tzinfo=timezone.utc),
    )
    assert type(count) is int and count == 9
    assert "count(" in fake_session.calls[0][0].lower()


@pytest.mark.asyncio
async def test_frontend_error_count_returns_zero_when_count_is_none(
    repo: MonitoringRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = None
    count = await repo.frontend_error_count(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 2, tzinfo=timezone.utc),
    )
    assert count == 0
