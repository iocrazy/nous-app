"""POST /tasks/{task_id}/retry must respect the media-parser switch.

Third dispatch path into download_workflow (after /media/fetch and
/media/retry/{platform_id}). This router hosts every task type, so the gate is
inside the endpoint and only for ``task_type == "download"``.

Direct-function-call pattern (no TestClient fixture in this codebase).
"""

from __future__ import annotations

import importlib

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def _clear_gate_cache():
    from app.core.cache import module_gate_cache

    module_gate_cache.clear()
    yield
    module_gate_cache.clear()


def _auth(user_id: str = "user-1") -> MagicMock:
    auth = MagicMock()
    auth.user_id = user_id
    return auth


@pytest.mark.asyncio
async def test_download_retry_blocked_when_media_parser_disabled(monkeypatch):
    # The api package re-exports the router object under the module's own
    # name, so `import app.api.task_manager_router as tm` would bind the
    # APIRouter, not the module.
    tm = importlib.import_module("app.api.task_manager_router")

    monkeypatch.setattr(tm, "_peek_task_type", AsyncMock(return_value="download"))
    tracker = MagicMock()
    tracker.retry_task = AsyncMock(return_value={"task_type": "download"})
    monkeypatch.setattr(tm, "get_task_manager", lambda: tracker)

    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value={"enabled": False, "visible": False}),
    ):
        with pytest.raises(HTTPException) as exc:
            await tm.retry_task("task-1", _auth(), MagicMock())

    assert exc.value.status_code == 503
    assert exc.value.detail == {"code": "MODULE_DISABLED", "module": "media-parser"}
    # Rejected BEFORE the row was re-keyed — otherwise the task would sit in
    # QUEUED pointing at a workflow that never starts.
    tracker.retry_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_download_retry_is_not_blocked(monkeypatch):
    """Other task types must stay retryable while media-parser is off."""
    # The api package re-exports the router object under the module's own
    # name, so `import app.api.task_manager_router as tm` would bind the
    # APIRouter, not the module.
    tm = importlib.import_module("app.api.task_manager_router")

    monkeypatch.setattr(tm, "_peek_task_type", AsyncMock(return_value="transcode"))
    tracker = MagicMock()
    # None => endpoint raises its own 404; reaching that proves the gate let
    # this task type through instead of short-circuiting with 503.
    tracker.retry_task = AsyncMock(return_value=None)
    monkeypatch.setattr(tm, "get_task_manager", lambda: tracker)

    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value={"enabled": False, "visible": False}),
    ):
        with pytest.raises(HTTPException) as exc:
            await tm.retry_task("task-2", _auth(), MagicMock())

    assert exc.value.status_code == 404
    tracker.retry_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_retry_proceeds_when_media_parser_enabled(monkeypatch):
    # The api package re-exports the router object under the module's own
    # name, so `import app.api.task_manager_router as tm` would bind the
    # APIRouter, not the module.
    tm = importlib.import_module("app.api.task_manager_router")

    monkeypatch.setattr(tm, "_peek_task_type", AsyncMock(return_value="download"))
    tracker = MagicMock()
    tracker.retry_task = AsyncMock(return_value=None)
    monkeypatch.setattr(tm, "get_task_manager", lambda: tracker)

    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value={"enabled": True, "visible": True}),
    ):
        with pytest.raises(HTTPException) as exc:
            await tm.retry_task("task-3", _auth(), MagicMock())

    assert exc.value.status_code == 404
    tracker.retry_task.assert_awaited_once()
