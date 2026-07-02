"""Unit tests for AdminTagsRepository (SQLAlchemy 2.0 ORM, admin tags/tag_groups).

Post-rollout the repository IS the SQLAlchemy 2.0 implementation — the legacy
supabase-py REST path was retired with USE_ORM_ADMIN_TAGS. These tests mock
``read_scope``/``write_scope`` with a fake session that captures every emitted
``(compiled sql, binds)`` pair and returns configured ORM row objects / tuples /
scalars, so the compiled SQL shape + bind params AND the strategy-C value-type
parity sweep (tags.user_id uuid → str, created_at timestamptz → ISO str, BIGINT
id / group_id → native int, the tag_groups embed shape) are asserted WITHOUT a
live database (the DSN-gated integration suite in
``tests/integration/test_admin_tags_repository_orm.py`` exercises the real
round-trip). This keeps fast, always-run coverage of the collapsed ORM bodies.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.admin.tags_repository as mod
from app.models import TagGroups, Tags
from app.repositories.admin.tags_repository import AdminTagsRepository

# ─── ORM fake session ──────────────────────────────────────────────


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, session: "_FakeSession") -> None:
        self._s = session

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._s.rows)

    def all(self) -> list[Any]:
        return list(self._s.tuples)

    def first(self) -> Any:
        return self._s.first_value


class _FakeSession:
    """Captures execute/scalar (compiled sql, binds); returns configured rows /
    tuples / scalar / first-value."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows: list[Any] = []  # scalars().all() / scalars().first()
        self.tuples: list[Any] = []  # result.all()  (row tuples)
        self.scalar_value: Any = None  # session.scalar()
        self.first_value: Any = None  # result.first() (RETURNING id delete)

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return _FakeResult(self)

    async def scalar(self, stmt: Any) -> Any:
        self.calls.append(_compile(stmt))
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
def repo() -> AdminTagsRepository:
    return AdminTagsRepository()


# ─── Tag groups ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_groups_orders_by_sort_then_created(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [TagGroups(id=1, name="A", sort_order=0)]
    groups = await repo.list_groups()
    assert groups[0]["name"] == "A"
    assert type(groups[0]["id"]) is int  # BIGINT → native int (NOT uuid)

    sql, _ = fake_session.calls[-1]
    assert "tag_groups" in sql
    assert "ORDER BY public.tag_groups.sort_order" in sql
    assert "public.tag_groups.created_at" in sql


