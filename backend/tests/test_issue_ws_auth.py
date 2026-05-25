"""Tests for the _resolve_issue_ws_user auth+visibility helper in ws_router."""

from unittest.mock import AsyncMock

import pytest

from app.api import ws_router as w


@pytest.mark.asyncio
async def test_resolve_denies_when_not_visible(monkeypatch):
    monkeypatch.setattr(w, "_authenticate_ws", AsyncMock(return_value="user-9"))
    monkeypatch.setattr(
        w.issue_repository,
        "get_by_id",
        AsyncMock(
            return_value={"created_by_user_id": "other", "assignee_user_id": None}
        ),
    )
    assert await w._resolve_issue_ws_user(5, None, "jwt") is None


@pytest.mark.asyncio
async def test_resolve_allows_creator(monkeypatch):
    monkeypatch.setattr(w, "_authenticate_ws", AsyncMock(return_value="user-9"))
    monkeypatch.setattr(
        w.issue_repository,
        "get_by_id",
        AsyncMock(
            return_value={"created_by_user_id": "user-9", "assignee_user_id": None}
        ),
    )
    assert await w._resolve_issue_ws_user(5, None, "jwt") == "user-9"


@pytest.mark.asyncio
async def test_resolve_denies_bad_auth(monkeypatch):
    monkeypatch.setattr(w, "_authenticate_ws", AsyncMock(return_value=None))
    assert await w._resolve_issue_ws_user(5, None, "bad") is None


@pytest.mark.asyncio
async def test_resolve_allows_assignee(monkeypatch):
    """Assignee (not creator) must also be granted visibility."""
    monkeypatch.setattr(w, "_authenticate_ws", AsyncMock(return_value="user-9"))
    monkeypatch.setattr(
        w.issue_repository,
        "get_by_id",
        AsyncMock(
            return_value={"created_by_user_id": "other", "assignee_user_id": "user-9"}
        ),
    )
    assert await w._resolve_issue_ws_user(5, None, "jwt") == "user-9"
