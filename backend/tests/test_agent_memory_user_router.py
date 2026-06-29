"""User-facing agent-memory endpoint tests (Task 1 — TDD RED phase).

Tests for:
  GET  /agent-memory      → list_my_memories
  DELETE /agent-memory/{memory_id} → delete_my_memory

in ``app.api.agent_memory_user_router``.

Pattern mirrors test_admin_memory_promotions.py:
  patch repo functions at the router import site, pass a MagicMock auth
  with ``.user_id``, call each handler directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OWN_ROW = {
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

_SHARED_ROW = {
    "id": 20,
    "owner_user_id": "user-other-uuid",
    "title": "Team deploy process",
    "body_md": "Deploy every Friday.",
    "kind": "fact",
    "scope": "team",
    "visibility": "shared",
    "when_to_use": "before deploy",
    "created_at": "2026-06-19T10:00:00",
}


def _make_auth(user_id: str = "user-abc") -> MagicMock:
    auth = MagicMock()
    auth.user_id = user_id
    return auth


# ---------------------------------------------------------------------------
# T1: list_my_memories — calls get_user_team_ids + list_user_memories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_memories_calls_get_user_team_ids_with_auth_user_id():
    """list_my_memories must call get_user_team_ids(auth.user_id) server-side."""
    from app.api.agent_memory_user_router import list_my_memories

    auth = _make_auth("user-abc")
    mock_team_ids = AsyncMock(return_value=[7, 8])
    mock_list = AsyncMock(return_value=[])

    with (
        patch("app.api.agent_memory_user_router.get_user_team_ids", mock_team_ids),
        patch("app.api.agent_memory_user_router.list_user_memories", mock_list),
    ):
        await list_my_memories(auth=auth)

    mock_team_ids.assert_called_once_with("user-abc")


@pytest.mark.asyncio
async def test_list_memories_calls_list_with_server_team_ids():
    """list_user_memories must be called with the server-side team_ids (not a client value)."""
    from app.api.agent_memory_user_router import list_my_memories

    auth = _make_auth("user-abc")
    mock_team_ids = AsyncMock(return_value=[7, 8])
    mock_list = AsyncMock(return_value=[])

    with (
        patch("app.api.agent_memory_user_router.get_user_team_ids", mock_team_ids),
        patch("app.api.agent_memory_user_router.list_user_memories", mock_list),
    ):
        await list_my_memories(auth=auth)

    mock_list.assert_called_once_with(user_id="user-abc", team_ids=[7, 8])


@pytest.mark.asyncio
async def test_list_memories_is_owner_true_for_own_row():
    """is_owner must be True when the row owner matches auth.user_id."""
    from app.api.agent_memory_user_router import list_my_memories

    auth = _make_auth("user-abc")
    mock_team_ids = AsyncMock(return_value=[])
    mock_list = AsyncMock(return_value=[_OWN_ROW])

    with (
        patch("app.api.agent_memory_user_router.get_user_team_ids", mock_team_ids),
        patch("app.api.agent_memory_user_router.list_user_memories", mock_list),
    ):
        result = await list_my_memories(auth=auth)

    assert len(result.items) == 1
    assert result.items[0].is_owner is True
    assert result.items[0].id == 10
    assert result.items[0].title == "My preference"


@pytest.mark.asyncio
async def test_list_memories_is_owner_false_for_teammates_row():
    """is_owner must be False when the row owner differs from auth.user_id."""
    from app.api.agent_memory_user_router import list_my_memories

    auth = _make_auth("user-abc")
    mock_team_ids = AsyncMock(return_value=[7])
    mock_list = AsyncMock(return_value=[_SHARED_ROW])

    with (
        patch("app.api.agent_memory_user_router.get_user_team_ids", mock_team_ids),
        patch("app.api.agent_memory_user_router.list_user_memories", mock_list),
    ):
        result = await list_my_memories(auth=auth)

    assert len(result.items) == 1
    assert result.items[0].is_owner is False


@pytest.mark.asyncio
async def test_list_memories_no_owner_uuid_leak():
    """The response must NOT contain the teammate's owner_user_id UUID."""
    from app.api.agent_memory_user_router import list_my_memories

    auth = _make_auth("user-abc")
    mock_team_ids = AsyncMock(return_value=[7])
    mock_list = AsyncMock(return_value=[_SHARED_ROW])

    with (
        patch("app.api.agent_memory_user_router.get_user_team_ids", mock_team_ids),
        patch("app.api.agent_memory_user_router.list_user_memories", mock_list),
    ):
        result = await list_my_memories(auth=auth)

    # Serialize the response and check the teammate UUID is not present
    serialized = result.model_dump()
    serialized_str = str(serialized)
    assert "user-other-uuid" not in serialized_str


