"""Unit tests for AdminTeamsRepository."""

from __future__ import annotations

from typing import Any

import pytest

from app.repositories.admin.teams_repository import AdminTeamsRepository


class _FakeQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._data: Any = []
        self._count: int | None = None

    def __getattr__(self, name: str):
        def _capture(*args: Any, **kwargs: Any) -> "_FakeQuery":
            self.calls.append((name, args, kwargs))
            return self

        return _capture

    async def execute(self) -> Any:
        class _R:
            data = self._data
            count = self._count

        return _R()


class _FakeClient:
    def __init__(self, query: _FakeQuery) -> None:
        self._query = query

    def table(self, name: str) -> _FakeQuery:
        self._query.calls.append(("table", (name,), {}))
        return self._query


@pytest.fixture
def fake_query() -> _FakeQuery:
    return _FakeQuery()


@pytest.fixture
def repo(fake_query: _FakeQuery) -> AdminTeamsRepository:
    r = AdminTeamsRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


# ─── Teams ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_teams_without_search(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "t1", "name": "A"}]
    fake_query._count = 1
    rows, total = await repo.list_teams(search=None, offset=0, limit=10)
    assert rows == [{"id": "t1", "name": "A"}]
    assert total == 1

    ilikes = [c for c in fake_query.calls if c[0] == "ilike"]
    assert ilikes == []


@pytest.mark.asyncio
async def test_list_teams_with_search_uses_ilike(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_teams(search="foo", offset=0, limit=10)

    ilike = next(c for c in fake_query.calls if c[0] == "ilike")
    assert ilike[1] == ("name", "%foo%")


@pytest.mark.asyncio
async def test_list_teams_paginates_with_range(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_teams(search=None, offset=20, limit=10)

    rng = next(c for c in fake_query.calls if c[0] == "range")
    assert rng[1] == (20, 29)


@pytest.mark.asyncio
async def test_get_returns_row(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {"id": "t1", "name": "A"}
    team = await repo.get("t1")
    assert team is not None
    assert team["name"] == "A"


@pytest.mark.asyncio
async def test_update_sends_changes(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    await repo.update("t1", {"owner_id": "u2"})

    upd = next(c for c in fake_query.calls if c[0] == "update")
    assert upd[1] == ({"owner_id": "u2"},)
    eqs = [c for c in fake_query.calls if c[0] == "eq"]
    assert ("id", "t1") in [c[1] for c in eqs]


@pytest.mark.asyncio
async def test_delete_targets_id(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    await repo.delete("t1")

    assert any(c[0] == "delete" for c in fake_query.calls)
    eqs = [c for c in fake_query.calls if c[0] == "eq"]
    assert ("id", "t1") in [c[1] for c in eqs]


@pytest.mark.asyncio
async def test_unlink_collections_updates_team_id_null(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    await repo.unlink_collections("t1")

    upd = next(c for c in fake_query.calls if c[0] == "update")
    assert upd[1] == ({"team_id": None},)

    tables = [c for c in fake_query.calls if c[0] == "table"]
    assert tables[0][1] == ("collections",)


# ─── Members ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_members_filters_by_team(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"user_id": "u1", "role": "admin"}]
    rows = await repo.list_members("t1")
    assert rows == [{"user_id": "u1", "role": "admin"}]

    eqs = [c for c in fake_query.calls if c[0] == "eq"]
    assert ("team_id", "t1") in [c[1] for c in eqs]


@pytest.mark.asyncio
async def test_get_member_filters_both_columns(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {"role": "admin"}
    await repo.get_member("t1", "u1")

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("team_id", "t1") in eq_values
    assert ("user_id", "u1") in eq_values


@pytest.mark.asyncio
async def test_update_member_role(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    await repo.update_member_role("t1", "u1", "owner")

    upd = next(c for c in fake_query.calls if c[0] == "update")
    assert upd[1] == ({"role": "owner"},)


@pytest.mark.asyncio
async def test_delete_member_issues_delete(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    await repo.delete_member("t1", "u1")

    assert any(c[0] == "delete" for c in fake_query.calls)
    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("team_id", "t1") in eq_values
    assert ("user_id", "u1") in eq_values


# ─── Points balances ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_points_balance_returns_value(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {"points_balance": 500}
    assert await repo.get_points_balance("t1") == 500


@pytest.mark.asyncio
async def test_get_points_balance_zero_on_missing(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = None
    assert await repo.get_points_balance("t1") == 0


@pytest.mark.asyncio
async def test_batch_points_balances_empty_input(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    assert await repo.batch_points_balances([]) == {}


@pytest.mark.asyncio
async def test_batch_points_balances_groups_by_team_id(
    repo: AdminTeamsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {"team_id": "t1", "points_balance": 100},
        {"team_id": "t2", "points_balance": 200},
    ]
    result = await repo.batch_points_balances(["t1", "t2"])
    assert result == {"t1": 100, "t2": 200}
