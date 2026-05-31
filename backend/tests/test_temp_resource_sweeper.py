"""Tests for the temp_resource_sweeper DBOS scheduled workflow.

TDD: tests are written before the implementation.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.workflows import temp_resource_sweeper as m


def _resource(id_: str, days_old: int) -> dict:
    return {
        "id": id_,
        "created_at": (
            datetime.now(timezone.utc) - timedelta(days=days_old)
        ).isoformat(),
    }


@pytest.mark.asyncio
async def test_sweep_scope_soft_deletes_expired(monkeypatch):
    """Resources older than TTL get is_trashed=true; younger ones untouched."""
    fake_repo = MagicMock()
    fake_repo.get_folders = AsyncMock(return_value=[{"id": "f-temp", "name": "temp"}])
    fake_repo.list_resources_in_folder = AsyncMock(
        return_value=[
            _resource("r-old", days_old=45),
            _resource("r-young", days_old=5),
        ]
    )
    fake_repo.soft_delete_resource = AsyncMock()
    monkeypatch.setattr(m, "_build_repo", lambda: fake_repo)
    monkeypatch.setattr(m, "get_chat_temp_ttl_days", AsyncMock(return_value=30))

    deleted = await m._sweep_scope("personal", "u1")

    assert deleted == 1
    fake_repo.soft_delete_resource.assert_awaited_once_with("r-old")


@pytest.mark.asyncio
async def test_sweep_scope_with_never_ttl_skips(monkeypatch):
    fake_repo = MagicMock()
    fake_repo.get_folders = AsyncMock(return_value=[{"id": "f-temp", "name": "temp"}])
    fake_repo.list_resources_in_folder = AsyncMock()
    fake_repo.soft_delete_resource = AsyncMock()
    monkeypatch.setattr(m, "_build_repo", lambda: fake_repo)
    monkeypatch.setattr(m, "get_chat_temp_ttl_days", AsyncMock(return_value=None))

    deleted = await m._sweep_scope("team", "42")

    assert deleted == 0
    fake_repo.list_resources_in_folder.assert_not_called()
    fake_repo.soft_delete_resource.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_scope_no_temp_folder_skips(monkeypatch):
    fake_repo = MagicMock()
    fake_repo.get_folders = AsyncMock(return_value=[{"id": "f-other", "name": "Other"}])
    fake_repo.soft_delete_resource = AsyncMock()
    monkeypatch.setattr(m, "_build_repo", lambda: fake_repo)
    monkeypatch.setattr(m, "get_chat_temp_ttl_days", AsyncMock(return_value=30))

    assert await m._sweep_scope("personal", "u1") == 0
    fake_repo.soft_delete_resource.assert_not_called()


@pytest.mark.asyncio
async def test_iter_scopes_yields_all_personal_and_team(monkeypatch):
    """The sweeper enumerates every scope that owns a temp folder.

    PR-E 4c: a single query lists all temp folders and derives scope_type
    from teams.kind (folders.scope_type is being dropped). The iterator yields
    the (scope_type, scope_id) pairs from that query verbatim.
    """
    fake_fetch = AsyncMock(
        return_value=[
            {"scope_id": "pt1", "scope_type": "personal"},
            {"scope_id": "pt2", "scope_type": "personal"},
            {"scope_id": "42", "scope_type": "team"},
            {"scope_id": "99", "scope_type": "team"},
        ]
    )
    monkeypatch.setattr("app.db.engine.fetch_all", fake_fetch)
    scopes = [s async for s in m._iter_scopes()]
    assert set(scopes) == {
        ("personal", "pt1"),
        ("personal", "pt2"),
        ("team", "42"),
        ("team", "99"),
    }


@pytest.mark.asyncio
async def test_sweep_continues_on_per_scope_failure(monkeypatch):
    """A failure in one scope must not block the rest."""

    async def _iter():
        yield ("personal", "u1")
        yield ("team", "42")

    monkeypatch.setattr(m, "_iter_scopes", _iter)

    async def _sweep(scope_type, scope_id):
        if scope_type == "personal":
            raise RuntimeError("simulated DB hiccup")
        return 3

    monkeypatch.setattr(m, "_sweep_scope", _sweep)

    result = await m.sweep_temp_resources()
    # personal failed → counted as 0; team swept 3.
    assert result["scopes_swept"] == 1
    assert result["total_deleted"] == 3
    assert "duration_s" in result
