"""Unit tests for AgentRepository (mock-based, no real DB)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.repositories.agent_repository import AgentRepository


# ─── Fake Supabase client/query plumbing ──────────────────────────────
#
# Captures every chained call so tests can assert on query construction
# (matches the style used by test_nous_repository.py).


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
    """Records every .table() invocation (useful for delete-then-insert)."""

    def __init__(self, query: _FakeQuery) -> None:
        self._query = query

    def table(self, name: str) -> _FakeQuery:
        self._query.calls.append(("table", (name,), {}))
        return self._query


@pytest.fixture
def fake_query() -> _FakeQuery:
    return _FakeQuery()


@pytest.fixture
def repo(fake_query: _FakeQuery) -> AgentRepository:
    r = AgentRepository()

    async def _get_client():
        return _FakeClient(fake_query)

    r._get_client = _get_client  # type: ignore[method-assign]
    return r


# ─── get_by_slug ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_slug_returns_none_for_missing(
    repo: AgentRepository, fake_query: _FakeQuery
) -> None:
    """maybe_single() returns empty data when slug doesn't exist."""
    fake_query._data = None
    result = await repo.get_by_slug("nonexistent_slug")
    assert result is None

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("slug", "nonexistent_slug") in eq_values


@pytest.mark.asyncio
async def test_get_by_slug_returns_row_when_found(
    repo: AgentRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {"id": "abc", "slug": "script_ai", "name": "Script AI"}
    result = await repo.get_by_slug("script_ai")
    assert result == {"id": "abc", "slug": "script_ai", "name": "Script AI"}


# ─── get_skill_ids ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_skill_ids_returns_empty_for_agent_with_no_skills(
    repo: AgentRepository, fake_query: _FakeQuery
) -> None:
    """No binding rows => empty list."""
    fake_query._data = []
    agent_id = uuid4()
    skill_ids = await repo.get_skill_ids(agent_id)

    assert skill_ids == []
    # Confirms we filter on enabled=true
    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("enabled", True) in eq_values
    assert ("agent_id", str(agent_id)) in eq_values


@pytest.mark.asyncio
async def test_get_skill_ids_casts_bigint_rows_to_int(
    repo: AgentRepository, fake_query: _FakeQuery
) -> None:
    """skill_id is BIGINT — make sure we coerce to int."""
    fake_query._data = [{"skill_id": 100}, {"skill_id": 200}, {"skill_id": 300}]
    skill_ids = await repo.get_skill_ids(uuid4())
    assert skill_ids == [100, 200, 300]
    assert all(isinstance(sid, int) for sid in skill_ids)


# ─── update_skill_bindings ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_skill_bindings_replaces_existing(
    repo: AgentRepository, fake_query: _FakeQuery
) -> None:
    """Replace-all semantics: delete existing, then insert new rows."""
    agent_id = uuid4()

    # First call: seed one binding (skill=10) — delete all + insert [10]
    await repo.update_skill_bindings(agent_id, [10])

    # Second call: replace with [20] — delete all + insert [20]
    await repo.update_skill_bindings(agent_id, [20])

    # Check operation order: each call should issue delete followed by insert.
    ops = [c[0] for c in fake_query.calls]
    assert ops.count("delete") == 2
    assert ops.count("insert") == 2

    # Every delete is followed by an insert (never insert without delete).
    delete_indices = [i for i, op in enumerate(ops) if op == "delete"]
    insert_indices = [i for i, op in enumerate(ops) if op == "insert"]
    for d_idx, i_idx in zip(delete_indices, insert_indices):
        assert d_idx < i_idx

    # Last insert should carry skill_id=20 (replaced 10), not both.
    last_insert = [c for c in fake_query.calls if c[0] == "insert"][-1]
    rows = last_insert[1][0]
    assert len(rows) == 1
    assert rows[0]["skill_id"] == 20
    assert rows[0]["agent_id"] == str(agent_id)
    assert rows[0]["enabled"] is True
    assert rows[0]["sort_order"] == 0


@pytest.mark.asyncio
async def test_update_skill_bindings_skips_insert_when_empty(
    repo: AgentRepository, fake_query: _FakeQuery
) -> None:
    """Clearing bindings: delete only, no empty insert call."""
    await repo.update_skill_bindings(uuid4(), [])

    ops = [c[0] for c in fake_query.calls]
    assert "delete" in ops
    assert "insert" not in ops


# ─── update_fields ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_fields_returns_first_row(
    repo: AgentRepository, fake_query: _FakeQuery
) -> None:
    agent_id = uuid4()
    fake_query._data = [{"id": str(agent_id), "name": "Renamed"}]

    result = await repo.update_fields(agent_id, {"name": "Renamed"})
    assert result == {"id": str(agent_id), "name": "Renamed"}

    upd = next(c for c in fake_query.calls if c[0] == "update")
    assert upd[1] == ({"name": "Renamed"},)


@pytest.mark.asyncio
async def test_update_fields_returns_empty_dict_when_no_row(
    repo: AgentRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    result = await repo.update_fields(uuid4(), {"name": "x"})
    assert result == {}
