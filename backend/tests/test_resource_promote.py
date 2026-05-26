# backend/tests/test_resource_promote.py
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api import resources_crud_router as r
from app.schemas.resources import ResourceUpdate


class _Auth:
    def __init__(self, user_id="u1"):
        self.user_id = user_id


def _patch_repo(monkeypatch, *, resource, folder=None, updated=None):
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)
    repo.get_folder_by_id = AsyncMock(return_value=folder)
    repo.update_resource = AsyncMock(
        return_value=updated
        or {
            **resource,
            **({"folder_id": "f-target"} if folder else {"folder_id": None}),
        }
    )
    monkeypatch.setattr(r, "ResourcesRepository", lambda: repo)
    return repo


@pytest.mark.asyncio
async def test_promote_to_root_clears_folder_id(monkeypatch):
    resource = {
        "id": "res-1",
        "scope_type": "personal",
        "scope_id": "u1",
        "folder_id": "f-temp",
    }
    repo = _patch_repo(monkeypatch, resource=resource)
    out = await r.update_resource("res-1", ResourceUpdate(folder_id=None), _Auth())
    assert out["success"] is True
    repo.update_resource.assert_awaited_once()
    args, _ = repo.update_resource.call_args
    assert args[0] == "res-1"
    assert args[1].get("folder_id") is None


@pytest.mark.asyncio
async def test_promote_to_same_scope_folder_succeeds(monkeypatch):
    resource = {
        "id": "res-1",
        "scope_type": "team",
        "scope_id": "42",
        "folder_id": "f-temp",
    }
    target_folder = {"id": "f-target", "scope_type": "team", "scope_id": "42"}
    _patch_repo(monkeypatch, resource=resource, folder=target_folder)
    out = await r.update_resource(
        "res-1", ResourceUpdate(folder_id="f-target"), _Auth()
    )
    assert out["success"] is True


@pytest.mark.asyncio
async def test_promote_to_other_scope_folder_rejected(monkeypatch):
    resource = {
        "id": "res-1",
        "scope_type": "personal",
        "scope_id": "u1",
        "folder_id": "f-temp",
    }
    other_scope_folder = {"id": "f-target", "scope_type": "team", "scope_id": "42"}
    _patch_repo(monkeypatch, resource=resource, folder=other_scope_folder)
    with pytest.raises(HTTPException) as e:
        await r.update_resource("res-1", ResourceUpdate(folder_id="f-target"), _Auth())
    assert e.value.status_code == 400
    assert "scope" in str(e.value.detail).lower()


@pytest.mark.asyncio
async def test_promote_to_missing_folder_rejected(monkeypatch):
    resource = {
        "id": "res-1",
        "scope_type": "personal",
        "scope_id": "u1",
        "folder_id": "f-temp",
    }
    _patch_repo(monkeypatch, resource=resource, folder=None)
    with pytest.raises(HTTPException) as e:
        await r.update_resource("res-1", ResourceUpdate(folder_id="f-ghost"), _Auth())
    assert e.value.status_code == 404
