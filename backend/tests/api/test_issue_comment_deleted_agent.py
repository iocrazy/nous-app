"""Comments on an issue whose assignee was soft-deleted (mig 501, review H1).

Hard delete used to null ``issues.assignee_agent_id`` (FK SET NULL), so such a
comment took the legacy path and was stored. Soft delete keeps the id, and
the 409 ``agent_in_use`` guard deliberately ignores done / cancelled / hidden
issues — so they still point at the deleted agent. Without a guard the comment
predicate said "wake", the endpoint answered with an optimistic comment, and
the turn then refused (``agent_deleted``) BEFORE writing the user message:
the comment body was lost.

Now the send path refuses synchronously with 409 ``agent_deleted`` (the client
keeps its input), before a session is created or anything is dispatched, and
the preview says ``will_wake=False`` with ``agent_deleted=True``.
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException

import app.services.issues.issue_reply_dispatch as _dispatch_mod
from app.schemas.issue_message import CommentTriggerPreviewRequest, IssueMessagePost
from app.services.issues.comment_trigger import (
    apply_agent_deleted,
    compute_comment_trigger,
)

_AGENT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_OWNER = "11111111-1111-1111-1111-111111111111"


def _router():
    importlib.import_module("app.api.issue_messages_router")
    return sys.modules["app.api.issue_messages_router"]


def _row(**over) -> dict:
    return {
        "id": 5,
        "status": "done",
        "assignee_agent_id": _AGENT,
        "created_by_user_id": _OWNER,
        "assignee_user_id": None,
        "ai_session_id": None,
        **over,
    }


def _auth():
    return SimpleNamespace(user_id=UUID(_OWNER))


def _wire(monkeypatch, r, *, deleted: bool):
    session = AsyncMock(return_value="900001")
    monkeypatch.setattr(r, "_assert_issue_visible", AsyncMock(return_value=_row()))
    monkeypatch.setattr(r, "get_or_create_issue_session", session)
    monkeypatch.setattr(r, "is_agent_soft_deleted", AsyncMock(return_value=deleted))
    return session


def test_apply_agent_deleted_turns_off_the_wake_and_keeps_the_name():
    v = apply_agent_deleted(compute_comment_trigger(_row(), "hi"), deleted=True)
    assert v.will_wake is False
    assert v.agent_deleted is True
    assert v.agent_id == _AGENT
    live = apply_agent_deleted(compute_comment_trigger(_row(), "hi"), deleted=False)
    assert live.will_wake is True and live.agent_deleted is False


@pytest.mark.asyncio
async def test_preview_reports_no_wake_for_deleted_assignee(monkeypatch):
    r = _router()
    _wire(monkeypatch, r, deleted=True)
    p = await r.comment_trigger_preview(5, CommentTriggerPreviewRequest(), _auth())
    assert p.will_wake is False
    assert p.agent_deleted is True
    assert p.agent_id == _AGENT


@pytest.mark.parametrize("body", ["please revisit", "/note for the record"])
@pytest.mark.asyncio
async def test_post_on_done_issue_of_deleted_agent_is_409_and_dispatches_nothing(
    monkeypatch, body
):
    r = _router()
    session = _wire(monkeypatch, r, deleted=True)
    started = {}
    append = AsyncMock()
    with (
        patch.object(
            _dispatch_mod,
            "DBOS",
            SimpleNamespace(start_workflow=lambda fn, *a: started.update(fn=fn)),
        ),
        patch.object(r.ConversationsAiStore, "append_user_message", append),
    ):
        with pytest.raises(HTTPException) as info:
            await r.post_issue_message(5, IssueMessagePost(body=body), _auth())
    assert info.value.status_code == 409
    assert info.value.detail["code"] == "agent_deleted"
    assert "Reassign" in info.value.detail["message"]
    assert started == {}, "nothing may be dispatched"
    session.assert_not_awaited()
    append.assert_not_awaited()


@pytest.mark.asyncio
async def test_issue_session_create_refuses_a_deleted_assignee(monkeypatch):
    """The create branch must not lean on create_session's 404 (slug lookup
    filters deleted rows): a tombstone from get_by_id is a typed refusal."""
    from contextlib import asynccontextmanager

    from app.services.issues import issue_session as mod

    class _Res:
        def mappings(self):
            return self

        def first(self):
            return {
                "ai_session_id": None,
                "title": "T",
                "assignee_agent_id": _AGENT,
                "created_by_user_id": _OWNER,
                "assignee_user_id": None,
                "project_id": None,
                "team_id": None,
            }

    class _Sess:
        async def execute(self, _stmt):
            return _Res()

    @asynccontextmanager
    async def _scope():
        yield _Sess()

    repo = SimpleNamespace(
        get_by_id=AsyncMock(
            return_value={"id": _AGENT, "slug": "gone", "deleted_at": "2026-09-23"}
        )
    )
    create = AsyncMock()
    monkeypatch.setattr("app.db.session.read_scope", _scope)
    monkeypatch.setattr(mod, "get_agent_repository", lambda: repo)
    monkeypatch.setattr(mod.AILibraryChatService, "create_session", create)
    with pytest.raises(mod.IssueAssigneeDeleted) as info:
        await mod.get_or_create_issue_session(5)
    assert info.value.code == "agent_deleted"
    create.assert_not_awaited()
