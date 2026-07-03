"""Unit tests for AdminTeamsRepository (ORM 2.0, model-backed reads + writes).

Post-rollout the repository is the SQLAlchemy 2.0 implementation — reads go
through ``read_scope()`` with ``select(...)`` statements and writes through
``write_scope()`` with ``update()`` / ``delete()`` statements. These tests mock
those scopes with a fake session that captures every emitted ``(sql, binds)``
pair and returns in-memory ``Teams`` / ``TeamMembers`` instances, so the compiled
SQL shape + bind params AND the ``_obj_dict`` value-type sweep (uuid → str,
datetime → ISO str, bigint id → native int) are asserted WITHOUT a live database
(the DSN-gated integration suite in
``tests/integration/test_admin_teams_repository_orm.py`` exercises the real
round-trip). This keeps fast, always-run coverage of the collapsed ORM bodies.

team_id path/query params are int-coerced by ``_bigint`` (asyncpg int8 codec is
strict), so these tests pass numeric ids ("100") — a non-numeric id would raise
before reaching the fake session.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

import app.repositories.admin.teams_repository as mod
from app.models import TeamMembers, Teams
from app.repositories.admin.teams_repository import AdminTeamsRepository


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeResult:
    """Supports ``.scalars().all()`` / ``.scalars().one_or_none()`` (model reads)
    and ``.all()`` (batch_points_balances' (team_id, balance) tuples)."""

    def __init__(self, scalar_rows: list[Any], all_rows: list[Any]) -> None:
        self._scalar_rows = scalar_rows
        self._all_rows = all_rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._scalar_rows)

    def all(self) -> list[Any]:
        return self._all_rows


