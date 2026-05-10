"""Unit tests for AdminTagsRepository."""

from __future__ import annotations

from typing import Any

import pytest

from app.repositories.admin.tags_repository import AdminTagsRepository


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
def repo(fake_query: _FakeQuery) -> AdminTagsRepository:
    r = AdminTagsRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


# ─── Tag groups ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_groups_orders_by_sort_then_created(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "g1", "name": "A"}]
    await repo.list_groups()

    order_calls = [c for c in fake_query.calls if c[0] == "order"]
    assert order_calls[0][1] == ("sort_order",)
    assert order_calls[1][1] == ("created_at",)


@pytest.mark.asyncio
async def test_max_group_sort_order_returns_zero_on_empty(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    assert await repo.max_group_sort_order() == 0


@pytest.mark.asyncio
async def test_max_group_sort_order_returns_first_row(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"sort_order": 42}]
    assert await repo.max_group_sort_order() == 42


@pytest.mark.asyncio
async def test_create_group_inserts_with_sort(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "g1", "name": "New", "sort_order": 5}]
    group = await repo.create_group("New", 5)
    assert group == {"id": "g1", "name": "New", "sort_order": 5}

    insert_call = next(c for c in fake_query.calls if c[0] == "insert")
    assert insert_call[1] == ({"name": "New", "sort_order": 5},)


@pytest.mark.asyncio
async def test_delete_group_returns_false_when_nothing_deleted(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    assert await repo.delete_group("nope") is False


@pytest.mark.asyncio
async def test_reorder_groups_sends_one_update_per_id(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.reorder_groups(["g1", "g2", "g3"])

    update_calls = [c for c in fake_query.calls if c[0] == "update"]
    # Each id → one update({sort_order: idx})
    assert update_calls == [
        ("update", ({"sort_order": 0},), {}),
        ("update", ({"sort_order": 1},), {}),
        ("update", ({"sort_order": 2},), {}),
    ]


# ─── Tags ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tags_uncategorized_uses_is_null(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_tags(page=1, page_size=50, group_id="uncategorized")

    is_calls = [c for c in fake_query.calls if c[0] == "is_"]
    assert ("group_id", "null") in [c[1] for c in is_calls]


@pytest.mark.asyncio
async def test_list_tags_specific_group_uses_eq(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_tags(page=1, page_size=50, group_id="g-1")

    eq_calls = [c for c in fake_query.calls if c[0] == "eq"]
    assert ("group_id", "g-1") in [c[1] for c in eq_calls]


@pytest.mark.asyncio
async def test_list_tags_search_uses_or_ilike(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_tags(page=1, page_size=50, search="foo")

    or_call = next(c for c in fake_query.calls if c[0] == "or_")
    assert "name.ilike.%foo%" in or_call[1][0]
    assert "name_zh.ilike.%foo%" in or_call[1][0]


@pytest.mark.asyncio
async def test_list_tags_custom_sort(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_tags(page=1, page_size=50, sort_by="name", sort_order="asc")

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("name",)
    assert order[2] == {"desc": False}


@pytest.mark.asyncio
async def test_usage_counts_returns_empty_on_empty_input(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    assert await repo.usage_counts([]) == {}


@pytest.mark.asyncio
async def test_usage_counts_groups_by_tag_id(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {"tag_id": "t1"},
        {"tag_id": "t1"},
        {"tag_id": "t2"},
    ]
    counts = await repo.usage_counts(["t1", "t2"])
    assert counts == {"t1": 2, "t2": 1}


@pytest.mark.asyncio
async def test_delete_tag_cascades_resource_tags_first(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "t1"}]
    await repo.delete_tag("t1")

    # The first delete should target resource_tags, then tags
    table_calls = [c for c in fake_query.calls if c[0] == "table"]
    assert table_calls[0][1] == ("resource_tags",)
    assert table_calls[1][1] == ("tags",)


# ─── Batch ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_set_group_to_null(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.batch_set_group(["t1", "t2"], None)

    updates = [c for c in fake_query.calls if c[0] == "update"]
    for u in updates:
        assert u[1] == ({"group_id": None},)
    assert len(updates) == 2


@pytest.mark.asyncio
async def test_batch_set_color(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.batch_set_color(["t1", "t2"], "#ff0000")

    updates = [c for c in fake_query.calls if c[0] == "update"]
    for u in updates:
        assert u[1] == ({"color": "#ff0000"},)


@pytest.mark.asyncio
async def test_reorder_tags_matches_index(
    repo: AdminTagsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.reorder_tags(["t-a", "t-b"])

    updates = [c for c in fake_query.calls if c[0] == "update"]
    assert updates == [
        ("update", ({"sort_order": 0},), {}),
        ("update", ({"sort_order": 1},), {}),
    ]
