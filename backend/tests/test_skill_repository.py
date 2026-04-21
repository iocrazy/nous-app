"""Unit tests for SkillRepository (mock-based, no real DB).

Mirrors the _FakeQuery / _FakeClient fixture style used by
``test_agent_repository.py`` and ``test_nous_repository.py``.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.repositories.skill_repository import SkillRepository


# ─── Fake Supabase client/query plumbing ──────────────────────────────


class _FakeQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._data: Any = []
        self._raises: Exception | None = None

    def __getattr__(self, name: str):
        def _capture(*args: Any, **kwargs: Any) -> "_FakeQuery":
            self.calls.append((name, args, kwargs))
            return self

        return _capture

    async def execute(self) -> Any:
        if self._raises is not None:
            raise self._raises

        class _R:
            data = self._data

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
def repo(fake_query: _FakeQuery) -> SkillRepository:
    r = SkillRepository()

    async def _get_client():
        return _FakeClient(fake_query)

    r._get_client = _get_client  # type: ignore[method-assign]
    return r


# ─── get_by_slug ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_slug_returns_none_for_missing(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    """maybe_single() returns empty data when slug doesn't exist."""
    fake_query._data = None
    result = await repo.get_by_slug("nonexistent_slug")
    assert result is None

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("slug", "nonexistent_slug") in eq_values


