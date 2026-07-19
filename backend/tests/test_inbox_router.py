"""Unit tests for the inbox router (W3d) — own-rows-only 404 + read/read-all.

The endpoints are called directly with a fake repository and a constructed
AuthContext, so the ownership/404 logic is asserted without a TestClient or DB.
"""

from __future__ import annotations

import importlib
from typing import Any, Optional

import pytest
from fastapi import HTTPException

from app.core.deps import AuthContext

# NOTE: `import app.api.inbox_router as router_mod` would resolve to the shadowed
# APIRouter attribute (app/api/__init__ rebinds `inbox_router` to the router
# object), so grab the real module via importlib.
router_mod = importlib.import_module("app.api.inbox_router")


class _FakeRepo:
    def __init__(self, owner: Optional[str] = None) -> None:
        self._owner = owner
        self.marked: list[str] = []
        self.mark_all_called = False

    async def list_notifications(self, user_id: str, **kw: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "1",
                "kind": "generation_result",
                "title": "Download ready",
                "body": None,
                "severity": "success",
                "link_kind": "resource",
                "link_id": "900",
                "team_id": None,
                "read": False,
                "read_at": None,
                "created_at": "2026-07-18T00:00:00+00:00",
            }
        ]

    async def unread_count(self, user_id: str) -> int:
        return 1

    async def get_owner(self, notification_id: str) -> Optional[str]:
        return self._owner

    async def mark_read(self, notification_id: str, user_id: str) -> bool:
        self.marked.append(notification_id)
        return True

    async def mark_all_read(self, user_id: str) -> int:
        self.mark_all_called = True
        return 3


def _auth(user_id: str = "user-1") -> AuthContext:
    return AuthContext(user_id=user_id, auth_type="jwt")


@pytest.fixture
def patch_repo(monkeypatch: pytest.MonkeyPatch):
    def _install(repo: _FakeRepo) -> _FakeRepo:
        monkeypatch.setattr(router_mod, "get_inbox_repository", lambda: repo)
        return repo

    return _install


@pytest.mark.asyncio
async def test_list_returns_own_rows_and_unread_count(patch_repo) -> None:
    patch_repo(_FakeRepo())
    resp = await router_mod.list_inbox(_auth(), unread_only=False, limit=50, offset=0)
    assert resp.total == 1
    assert resp.unread_count == 1
    assert resp.notifications[0].kind == "generation_result"
    assert resp.notifications[0].id == "1"


@pytest.mark.asyncio
async def test_mark_read_own_row_succeeds(patch_repo) -> None:
    repo = patch_repo(_FakeRepo(owner="user-1"))
    resp = await router_mod.mark_inbox_read("1", _auth("user-1"))
    assert resp.success is True
    assert repo.marked == ["1"]


@pytest.mark.asyncio
async def test_mark_read_foreign_row_is_404(patch_repo) -> None:
    repo = patch_repo(_FakeRepo(owner="someone-else"))
    with pytest.raises(HTTPException) as ei:
        await router_mod.mark_inbox_read("1", _auth("user-1"))
    assert ei.value.status_code == 404
    assert repo.marked == []  # never mutated a foreign row


@pytest.mark.asyncio
async def test_mark_read_absent_row_is_404(patch_repo) -> None:
    repo = patch_repo(_FakeRepo(owner=None))
    with pytest.raises(HTTPException) as ei:
        await router_mod.mark_inbox_read("999", _auth("user-1"))
    assert ei.value.status_code == 404
    assert repo.marked == []


@pytest.mark.asyncio
async def test_mark_all_read(patch_repo) -> None:
    repo = patch_repo(_FakeRepo())
    resp = await router_mod.mark_all_inbox_read(_auth("user-1"))
    assert resp.success is True
    assert "3" in resp.message
    assert repo.mark_all_called is True
