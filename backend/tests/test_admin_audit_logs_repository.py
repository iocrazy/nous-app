"""Unit tests for AuditLogsRepository (ORM 2.0, model-backed reads).

Post-rollout the repository is the SQLAlchemy 2.0 implementation — reads go
through ``read_scope()`` with ``select(AuditLogs)`` statements and the row
objects are converted to SELECT *-shaped dicts by ``_row``. These tests mock
``read_scope`` with a fake session that captures every emitted ``(sql, binds)``
pair and returns in-memory ``AuditLogs`` instances, so the compiled SQL shape +
bind params AND the ``_row`` value-type sweep are asserted WITHOUT a live
database (the DSN-gated integration suite in
``tests/integration/test_audit_logs_repository_orm.py`` exercises the real
round-trip). This keeps fast, always-run coverage of the collapsed ORM bodies.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

import app.repositories.admin.audit_logs_repository as mod
from app.models import AuditLogs
from app.repositories.admin.audit_logs_repository import AuditLogsRepository


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeResult:
    """Supports both ``.scalars().all()`` (model reads) and ``.all()``
    (the distinct-action 1-tuple rows)."""

    def __init__(self, scalar_rows: list[Any], all_rows: list[Any]) -> None:
        self._scalar_rows = scalar_rows
        self._all_rows = all_rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._scalar_rows)

    def all(self) -> list[Any]:
        return self._all_rows


class _FakeSession:
    """Captures execute/scalar (stmt, binds); returns configured rows / count."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scalar_rows: list[Any] = []
        self.all_rows: list[Any] = []
        self.scalar_value: int | None = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append((str(stmt), stmt.compile().params))
        return _FakeResult(self.scalar_rows, self.all_rows)

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
def repo() -> AuditLogsRepository:
    return AuditLogsRepository()


def _log(**overrides: Any) -> AuditLogs:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "admin_id": uuid.uuid4(),
        "action": "ban_user",
        "target_type": "user",
        "created_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return AuditLogs(**fields)


@pytest.mark.asyncio
async def test_list_threads_all_filters(
    repo: AuditLogsRepository, fake_session: _FakeSession
) -> None:
    row_id = uuid.uuid4()
    admin = uuid.uuid4()
    fake_session.scalar_rows = [_log(id=row_id, admin_id=admin)]
    fake_session.scalar_value = 1
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    end = datetime(2026, 4, 17, tzinfo=timezone.utc)

    rows, total = await repo.list(
        page=2,
        page_size=25,
        admin_id="admin-1",
        action="ban_user",
        target_type="user",
        start_date=start,
        end_date=end,
    )
    assert total == 1
    # _row value-type sweep: uuid → str, created_at → ISO str.
    assert rows[0]["id"] == str(row_id)
    assert rows[0]["admin_id"] == str(admin)
    assert rows[0]["created_at"] == "2026-04-01T00:00:00+00:00"

    # Every filter is threaded into the SELECT and its bind params.
    sql, binds = fake_session.calls[-1]  # the paginated SELECT
    assert "audit_logs" in sql
    assert "admin_id" in sql and "action" in sql and "target_type" in sql
    assert "created_at" in sql
    values = set(binds.values())
    assert "admin-1" in values
    assert "ban_user" in values
    assert "user" in values
    assert start in values  # gte binds the tz-aware datetime
    assert end in values  # lte binds the tz-aware datetime


@pytest.mark.asyncio
async def test_list_pagination_math(
    repo: AuditLogsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list(page=3, page_size=50)

    sql, binds = fake_session.calls[-1]  # the paginated SELECT
    # page=3, page_size=50 → OFFSET 100, LIMIT 50.
    assert 100 in binds.values()
    assert 50 in binds.values()


@pytest.mark.asyncio
async def test_list_orders_newest_first(
    repo: AuditLogsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list(page=1, page_size=10)

    sql, _ = fake_session.calls[-1]
    assert "ORDER BY" in sql
    assert "created_at DESC" in sql


@pytest.mark.asyncio
async def test_list_total_fallback_when_count_is_none(
    repo: AuditLogsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [_log(), _log()]
    fake_session.scalar_value = None
    rows, total = await repo.list(page=1, page_size=20)
    assert total == 2  # falls back to len(rows) when count scalar is None


@pytest.mark.asyncio
async def test_list_distinct_actions_empty_input(
    repo: AuditLogsRepository, fake_session: _FakeSession
) -> None:
    fake_session.all_rows = []
    assert await repo.list_distinct_actions() == []


@pytest.mark.asyncio
async def test_list_distinct_actions_sorts_and_dedups(
    repo: AuditLogsRepository, fake_session: _FakeSession
) -> None:
    fake_session.all_rows = [
        ("ban_user",),
        ("update_user",),
        ("ban_user",),
        (None,),
    ]
    result = await repo.list_distinct_actions()
    assert result == ["ban_user", "update_user"]  # sorted, de-duped, NULL dropped


@pytest.mark.asyncio
async def test_list_since_uses_gte(
    repo: AuditLogsRepository, fake_session: _FakeSession
) -> None:
    row_id = uuid.uuid4()
    fake_session.scalar_rows = [_log(id=row_id)]
    since = datetime(2026, 4, 10, tzinfo=timezone.utc)
    rows = await repo.list_since(since)
    assert rows[0]["id"] == str(row_id)

    sql, binds = fake_session.calls[-1]
    assert "created_at >=" in sql
    assert since in binds.values()  # tz-aware datetime bound (v3 rule)
