"""Spec-1b: post_issue_message routes session-backed issues to a reply turn."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.schemas.issue_message import IssueMessageKind, IssueMessagePost


def _router_module():
    """Return the issue_messages_router *module* (not the APIRouter object).

    app.api.__init__ does ``from … import router as issue_messages_router``,
    which shadows the submodule name in the package namespace.  We bypass that
    by resolving through sys.modules (populated by importlib on first access).
    """
    importlib.import_module("app.api.issue_messages_router")
    return sys.modules["app.api.issue_messages_router"]


@pytest.mark.asyncio
async def test_session_issue_dispatches_reply_workflow(monkeypatch):
    r = _router_module()

    issue_row = {
        "id": 5,
        "assignee_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "created_by_user_id": "11111111-1111-1111-1111-111111111111",
        "assignee_user_id": None,
    }
    monkeypatch.setattr(r, "_assert_issue_visible", AsyncMock(return_value=issue_row))
    monkeypatch.setattr(
        r, "get_or_create_issue_session", AsyncMock(return_value="sess-5")
    )

    started = {}

    def fake_start_workflow(fn, *args):
        started["fn"] = fn.__name__
        started["args"] = args

    auth = SimpleNamespace(user_id=UUID("11111111-1111-1111-1111-111111111111"))

    with (
        patch.object(r, "DBOS", SimpleNamespace(start_workflow=fake_start_workflow)),
        patch.object(r, "SetWorkflowID", lambda *_a, **_k: _nullctx()),
    ):
        resp = await r.post_issue_message(
            5, IssueMessagePost(body="please continue"), auth
        )

    assert started["fn"] == "respond_to_issue_reply"
    assert started["args"][0] == 5  # issue_id
    assert started["args"][1] == "11111111-1111-1111-1111-111111111111"  # owner
    assert started["args"][2] == "please continue"  # reply_text
    assert resp.comment.kind == IssueMessageKind.COMMENT
    assert resp.comment.body == "please continue"
    assert resp.agent_run is None


@pytest.mark.asyncio
async def test_legacy_issue_still_inserts_issue_messages(monkeypatch):
    r = _router_module()

    issue_row = {"id": 6, "assignee_agent_id": None}
    monkeypatch.setattr(r, "_assert_issue_visible", AsyncMock(return_value=issue_row))

    inserted = {"row": None}

    class _Tbl:
        def insert(self, row):
            inserted["row"] = row
            return self

        async def execute(self):
            return SimpleNamespace(
                data=[
                    {
                        "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                        "issue_id": 6,
                        "kind": "comment",
                        "author_user_id": "11111111-1111-1111-1111-111111111111",
                        "body": "hi",
                        "meta": {},
                        "created_at": "2026-05-25T00:00:00+00:00",
                    }
                ]
            )

    fake_sb = MagicMock()
    fake_sb.table = MagicMock(return_value=_Tbl())
    monkeypatch.setattr(r, "get_async_supabase_admin", AsyncMock(return_value=fake_sb))

    auth = SimpleNamespace(user_id=UUID("11111111-1111-1111-1111-111111111111"))
    resp = await r.post_issue_message(6, IssueMessagePost(body="hi"), auth)

    assert inserted["row"]["kind"] == "comment"
    assert resp.comment.body == "hi"


def _nullctx():
    import contextlib

    return contextlib.nullcontext()
