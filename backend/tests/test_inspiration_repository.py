"""InspirationNotesRepository unit tests — fake chainable supabase client."""

from unittest.mock import AsyncMock, patch

import pytest

from app.repositories.inspiration_repository import InspirationNotesRepository


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """Chainable stand-in recording every builder call."""

    def __init__(self, result_data):
        self._result_data = result_data
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self

        return method

    async def execute(self):
        return FakeResult(self._result_data)


class FakeClient:
    def __init__(self, result_data):
        self.query = FakeQuery(result_data)
        self.rpc_query = FakeQuery(result_data)

    def table(self, name):
        self.query.calls.append(("table", (name,), {}))
        return self.query

    def rpc(self, fn, params):
        self.rpc_query.calls.append(("rpc", (fn, params), {}))
        return self.rpc_query


def _patch_client(fake):
    return patch(
        "app.repositories.inspiration_repository.get_async_supabase_admin",
        new=AsyncMock(return_value=fake),
    )


@pytest.mark.asyncio
async def test_create_inserts_row_and_returns_dict():
    fake = FakeClient([{"id": 1, "content_md": "x"}])
    with _patch_client(fake):
        repo = InspirationNotesRepository()
        row = await repo.create(
            user_id="u1",
            content_md="x #a",
            tags=["a"],
            note_date="2026-07-07",
            ref_hotspot=None,
        )
    assert row == {"id": 1, "content_md": "x"}
    insert_calls = [c for c in fake.query.calls if c[0] == "insert"]
    assert len(insert_calls) == 1
    payload = insert_calls[0][1][0]
    assert payload["user_id"] == "u1"
    assert payload["tags"] == ["a"]
    assert payload["note_date"] == "2026-07-07"
    assert "ref_hotspot" not in payload  # None 不显式写,吃 DB 默认


@pytest.mark.asyncio
async def test_list_applies_keyset_and_filters():
    fake = FakeClient([])
    with _patch_client(fake):
        repo = InspirationNotesRepository()
        await repo.list(
            "u1", date="2026-07-07", tag="hooks", q="ferry", limit=20, before_id="99"
        )
    names = [c[0] for c in fake.query.calls]
    assert "lt" in names  # keyset: id < before_id
    assert "contains" in names  # tags @> [tag]
    assert "ilike" in names  # content_md ILIKE %q%
    lt = next(c for c in fake.query.calls if c[0] == "lt")
    assert lt[1] == ("id", 99)  # bigint 收敛为 int


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_missing():
    fake = FakeClient(None)
    with _patch_client(fake):
        repo = InspirationNotesRepository()
        assert await repo.get_by_id("123") is None


@pytest.mark.asyncio
async def test_soft_delete_sets_deleted_at():
    fake = FakeClient([{"id": 1}])
    with _patch_client(fake):
        repo = InspirationNotesRepository()
        ok = await repo.soft_delete("123")
    assert ok is True
    update_calls = [c for c in fake.query.calls if c[0] == "update"]
    assert "deleted_at" in update_calls[0][1][0]


@pytest.mark.asyncio
async def test_activity_calls_rpc_with_named_params():
    fake = FakeClient([{"day": "2026-07-07", "cnt": 3}])
    with _patch_client(fake):
        repo = InspirationNotesRepository()
        rows = await repo.activity("u1", "2026-04-01", "2026-07-07")
    assert rows == [{"day": "2026-07-07", "cnt": 3}]
    rpc = fake.rpc_query.calls[0]
    assert rpc[1][0] == "inspiration_activity"
    assert rpc[1][1] == {
        "p_user_id": "u1",
        "p_from": "2026-04-01",
        "p_to": "2026-07-07",
    }