@pytest.mark.asyncio
async def test_get_by_slug_returns_row_when_found(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {"id": 42, "slug": "translate", "name": "Translate"}
    result = await repo.get_by_slug("translate")
    assert result == {"id": 42, "slug": "translate", "name": "Translate"}


@pytest.mark.asyncio
async def test_get_by_slug_returns_none_on_exception(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    """Read errors are swallowed and return None."""
    fake_query._raises = RuntimeError("boom")
    assert await repo.get_by_slug("translate") is None


# ─── list_by_ids (batch composer fetch) ───────────────────────────────


@pytest.mark.asyncio
async def test_list_by_ids_returns_empty_for_empty_input(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    """Short-circuits on empty input — never issues a query with IN ()."""
    result = await repo.list_by_ids([])
    assert result == []
    # No .table() call at all — we bailed before touching the client.
    assert fake_query.calls == []


@pytest.mark.asyncio
async def test_list_by_ids_uses_in_clause(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": 1}, {"id": 2}, {"id": 3}]
    result = await repo.list_by_ids([1, 2, 3])
    assert result == [{"id": 1}, {"id": 2}, {"id": 3}]

    in_calls = [c for c in fake_query.calls if c[0] == "in_"]
    assert len(in_calls) == 1
    assert in_calls[0][1] == ("id", [1, 2, 3])


# ─── list_files ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_files_returns_files_in_order(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    """Files are ordered by sort_order then path."""
    fake_query._data = [
        {"id": "u1", "skill_id": 10, "path": "SKILL.md", "sort_order": 0},
        {"id": "u2", "skill_id": 10, "path": "refs/a.md", "sort_order": 1},
    ]
    files = await repo.list_files(10)
    assert len(files) == 2
    assert files[0]["path"] == "SKILL.md"

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("skill_id", 10) in eq_values

    order_args = [c[1] for c in fake_query.calls if c[0] == "order"]
    assert ("sort_order",) in order_args
    assert ("path",) in order_args

    # And we're hitting the correct table.
    table_args = [c[1] for c in fake_query.calls if c[0] == "table"]
    assert ("skill_files",) in table_args


@pytest.mark.asyncio
async def test_list_files_returns_empty_on_error(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    fake_query._raises = RuntimeError("boom")
    assert await repo.list_files(10) == []


# ─── get_file ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_file_returns_none_for_unknown_path(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = None
    result = await repo.get_file(10, "missing.md")
    assert result is None

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("skill_id", 10) in eq_values
    assert ("path", "missing.md") in eq_values


@pytest.mark.asyncio
async def test_get_file_returns_row_when_found(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {
        "id": "uuid-1",
        "skill_id": 10,
        "path": "SKILL.md",
        "content": "hi",
        "file_type": "markdown",
    }
    row = await repo.get_file(10, "SKILL.md")
    assert row is not None
    assert row["content"] == "hi"


# ─── upsert_file ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_file_passes_on_conflict_param(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    """upsert() must be called with on_conflict='skill_id,path'."""
    fake_query._data = [
        {
            "id": "uuid-1",
            "skill_id": 10,
            "path": "SKILL.md",
            "content": "hello",
            "file_type": "markdown",
        }
    ]

    result = await repo.upsert_file(
        10,
        path="SKILL.md",
        content="hello",
        file_type="markdown",
    )
    assert result["path"] == "SKILL.md"

    upserts = [c for c in fake_query.calls if c[0] == "upsert"]
    assert len(upserts) == 1
    args, kwargs = upserts[0][1], upserts[0][2]
    # row payload positional
    assert args[0]["skill_id"] == 10
    assert args[0]["path"] == "SKILL.md"
    assert args[0]["content"] == "hello"
    assert args[0]["file_type"] == "markdown"
    assert args[0]["binary_url"] is None
    # on_conflict keyword
    assert kwargs == {"on_conflict": "skill_id,path"}


@pytest.mark.asyncio
async def test_upsert_file_raises_when_no_data_returned(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    """Writes never silently no-op — empty result must raise."""
    fake_query._data = []
    with pytest.raises(RuntimeError):
        await repo.upsert_file(10, path="x.md", content="", file_type="markdown")


# ─── delete_file ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_file_filters_by_skill_and_path(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    await repo.delete_file(10, "refs/old.md")

    ops = [c[0] for c in fake_query.calls]
    assert "delete" in ops

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("skill_id", 10) in eq_values
    assert ("path", "refs/old.md") in eq_values


# ─── list_accessible ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_accessible_filters_active_status(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": 1, "name": "Public skill", "is_public": True}]
    uid = uuid4()
    rows = await repo.list_accessible(uid)
    assert len(rows) == 1

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("status", "active") in eq_values

    # No project_id / team_ids => OR filter is public + user's own only.
    or_calls = [c for c in fake_query.calls if c[0] == "or_"]
    assert len(or_calls) == 1
    assert or_calls[0][1] == (f"is_public.eq.true,created_by.eq.{uid}",)


@pytest.mark.asyncio
async def test_list_accessible_with_project_id_widens_or_filter(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    uid = uuid4()
    await repo.list_accessible(uid, project_id=777)

    or_calls = [c for c in fake_query.calls if c[0] == "or_"]
    assert len(or_calls) == 1
    assert or_calls[0][1] == (
        f"is_public.eq.true,created_by.eq.{uid},project_id.in.(777)",
    )


@pytest.mark.asyncio
async def test_list_accessible_with_team_and_project_ids(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    """New plural params: team_ids + project_ids both widen the OR filter."""
    fake_query._data = []
    uid = uuid4()
    await repo.list_accessible(uid, team_ids=[10, 20], project_ids=[30])

    or_calls = [c for c in fake_query.calls if c[0] == "or_"]
    assert len(or_calls) == 1
    assert or_calls[0][1] == (
        f"is_public.eq.true,created_by.eq.{uid},"
        f"project_id.in.(30),team_id.in.(10,20)",
    )


# ─── update_fields ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_fields_returns_first_row(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": 10, "name": "Renamed"}]
    result = await repo.update_fields(10, {"name": "Renamed"})
    assert result == {"id": 10, "name": "Renamed"}

    upd = next(c for c in fake_query.calls if c[0] == "update")
    assert upd[1] == ({"name": "Renamed"},)
    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("id", 10) in eq_values


@pytest.mark.asyncio
async def test_update_fields_returns_empty_dict_when_no_row(
    repo: SkillRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    assert await repo.update_fields(10, {"name": "x"}) == {}
