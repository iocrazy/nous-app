"""G1+G5 — CRUD endpoint smoke tests for /api/v1/ai-library/mcp-servers.

Mocks the repository layer; verifies the route layer's auth wiring,
response shaping (token masking), and ownership checks on
PATCH/DELETE.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

import sys

# The package __init__ rebinds `app.api.ai_library_router` to the
# router instance (`from ... import router as ai_library_router`).
# Force-load the module file via sys.modules so we can call the
# top-level handler functions directly.
import app.api.ai_library_router  # noqa: F401 — populates sys.modules
router_module = sys.modules["app.api.ai_library_router"]
from app.repositories.user_mcp_servers_repository import UserMCPServer


def _row(user_id, *, name="srv1", url="https://x.com/jsonrpc",
         token="sekret", desc="d", enabled=True):
    return UserMCPServer(
        id=uuid4(),
        user_id=user_id,
        name=name,
        url=url,
        bearer_token=token,
        description=desc,
        enabled=enabled,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_masks_token():
    """list endpoint must NOT return bearer_token; only has_bearer_token bool."""
    user_id = uuid4()
    rows = [_row(user_id, name="notion", token="secret123")]

    fake_repo = MagicMock()
    fake_repo.list_for_user = AsyncMock(return_value=rows)

    auth = MagicMock()
    auth.user_id = str(user_id)

    with patch(
        "app.repositories.user_mcp_servers_repository.UserMCPServersRepository",
        return_value=fake_repo,
    ):
        out = await router_module.list_mcp_servers(auth)

    assert out["count"] == 1
    item = out["items"][0]
    assert "bearer_token" not in item
    assert item["has_bearer_token"] is True
    assert item["name"] == "notion"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_masks_token_absence_correctly():
    """has_bearer_token=false when token is None."""
    user_id = uuid4()
    rows = [_row(user_id, name="public_srv", token=None)]
    fake_repo = MagicMock()
    fake_repo.list_for_user = AsyncMock(return_value=rows)

    auth = MagicMock()
    auth.user_id = str(user_id)

    with patch(
        "app.repositories.user_mcp_servers_repository.UserMCPServersRepository",
        return_value=fake_repo,
    ):
        out = await router_module.list_mcp_servers(auth)

    assert out["items"][0]["has_bearer_token"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_returns_masked_row():
    user_id = uuid4()
    new_row = _row(user_id, name="linear", token="t1")

    fake_repo = MagicMock()
    fake_repo.create = AsyncMock(return_value=new_row)

    auth = MagicMock()
    auth.user_id = str(user_id)

    payload = router_module._MCPServerCreate(
        name="linear",
        url="https://mcp.linear.test/jsonrpc",
        bearer_token="t1",
    )
    with patch(
        "app.repositories.user_mcp_servers_repository.UserMCPServersRepository",
        return_value=fake_repo,
    ):
        out = await router_module.create_mcp_server(payload, auth)

    assert out["name"] == "linear"
    assert "bearer_token" not in out
    assert out["has_bearer_token"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_bubbles_repo_error_as_409():
    fake_repo = MagicMock()
    fake_repo.create = AsyncMock(side_effect=Exception("UNIQUE constraint"))
    auth = MagicMock()
    auth.user_id = str(uuid4())
    payload = router_module._MCPServerCreate(
        name="dup", url="https://x.com",
    )
    with patch(
        "app.repositories.user_mcp_servers_repository.UserMCPServersRepository",
        return_value=fake_repo,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await router_module.create_mcp_server(payload, auth)
        assert exc_info.value.status_code == 409


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_404s_when_not_owner():
    """Patching another user's server must 404, not 403, to avoid
    leaking the existence of the row."""
    owner_id = uuid4()
    intruder_id = uuid4()
    row_id = uuid4()
    foreign_row = _row(owner_id)

    fake_repo = MagicMock()
    fake_repo.get_by_id = AsyncMock(return_value=foreign_row)

    auth = MagicMock()
    auth.user_id = str(intruder_id)

    payload = router_module._MCPServerUpdate(enabled=False)
    with patch(
        "app.repositories.user_mcp_servers_repository.UserMCPServersRepository",
        return_value=fake_repo,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await router_module.update_mcp_server(row_id, payload, auth)
        assert exc_info.value.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_404s_when_row_missing():
    fake_repo = MagicMock()
    fake_repo.get_by_id = AsyncMock(return_value=None)
    auth = MagicMock()
    auth.user_id = str(uuid4())
    payload = router_module._MCPServerUpdate(enabled=False)
    with patch(
        "app.repositories.user_mcp_servers_repository.UserMCPServersRepository",
        return_value=fake_repo,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await router_module.update_mcp_server(uuid4(), payload, auth)
        assert exc_info.value.status_code == 404


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_returns_fresh_row():
    user_id = uuid4()
    row_id = uuid4()
    own_row = _row(user_id, name="srv", url="https://old.com")
    updated_row = _row(user_id, name="srv", url="https://new.com")

    fake_repo = MagicMock()
    # First get_by_id (ownership check) returns the original
    # Second get_by_id (fresh fetch after update) returns the updated row
    fake_repo.get_by_id = AsyncMock(side_effect=[own_row, updated_row])
    fake_repo.update = AsyncMock(return_value=True)

    auth = MagicMock()
    auth.user_id = str(user_id)

    payload = router_module._MCPServerUpdate(url="https://new.com")
    with patch(
        "app.repositories.user_mcp_servers_repository.UserMCPServersRepository",
        return_value=fake_repo,
    ):
        out = await router_module.update_mcp_server(row_id, payload, auth)

    assert out["url"] == "https://new.com"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_404s_when_not_owner():
    owner_id = uuid4()
    intruder_id = uuid4()
    foreign_row = _row(owner_id)

    fake_repo = MagicMock()
    fake_repo.get_by_id = AsyncMock(return_value=foreign_row)
    fake_repo.delete = AsyncMock()

    auth = MagicMock()
    auth.user_id = str(intruder_id)

    with patch(
        "app.repositories.user_mcp_servers_repository.UserMCPServersRepository",
        return_value=fake_repo,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await router_module.delete_mcp_server(uuid4(), auth)
        assert exc_info.value.status_code == 404
    # The actual delete must NOT have been called
    fake_repo.delete.assert_not_called()