@pytest.mark.asyncio
async def test_max_group_sort_order_returns_zero_on_none(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = None
    assert await repo.max_group_sort_order() == 0

    sql, _ = fake_session.calls[-1]
    assert "tag_groups.sort_order" in sql
    assert "DESC" in sql and "LIMIT" in sql


@pytest.mark.asyncio
async def test_max_group_sort_order_returns_value(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 42
    assert await repo.max_group_sort_order() == 42


@pytest.mark.asyncio
async def test_create_group_inserts_and_returns_row(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [TagGroups(id=7, name="New", sort_order=5)]
    group = await repo.create_group("New", 5)
    assert group["id"] == 7 and group["name"] == "New" and group["sort_order"] == 5

    sql, binds = fake_session.calls[-1]
    assert "INSERT INTO public.tag_groups" in sql
    assert "RETURNING" in sql
    assert "New" in binds.values()
    assert 5 in binds.values()


@pytest.mark.asyncio
async def test_update_group_empty_changes_reads_via_get(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [TagGroups(id=3, name="G", sort_order=1)]
    group = await repo.update_group("3", {})
    assert group["id"] == 3

    sql, binds = fake_session.calls[-1]
    # no changes → a SELECT (the _get_group fallback), not an UPDATE
    assert sql.startswith("SELECT")
    assert 3 in binds.values()  # id filter coerced to native int


@pytest.mark.asyncio
async def test_update_group_updates_with_returning(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [TagGroups(id=3, name="Renamed", sort_order=1)]
    group = await repo.update_group("3", {"name": "Renamed"})
    assert group["name"] == "Renamed"

    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.tag_groups SET" in sql
    assert "RETURNING" in sql
    assert "Renamed" in binds.values()
    assert 3 in binds.values()  # id bind coerced to int


@pytest.mark.asyncio
async def test_delete_group_true_when_row_returned(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.first_value = (3,)
    assert await repo.delete_group("3") is True

    sql, binds = fake_session.calls[-1]
    assert "DELETE FROM public.tag_groups" in sql
    assert "RETURNING public.tag_groups.id" in sql
    assert 3 in binds.values()


@pytest.mark.asyncio
async def test_delete_group_false_when_nothing_deleted(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.first_value = None
    assert await repo.delete_group("999") is False


@pytest.mark.asyncio
async def test_reorder_groups_one_update_per_id_with_index(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    await repo.reorder_groups(["10", "20", "30"])

    updates = [(s, b) for s, b in fake_session.calls if s.startswith("UPDATE")]
    assert len(updates) == 3
    # each id → UPDATE ... SET sort_order=idx WHERE id=<int>
    for idx, (sql, binds) in enumerate(updates):
        assert "tag_groups" in sql
        assert idx in binds.values()
    assert 10 in updates[0][1].values()
    assert 30 in updates[2][1].values()


@pytest.mark.asyncio
async def test_reorder_groups_noop_on_empty(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    await repo.reorder_groups([])
    assert fake_session.calls == []


# ─── Tags ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tags_uncategorized_uses_is_null(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 0
    fake_session.tuples = []
    await repo.list_tags(page=1, page_size=50, group_id="uncategorized")

    # the count query + the rows query both filter group_id IS NULL
    sql, _ = fake_session.calls[-1]
    assert "group_id IS NULL" in sql


@pytest.mark.asyncio
async def test_list_tags_specific_group_uses_eq_bind(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 0
    fake_session.tuples = []
    await repo.list_tags(page=1, page_size=50, group_id="42")

    sql, binds = fake_session.calls[-1]
    assert "group_id =" in sql
    assert 42 in binds.values()  # group_id coerced to native int


@pytest.mark.asyncio
async def test_list_tags_search_uses_or_ilike(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 0
    fake_session.tuples = []
    await repo.list_tags(page=1, page_size=50, search="foo")

    sql, binds = fake_session.calls[-1]
    assert "ILIKE" in sql
    assert "name" in sql and "name_zh" in sql
    assert "%foo%" in binds.values()


@pytest.mark.asyncio
async def test_list_tags_custom_sort_asc(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 0
    fake_session.tuples = []
    await repo.list_tags(page=1, page_size=50, sort_by="name", sort_order="asc")

    sql, _ = fake_session.calls[-1]
    assert "ORDER BY public.tags.name ASC" in sql


@pytest.mark.asyncio
async def test_list_tags_embed_shape_and_parity_sweep(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    user_id = uuid.uuid4()
    created = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    tag = Tags(
        id=100,
        name="t",
        type="system",
        group_id=42,
        color="#abc",
        user_id=user_id,
        created_at=created,
    )
    fake_session.scalar_value = 1
    fake_session.tuples = [(tag, "GroupName")]
    rows, total = await repo.list_tags(page=1, page_size=50)
    assert total == 1
    r = rows[0]
    assert type(r["id"]) is int and r["id"] == 100  # BIGINT → native int
    assert type(r["group_id"]) is int  # BIGINT → native int
    assert r["created_at"] == created.isoformat()  # tstz → ISO str
    assert type(r["created_at"]) is str
    assert r["user_id"] == str(user_id)  # uuid → str
    assert type(r["user_id"]) is str
    assert r["type"] == "system"  # plain str (CHECK, not Enum)
    # the PostgREST embed: nested {"name": ...}
    assert r["tag_groups"] == {"name": "GroupName"}


@pytest.mark.asyncio
async def test_list_tags_embed_none_when_no_group(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    tag = Tags(id=1, name="t", type="system", group_id=None)
    fake_session.scalar_value = 1
    fake_session.tuples = [(tag, None)]
    rows, _ = await repo.list_tags(page=1, page_size=50)
    assert rows[0]["tag_groups"] is None


@pytest.mark.asyncio
async def test_all_tag_group_ids_wraps_group_id(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.tuples = [(42,), (None,)]
    rows = await repo.all_tag_group_ids()
    assert rows == [{"group_id": 42}, {"group_id": None}]

    sql, _ = fake_session.calls[-1]
    assert "SELECT public.tags.group_id" in sql


@pytest.mark.asyncio
async def test_usage_counts_empty_input_short_circuits(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    assert await repo.usage_counts([]) == {}
    assert fake_session.calls == []  # no DB hit


@pytest.mark.asyncio
async def test_usage_counts_groups_by_str_tag_id(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.tuples = [(1,), (1,), (2,)]
    counts = await repo.usage_counts(["1", "2"])
    # keyed by str(tag_id) — matches the router's usage_counts.get(str(t["id"]))
    assert counts == {"1": 2, "2": 1}

    sql, binds = fake_session.calls[-1]
    assert "resource_tags" in sql and "IN" in sql
    # tag_ids coerced to native int for the BIGINT IN bind (expanding list param)
    assert [1, 2] in binds.values()


@pytest.mark.asyncio
async def test_create_tag_inserts_and_coerces_group_id(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [Tags(id=5, name="n", type="system", group_id=42)]
    created = await repo.create_tag(
        {"name": "n", "type": "system", "group_id": "42", "user_id": None}
    )
    assert created["id"] == 5

    sql, binds = fake_session.calls[-1]
    assert "INSERT INTO public.tags" in sql
    assert "RETURNING" in sql
    assert 42 in binds.values()  # group_id "42" → native int


@pytest.mark.asyncio
async def test_update_tag_empty_changes_reads_via_get(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [Tags(id=5, name="n", type="system")]
    updated = await repo.update_tag("5", {})
    assert updated["id"] == 5

    sql, binds = fake_session.calls[-1]
    assert sql.startswith("SELECT")  # _get_tag fallback
    assert 5 in binds.values()


@pytest.mark.asyncio
async def test_update_tag_updates_with_returning(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [Tags(id=5, name="n", type="system", color="#222222")]
    updated = await repo.update_tag("5", {"color": "#222222"})
    assert updated["color"] == "#222222"

    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.tags SET" in sql
    assert "RETURNING" in sql
    assert "#222222" in binds.values()
    assert 5 in binds.values()


@pytest.mark.asyncio
async def test_delete_tag_cascades_resource_tags_first(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.first_value = (5,)
    assert await repo.delete_tag("5") is True

    # first DELETE targets resource_tags, second targets tags (verbatim order)
    deletes = [s for s, _ in fake_session.calls if s.startswith("DELETE")]
    assert "resource_tags" in deletes[0]
    assert "public.tags" in deletes[1]
    assert "RETURNING public.tags.id" in deletes[1]


@pytest.mark.asyncio
async def test_delete_tag_false_when_nothing_deleted(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    fake_session.first_value = None
    assert await repo.delete_tag("999") is False


# ─── Batch ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_set_group_to_null(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    await repo.batch_set_group(["1", "2"], None)

    updates = [(s, b) for s, b in fake_session.calls if s.startswith("UPDATE")]
    assert len(updates) == 2
    for sql, binds in updates:
        assert "tags" in sql
        assert None in binds.values()  # group_id = NULL


@pytest.mark.asyncio
async def test_batch_set_group_coerces_group_id(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    await repo.batch_set_group(["1"], "42")

    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.tags" in sql
    assert 42 in binds.values()  # group_id "42" → native int


@pytest.mark.asyncio
async def test_batch_set_group_noop_on_empty(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    await repo.batch_set_group([], "42")
    assert fake_session.calls == []


@pytest.mark.asyncio
async def test_batch_set_color(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    await repo.batch_set_color(["1", "2"], "#ff0000")

    updates = [(s, b) for s, b in fake_session.calls if s.startswith("UPDATE")]
    assert len(updates) == 2
    for _, binds in updates:
        assert "#ff0000" in binds.values()


@pytest.mark.asyncio
async def test_batch_delete_cascades_per_id(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    await repo.batch_delete(["1", "2"])

    deletes = [s for s, _ in fake_session.calls if s.startswith("DELETE")]
    # per id: resource_tags then tags → 4 deletes, alternating
    assert len(deletes) == 4
    assert "resource_tags" in deletes[0] and "public.tags" in deletes[1]
    assert "resource_tags" in deletes[2] and "public.tags" in deletes[3]


@pytest.mark.asyncio
async def test_reorder_tags_matches_index(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    await repo.reorder_tags(["10", "20"])

    updates = [(s, b) for s, b in fake_session.calls if s.startswith("UPDATE")]
    assert len(updates) == 2
    assert 0 in updates[0][1].values() and 10 in updates[0][1].values()
    assert 1 in updates[1][1].values() and 20 in updates[1][1].values()


@pytest.mark.asyncio
async def test_reorder_tags_noop_on_empty(
    repo: AdminTagsRepository, fake_session: _FakeSession
) -> None:
    await repo.reorder_tags([])
    assert fake_session.calls == []
