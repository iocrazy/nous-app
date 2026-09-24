"""A human reply on an issue whose assignee agent cannot be resolved is a
typed 409, not a 500 (FH2 T5 review H1).

The rebind at session lookup raises IssueAssigneeNotFound; the reply route must
say so in the production ``ErrorResponse`` envelope — the frontend reads
``details.code`` (apiClient.ts), so the assertion reads it too. A bare
``FastAPI()`` would answer ``{"detail": …}``, a shape that never ships.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import register_exception_handlers

pytestmark = pytest.mark.unit

r = importlib.import_module("app.api.issue_messages_router")
ME = "11111111-1111-1111-1111-111111111111"
AGENT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


async def test_reply_with_unresolvable_assignee_is_a_typed_409(monkeypatch):
    from app.core.deps import get_auth
    from app.services.issues.issue_session import IssueAssigneeNotFound

    issue_row = {
        "id": 5,
        "assignee_agent_id": AGENT,
        "created_by_user_id": ME,
        "assignee_user_id": None,
        "paused_at": None,
        "execution_state": {},
    }
    monkeypatch.setattr(r, "_assert_issue_visible", AsyncMock(return_value=issue_row))
    monkeypatch.setattr(
        r,
        "get_or_create_issue_session",
        AsyncMock(side_effect=IssueAssigneeNotFound(5, AGENT)),
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(r.router, prefix="/api/v1")

    async def _grant():
        return SimpleNamespace(user_id=ME)

    app.dependency_overrides[get_auth] = _grant

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/api/v1/issues/5/messages", json={"body": "go on"})

    assert resp.status_code == 409
    body = resp.json()
    assert body["success"] is False
    assert body["details"]["code"] == "assignee_not_found"
    assert "reassign the issue" in body["details"]["message"]