class _FakeSession:
    """Captures execute/scalar (stmt, binds); returns configured rows / scalar."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scalar_rows: list[Any] = []
        self.all_rows: list[Any] = []
        self.scalar_value: Any = 0

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
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def repo() -> AdminTeamsRepository:
    return AdminTeamsRepository()


def _team(**overrides: Any) -> Teams:
    fields: dict[str, Any] = {
        "id": 100,
        "owner_id": uuid.uuid4(),
        "name": "Test Team",
        "invite_code": "inv123",
        "kind": "collaborative",
        "settings_json": None,
        "created_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return Teams(**fields)


def _member(**overrides: Any) -> TeamMembers:
    fields: dict[str, Any] = {
        "team_id": 100,
        "user_id": uuid.uuid4(),
        "role": "member",
        "joined_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return TeamMembers(**fields)


# ─── Teams ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_teams_no_search_orders_and_counts(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    owner = uuid.uuid4()
    fake_session.scalar_rows = [_team(id=100, owner_id=owner)]
    fake_session.scalar_value = 1

    rows, total = await repo.list_teams(search=None, offset=20, limit=10)
    assert total == 1
    r = rows[0]
    # _obj_dict value-type sweep: bigint → native int, uuid → str, datetime → ISO.
    assert type(r["id"]) is int and r["id"] == 100
    assert r["owner_id"] == str(owner)
    assert r["created_at"] == "2026-04-01T00:00:00+00:00"

    sql, binds = fake_session.calls[-1]  # the paginated SELECT
    assert "teams" in sql
    assert "ORDER BY" in sql and "created_at DESC" in sql
    assert "ilike" not in sql.lower() and "like" not in sql.lower()
    # offset=20, limit=10 threaded into binds.
    assert 20 in binds.values() and 10 in binds.values()


@pytest.mark.asyncio
async def test_list_teams_with_search_uses_ilike(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list_teams(search="foo", offset=0, limit=10)

    sql, binds = fake_session.calls[-1]
    assert "lower" in sql.lower() or "like" in sql.lower()  # ILIKE lowers both sides
    assert "%foo%" in binds.values()


@pytest.mark.asyncio
async def test_get_returns_row_parity(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    owner = uuid.uuid4()
    fake_session.scalar_rows = [_team(id=100, owner_id=owner)]

    team = await repo.get("100")
    assert team is not None
    assert type(team["id"]) is int and team["id"] == 100
    assert team["owner_id"] == str(owner)

    sql, binds = fake_session.calls[-1]
    assert "teams" in sql
    assert 100 in binds.values()  # _bigint coerced "100" → 100


@pytest.mark.asyncio
async def test_get_absent_returns_none(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.get("999") is None


@pytest.mark.asyncio
async def test_update_sends_changes_and_commits(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    await repo.update("100", {"name": "Renamed"})

    sql, binds = fake_session.calls[-1]
    assert "UPDATE" in sql and "teams" in sql
    assert "Renamed" in binds.values()
    assert 100 in binds.values()  # WHERE id = _bigint("100")


@pytest.mark.asyncio
async def test_update_empty_changes_is_noop(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    await repo.update("100", {})
    assert fake_session.calls == []  # returns before entering write_scope


@pytest.mark.asyncio
async def test_delete_targets_id(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    await repo.delete("100")

    sql, binds = fake_session.calls[-1]
    assert "DELETE FROM" in sql and "teams" in sql
    assert 100 in binds.values()


@pytest.mark.asyncio
async def test_unlink_collections_nulls_team_id(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    await repo.unlink_collections("100")

    sql, binds = fake_session.calls[-1]
    assert "UPDATE" in sql and "collections" in sql
    assert "team_id" in sql
    assert 100 in binds.values()


# ─── Members ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_members_filters_orders_and_parity(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    user = uuid.uuid4()
    fake_session.scalar_rows = [_member(user_id=user)]

    rows = await repo.list_members("100")
    assert rows[0]["user_id"] == str(user)  # uuid → str (member enrichment dict-key)
    assert rows[0]["joined_at"] == "2026-04-01T00:00:00+00:00"

    sql, binds = fake_session.calls[-1]
    assert "team_members" in sql
    assert "ORDER BY" in sql and "joined_at" in sql
    assert 100 in binds.values()


@pytest.mark.asyncio
async def test_get_member_filters_both_columns(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    user = uuid.uuid4()
    fake_session.scalar_rows = [_member(user_id=user)]
    await repo.get_member("100", str(user))

    sql, binds = fake_session.calls[-1]
    assert "team_id" in sql and "user_id" in sql
    values = set(binds.values())
    assert 100 in values and str(user) in values


@pytest.mark.asyncio
async def test_get_member_absent_returns_none(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.get_member("100", "u1") is None


@pytest.mark.asyncio
async def test_update_member_role_commits(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    await repo.update_member_role("100", "u1", "owner")

    sql, binds = fake_session.calls[-1]
    assert "UPDATE" in sql and "team_members" in sql
    values = set(binds.values())
    assert "owner" in values and 100 in values and "u1" in values


@pytest.mark.asyncio
async def test_delete_member_issues_delete(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    await repo.delete_member("100", "u1")

    sql, binds = fake_session.calls[-1]
    assert "DELETE FROM" in sql and "team_members" in sql
    values = set(binds.values())
    assert 100 in values and "u1" in values


# ─── Points balances ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_points_balance_returns_value(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 500
    assert await repo.get_points_balance("100") == 500


@pytest.mark.asyncio
async def test_get_points_balance_zero_on_missing(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = None
    assert await repo.get_points_balance("100") == 0


@pytest.mark.asyncio
async def test_batch_points_balances_empty_input(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    assert await repo.batch_points_balances([]) == {}
    assert fake_session.calls == []  # short-circuits before any query


@pytest.mark.asyncio
async def test_batch_points_balances_keys_by_str_team_id(
    repo: AdminTeamsRepository, fake_session: _FakeSession
) -> None:
    fake_session.all_rows = [(1, 100), (2, 200)]
    result = await repo.batch_points_balances([1, 2])
    # keyed by str(team_id) — matches the router's points_balances.get(str(tid)).
    assert result == {"1": 100, "2": 200}
