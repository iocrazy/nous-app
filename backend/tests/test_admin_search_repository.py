"""Unit tests for AdminSearchRepository (ORM 2.0, select()/text() over PG).

Post-rollout the repository is the SQLAlchemy 2.0 implementation — every read
goes through ``read_scope()`` with either a Core ``select()`` (request_logs /
app_logs / get_request_log / app_logs_by_request_id) or a parameterized
``text()`` statement (frontend_logs / audit_logs). These tests mock that scope
with a fake session that captures every ``(stmt, binds)`` pair, so the emitted
SQL shape + bind params are asserted WITHOUT a live database (the DSN-gated
integration suite in ``tests/integration/test_admin_search_repository_orm.py``
exercises the real round-trip). This keeps fast, always-run coverage of the
collapsed ORM bodies.

Unlike the all-``text()`` sibling repos, this domain mixes Core ``select()`` and
``text()``: for a ``select()`` statement the bind values are embedded in the
compiled statement (``stmt.compile().params``) rather than passed as a second
``execute`` arg, so the fake session stores the raw statement object and the
assertions compile it when they need the bound values.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

import app.repositories.admin.search_repository as mod
from app.repositories.admin.search_repository import AdminSearchRepository


class _AttrRow:
    """Attribute-access row (Core ``Row`` stand-in); missing attrs → None so a
    partial fixture row does not blow up the ``SELECT *`` projection."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.__dict__.update(data)

    def __getattr__(self, name: str) -> Any:  # only for keys not supplied
        return None


class _FakeScalars:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def first(self) -> Any:
        return _AttrRow(self._rows[0]) if self._rows else None


class _FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[_AttrRow]:
        return [_AttrRow(r) for r in self._rows]

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self._rows)

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    """Captures execute calls; returns the configured rows. Stores the raw stmt
    so select()-based assertions can compile it for embedded bind params."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []
        self.rows: list[dict[str, Any]] = []

    async def execute(self, stmt: Any, binds: dict[str, Any] | None = None) -> Any:
        self.calls.append((stmt, dict(binds or {})))
        return _FakeResult(self.rows)


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
def repo() -> AdminSearchRepository:
    return AdminSearchRepository()


def _sql(stmt: Any) -> str:
    return str(stmt)


def _embedded_params(stmt: Any) -> dict[str, Any]:
    """Bind values embedded in a compiled Core ``select()`` statement."""
    return stmt.compile().params


# ─── Time-range queries per log source ──────────────────────────────


@pytest.mark.asyncio
async def test_request_logs_uses_timestamp_range(
    repo: AdminSearchRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [{"id": 1}]
    rows = await repo.request_logs("2026-01-01", "2026-01-02")
    assert rows == [
        {
            "id": 1,
            "request_id": None,
            "method": None,
            "path": None,
            "status_code": None,
            "response_time_ms": None,
            "timestamp": None,
            "error_detail": None,
        }
    ]

    stmt, _ = fake_session.calls[0]
    sql = _sql(stmt)
    assert repo.REQUEST_LOGS_TABLE in sql
    assert "timestamp" in sql
    # both ISO bounds coerced to NATIVE tz-aware datetimes (v3 rule)
    dts = [v for v in _embedded_params(stmt).values() if isinstance(v, datetime)]
    assert len(dts) == 2
    assert all(d.tzinfo is not None for d in dts)


@pytest.mark.asyncio
async def test_request_logs_orders_desc(
    repo: AdminSearchRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.request_logs("2026-01-01", "2026-01-02")

    stmt, _ = fake_session.calls[0]
    sql = _sql(stmt)
    assert "ORDER BY" in sql
    assert "timestamp" in sql
    assert "DESC" in sql


@pytest.mark.asyncio
async def test_app_logs_uses_logged_at(
    repo: AdminSearchRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.app_logs("2026-01-01", "2026-01-02")

    stmt, _ = fake_session.calls[0]
    sql = _sql(stmt)
    assert repo.APP_LOGS_TABLE in sql
    assert "logged_at" in sql
    assert "DESC" in sql


@pytest.mark.asyncio
async def test_frontend_logs_uses_stack_column_and_table(
    repo: AdminSearchRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.frontend_logs("2026-01-01", "2026-01-02", limit=50)

    stmt, binds = fake_session.calls[0]
    sql = _sql(stmt)
    assert repo.FRONTEND_LOGS_TABLE in sql
    # the fixed column is `stack`, never the nonexistent `stack_trace`
    assert "stack" in sql
    assert "stack_trace" not in sql
    assert "created_at" in sql
    # text() binds are passed explicitly, tstz bounds coerced (v3 rule)
    assert isinstance(binds["start"], datetime) and binds["start"].tzinfo is not None
    assert isinstance(binds["end"], datetime) and binds["end"].tzinfo is not None
    assert binds["limit"] == 50


@pytest.mark.asyncio
async def test_audit_logs_uses_audit_logs_table_and_admin_id(
    repo: AdminSearchRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.audit_logs("2026-01-01", "2026-01-02")

    stmt, binds = fake_session.calls[0]
    sql = _sql(stmt)
    # the fixed table is `audit_logs`, never the nonexistent `admin_audit_logs`
    assert "admin_audit_logs" not in sql
    assert repo.AUDIT_LOGS_TABLE in sql
    assert "admin_id" in sql
    assert "admin_email" not in sql
    assert isinstance(binds["start"], datetime)


# ─── Request trace correlation ──────────────────────────────────────


@pytest.mark.asyncio
async def test_get_request_log_returns_first_row(
    repo: AdminSearchRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [
        {"id": 1, "request_id": "r-1", "method": "GET", "user_id": None},
        {"id": 2, "request_id": "r-1", "method": "POST", "user_id": None},
    ]
    log = await repo.get_request_log("r-1")
    assert log is not None
    assert log["method"] == "GET"
    assert log["request_id"] == "r-1"

    stmt, _ = fake_session.calls[0]
    sql = _sql(stmt)
    assert repo.REQUEST_LOGS_TABLE in sql
    assert "request_id" in sql


@pytest.mark.asyncio
async def test_get_request_log_none_when_empty(
    repo: AdminSearchRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    assert await repo.get_request_log("r-1") is None


@pytest.mark.asyncio
async def test_app_logs_by_request_id_filters_on_jsonb_path(
    repo: AdminSearchRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [{"level": "INFO", "extra": {}}]
    await repo.app_logs_by_request_id("r-1")

    stmt, _ = fake_session.calls[0]
    sql = _sql(stmt)
    # JSONB text-extraction (`->>`) equality on the extra column, oldest-first
    assert "extra" in sql
    assert "->>" in sql
    assert "ASC" in sql
    # the extracted key and the compared request_id ride as embedded binds
    values = list(_embedded_params(stmt).values())
    assert "request_id" in values
    assert "r-1" in values