@pytest.mark.asyncio
async def test_list_memories_mixed_own_and_shared():
    """Both own (is_owner=True) and shared (is_owner=False) rows are handled in one response."""
    from app.api.agent_memory_user_router import list_my_memories

    auth = _make_auth("user-abc")
    mock_team_ids = AsyncMock(return_value=[7])
    mock_list = AsyncMock(return_value=[_OWN_ROW, _SHARED_ROW])

    with (
        patch("app.api.agent_memory_user_router.get_user_team_ids", mock_team_ids),
        patch("app.api.agent_memory_user_router.list_user_memories", mock_list),
    ):
        result = await list_my_memories(auth=auth)

    assert len(result.items) == 2
    assert result.items[0].is_owner is True
    assert result.items[1].is_owner is False


@pytest.mark.asyncio
async def test_list_memories_empty_when_no_rows():
    """Empty list from the repo returns UserMemoryListResponse with empty items."""
    from app.api.agent_memory_user_router import list_my_memories

    auth = _make_auth("user-abc")
    mock_team_ids = AsyncMock(return_value=[])
    mock_list = AsyncMock(return_value=[])

    with (
        patch("app.api.agent_memory_user_router.get_user_team_ids", mock_team_ids),
        patch("app.api.agent_memory_user_router.list_user_memories", mock_list),
    ):
        result = await list_my_memories(auth=auth)

    assert result.items == []


# ---------------------------------------------------------------------------
# T2: delete_my_memory — forwards memory_id + user_id=auth.user_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_forwards_memory_id_and_user_id():
    """delete_my_memory must call delete_user_memory with memory_id AND user_id=auth.user_id."""
    from app.api.agent_memory_user_router import delete_my_memory

    auth = _make_auth("user-abc")
    mock_delete = AsyncMock(return_value=True)

    with patch("app.api.agent_memory_user_router.delete_user_memory", mock_delete):
        result = await delete_my_memory(memory_id=10, auth=auth)

    mock_delete.assert_called_once_with(memory_id=10, user_id="user-abc")
    assert result == {"deleted": True}


@pytest.mark.asyncio
async def test_delete_returns_false_when_not_owned():
    """delete_user_memory returns False → response is {"deleted": False}."""
    from app.api.agent_memory_user_router import delete_my_memory

    auth = _make_auth("user-abc")
    mock_delete = AsyncMock(return_value=False)

    with patch("app.api.agent_memory_user_router.delete_user_memory", mock_delete):
        result = await delete_my_memory(memory_id=999, auth=auth)

    assert result == {"deleted": False}


@pytest.mark.asyncio
async def test_delete_uses_auth_user_id_not_caller_supplied():
    """The user_id forwarded to the repo must come from auth.user_id, not a param."""
    from app.api.agent_memory_user_router import delete_my_memory

    auth = _make_auth("user-secure-id")
    mock_delete = AsyncMock(return_value=True)

    with patch("app.api.agent_memory_user_router.delete_user_memory", mock_delete):
        await delete_my_memory(memory_id=10, auth=auth)

    _, kwargs = mock_delete.call_args
    assert kwargs["user_id"] == "user-secure-id"
