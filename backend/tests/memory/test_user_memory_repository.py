"""User-facing agent_memory repo functions (Task 1 — TDD RED phase).

Tests for:
  async list_user_memories(*, user_id, team_ids, limit) -> List[Dict]
  async delete_user_memory(*, memory_id, user_id) -> bool

in ``app.repositories.agent_memory_repository``.

Pattern mirrors test_agent_memory_recall.py / test_memory_stats.py:
  custom _Session/_Scope classes, patch read_scope/write_scope at the repo
  import site.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Shared mock primitives
# ---------------------------------------------------------------------------

_ROW_OWN: Dict[str, Any] = {
    "id": 10,
    "owner_user_id": "user-abc",
    "title": "My preference",
    "body_md": "I prefer dark mode.",
    "kind": "preference",
    "scope": "agent_user",
    "visibility": "private",
    "when_to_use": "always",
    "created_at": "2026-06-20T08:00:00",
}

_ROW_SHARED: Dict[str, Any] = {
    "id": 20,
    "owner_user_id": "user-other",
    "title": "Team deploy process",
    "body_md": "Deploy every Friday.",
    "kind": "fact",
    "scope": "team",
    "visibility": "shared",
    "when_to_use": "before deploy",
    "created_at": "2026-06-19T10:00:00",
}


class _ListResult:
    """Fake SQLAlchemy result for list queries — supports .mappings().all()."""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    def mappings(self) -> "_ListResult":
        return self

    def all(self) -> list:
        return self._rows


class _DeleteResult:
    """Fake SQLAlchemy result for DELETE — carries .rowcount."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


def _make_read_scope(rows: list):
    captured: Dict[str, Any] = {}

    class _Session:
        async def execute(self, stmt, params=None):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _ListResult(rows)

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    return _Scope(), captured


def _make_write_scope(rowcount: int):
    captured: Dict[str, Any] = {}

    class _Session:
        async def execute(self, stmt, params=None):
            captured["sql"] = str(stmt)
            captured["params"] = params
            return _DeleteResult(rowcount)

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    return _Scope(), captured


# ---------------------------------------------------------------------------
# list_user_memories — binds + returns dicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_user_memories_binds_user_id_and_team_ids():
    """list_user_memories passes user_id and team_ids to the SQL query."""
    from app.repositories.agent_memory_repository import list_user_memories

    scope, captured = _make_read_scope([_ROW_OWN])

    with patch("app.repositories.agent_memory_repository.read_scope", return_value=scope):
        await list_user_memories(user_id="user-abc", team_ids=[7, 8])

    assert captured["params"]["user_id"] == "user-abc"
    assert captured["params"]["team_ids"] == [7, 8]


@pytest.mark.asyncio
async def test_list_user_memories_returns_mapped_dicts():
    """list_user_memories returns a list of plain dicts."""
    from app.repositories.agent_memory_repository import list_user_memories

    scope, _ = _make_read_scope([_ROW_OWN, _ROW_SHARED])

    with patch("app.repositories.agent_memory_repository.read_scope", return_value=scope):
        result = await list_user_memories(user_id="user-abc", team_ids=[7])

    assert len(result) == 2
    assert result[0]["id"] == 10
    assert result[0]["owner_user_id"] == "user-abc"
    assert result[1]["id"] == 20
    assert result[1]["owner_user_id"] == "user-other"


@pytest.mark.asyncio
async def test_list_user_memories_isolation_sql_predicate():
    """The WHERE predicate uses isolation: owner OR shared team."""
    from app.repositories.agent_memory_repository import list_user_memories

    scope, captured = _make_read_scope([])

    with patch("app.repositories.agent_memory_repository.read_scope", return_value=scope):
        await list_user_memories(user_id="user-abc", team_ids=[7])

    sql = captured["sql"]
    assert "owner_user_id" in sql
    assert "visibility" in sql
    assert "team_id" in sql


