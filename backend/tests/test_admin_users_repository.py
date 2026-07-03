"""Unit tests for AdminUsersRepository (ORM 2.0, model-backed reads + writes).

Post-rollout the repository is the SQLAlchemy 2.0 implementation — reads go
through ``read_scope()`` and writes through ``write_scope()`` with
``select(UserProfiles)`` / ``update(UserProfiles)`` statements, and the row
objects are converted to SELECT *-shaped dicts by ``_row``. These tests mock
``read_scope`` / ``write_scope`` with a fake session that captures every emitted
``(sql, binds)`` pair and returns in-memory ``UserProfiles`` instances, so the
compiled SQL shape + bind params AND the ``_row`` value-type sweep (uuid → str,
Enum(role) → bare .value, datetime → ISO str) are asserted WITHOUT a live
database (the DSN-gated integration suite in
``tests/integration/test_admin_users_repository_orm.py`` exercises the real
round-trip). This keeps fast, always-run coverage of the collapsed ORM bodies.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

import app.repositories.admin.users_repository as mod
from app.models import UserProfiles
from app.models._enums import UserRole
from app.repositories.admin.users_repository import AdminUsersRepository


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, scalar_rows: list[Any]) -> None:
        self._scalar_rows = scalar_rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._scalar_rows)


class _FakeSession:
    """Captures execute/scalar (stmt, binds); returns configured rows / scalar."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scalar_rows: list[Any] = []
        self.scalar_value: Any = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append((str(stmt), stmt.compile().params))
        return _FakeResult(self.scalar_rows)

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
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def repo() -> AdminUsersRepository:
    return AdminUsersRepository()


def _user(**overrides: Any) -> UserProfiles:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "username": "alice",
        "role": UserRole.ADMIN,
        "is_banned": False,
        "display_id": 123,
        "created_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return UserProfiles(**fields)


@pytest.mark.asyncio
async def test_list_with_filters_threads_search_role_and_pagination(
    repo: AdminUsersRepository, fake_session: _FakeSession
) -> None:
    row_id = uuid.uuid4()
    fake_session.scalar_rows = [_user(id=row_id)]
    fake_session.scalar_value = 1

    rows, total = await repo.list_with_filters(
        page=2, page_size=10, search="alice", role="admin"
    )
    assert total == 1
    # _row value-type sweep: uuid → str, Enum(role) → bare .value, datetime → ISO.
    r = rows[0]
    assert r["id"] == str(row_id)
    assert type(r["id"]) is str
    assert r["role"] == "admin"
    assert str(r["role"]) == "admin"  # bare value, NOT 'UserRole.ADMIN'
    assert r["created_at"] == "2026-04-01T00:00:00+00:00"

    # search + role both threaded into the SELECT and its bind params.
    sql, binds = fake_session.calls[-1]  # the paginated SELECT
    assert "user_profiles" in sql
    assert "username" in sql and "role" in sql
    values = set(binds.values())
    assert "%alice%" in values
    assert "admin" in values
    # page=2, page_size=10 → OFFSET 10, LIMIT 10.
    assert 10 in binds.values()


@pytest.mark.asyncio
async def test_list_orders_newest_first(
    repo: AdminUsersRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list_with_filters(page=1, page_size=20)

    sql, _ = fake_session.calls[-1]
    assert "ORDER BY" in sql
    assert "created_at DESC" in sql


@pytest.mark.asyncio
async def test_list_total_fallback_when_count_is_none(
    repo: AdminUsersRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [_user(), _user()]
    fake_session.scalar_value = None
    rows, total = await repo.list_with_filters(page=1, page_size=20)
    assert total == 0  # count None → (total or 0)


@pytest.mark.asyncio
async def test_get_by_id_returns_row(
    repo: AdminUsersRepository, fake_session: _FakeSession
) -> None:
    row_id = uuid.uuid4()
    fake_session.scalar_rows = [_user(id=row_id, username="alice")]
    user = await repo.get_by_id(str(row_id))
    assert user is not None
    assert user["id"] == str(row_id)
    assert user["username"] == "alice"


@pytest.mark.asyncio
async def test_get_by_id_returns_none_on_missing(
    repo: AdminUsersRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.get_by_id("nope") is None


@pytest.mark.asyncio
async def test_exists_true_and_false(
    repo: AdminUsersRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = uuid.uuid4()
    assert await repo.exists("u1") is True

    fake_session.scalar_value = None
    assert await repo.exists("u1") is False


@pytest.mark.asyncio
async def test_update_returns_full_row_and_binds_changes(
    repo: AdminUsersRepository, fake_session: _FakeSession
) -> None:
    row_id = uuid.uuid4()
    fake_session.scalar_rows = [_user(id=row_id, role=UserRole.USER)]

    row = await repo.update(str(row_id), {"role": "user"})
    assert row is not None
    assert row["id"] == str(row_id)
    assert str(row["role"]) == "user"  # Enum unwrapped on the returned row too

    sql, binds = fake_session.calls[-1]
    assert "UPDATE" in sql and "user_profiles" in sql
    assert "user" in binds.values()  # the changed role value is bound


@pytest.mark.asyncio
async def test_update_no_changes_returns_none_without_query(
    repo: AdminUsersRepository, fake_session: _FakeSession
) -> None:
    assert await repo.update("u1", {}) is None
    assert fake_session.calls == []  # short-circuits before touching the session


@pytest.mark.asyncio
async def test_update_returns_none_when_no_row_matched(
    repo: AdminUsersRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.update("u1", {"is_banned": True}) is None


@pytest.mark.asyncio
async def test_set_banned_delegates_to_update(
    repo: AdminUsersRepository, fake_session: _FakeSession
) -> None:
    row_id = uuid.uuid4()
    fake_session.scalar_rows = [_user(id=row_id, is_banned=True)]

    row = await repo.set_banned(str(row_id), True)
    assert row is not None and row["is_banned"] is True

    sql, binds = fake_session.calls[-1]
    assert "UPDATE" in sql and "is_banned" in sql
    assert True in binds.values()