@pytest.mark.asyncio
async def test_list_user_memories_returns_empty_on_error():
    """list_user_memories returns [] on any DB error — never raises."""
    from app.repositories.agent_memory_repository import list_user_memories

    with patch(
        "app.repositories.agent_memory_repository.read_scope",
        side_effect=RuntimeError("db down"),
    ):
        result = await list_user_memories(user_id="user-abc", team_ids=[])

    assert result == []


@pytest.mark.asyncio
async def test_list_user_memories_execute_error_returns_empty():
    """An error during session.execute also returns []."""
    from app.repositories.agent_memory_repository import list_user_memories

    class _BrokenSession:
        async def execute(self, stmt, params=None):
            raise RuntimeError("query error")

    class _Scope:
        async def __aenter__(self):
            return _BrokenSession()

        async def __aexit__(self, *a):
            return False

    with patch("app.repositories.agent_memory_repository.read_scope", return_value=_Scope()):
        result = await list_user_memories(user_id="user-abc", team_ids=[1])

    assert result == []


# ---------------------------------------------------------------------------
# delete_user_memory — owner-scoped DELETE + rowcount
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_user_memory_returns_true_when_row_deleted():
    """delete_user_memory returns True when rowcount > 0."""
    from app.repositories.agent_memory_repository import delete_user_memory

    scope, captured = _make_write_scope(1)

    with patch("app.repositories.agent_memory_repository.write_scope", return_value=scope):
        result = await delete_user_memory(memory_id=10, user_id="user-abc")

    assert result is True


@pytest.mark.asyncio
async def test_delete_user_memory_returns_false_when_not_found():
    """delete_user_memory returns False when rowcount == 0 (not owner or not found)."""
    from app.repositories.agent_memory_repository import delete_user_memory

    scope, _ = _make_write_scope(0)

    with patch("app.repositories.agent_memory_repository.write_scope", return_value=scope):
        result = await delete_user_memory(memory_id=999, user_id="user-abc")

    assert result is False


@pytest.mark.asyncio
async def test_delete_user_memory_binds_id_and_user_id():
    """DELETE binds :id and :user_id for owner-scoped isolation."""
    from app.repositories.agent_memory_repository import delete_user_memory

    scope, captured = _make_write_scope(1)

    with patch("app.repositories.agent_memory_repository.write_scope", return_value=scope):
        await delete_user_memory(memory_id=10, user_id="user-abc")

    assert captured["params"]["id"] == 10
    assert captured["params"]["user_id"] == "user-abc"


@pytest.mark.asyncio
async def test_delete_user_memory_sql_has_owner_filter():
    """The DELETE SQL filters by both id AND owner_user_id."""
    from app.repositories.agent_memory_repository import delete_user_memory

    scope, captured = _make_write_scope(1)

    with patch("app.repositories.agent_memory_repository.write_scope", return_value=scope):
        await delete_user_memory(memory_id=10, user_id="user-abc")

    sql = captured["sql"]
    assert "owner_user_id" in sql


@pytest.mark.asyncio
async def test_delete_user_memory_returns_false_on_error():
    """delete_user_memory returns False on any error — never raises."""
    from app.repositories.agent_memory_repository import delete_user_memory

    with patch(
        "app.repositories.agent_memory_repository.write_scope",
        side_effect=RuntimeError("db down"),
    ):
        result = await delete_user_memory(memory_id=10, user_id="user-abc")

    assert result is False


@pytest.mark.asyncio
async def test_delete_user_memory_never_raises_on_execute_error():
    """An error during session.execute also returns False (never raises)."""
    from app.repositories.agent_memory_repository import delete_user_memory

    class _BrokenSession:
        async def execute(self, stmt, params=None):
            raise RuntimeError("constraint error")

    class _Scope:
        async def __aenter__(self):
            return _BrokenSession()

        async def __aexit__(self, *a):
            return False

    with patch("app.repositories.agent_memory_repository.write_scope", return_value=_Scope()):
        result = await delete_user_memory(memory_id=10, user_id="user-abc")

    assert result is False
