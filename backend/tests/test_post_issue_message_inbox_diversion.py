"""harness p4 §1-③: a comment on an issue whose ROOT run is mid-turn goes to
the inbox (claimed at the next step boundary) — the comment row is kept and
no second turn is dispatched. Idle issue → the pre-existing wake path."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.schemas.issue_message import IssueMessagePost

ME = "11111111-1111-1111-1111-111111111111"


def _router_module():
    importlib.import_module("app.api.issue_messages_router")
    return sys.modules["app.api.issue_messages_router"]


def _wire(monkeypatch, *, running):
    r = _router_module()
    issue_row = {
        "id": 5,
        "assignee_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "created_by_user_id": ME,
        "assignee_user_id": None,
    }
    monkeypatch.setattr(r, "_assert_issue_visible", AsyncMock(return_value=issue_row))
    monkeypatch.setattr(r, "get_or_create_issue_session", AsyncMock(return_value="55"))

    runs_repo = SimpleNamespace(running_root_run_id=AsyncMock(return_value=running))
    inbox_repo = SimpleNamespace(
        enqueue=AsyncMock(return_value={"id": 310819108761499, "kind": "steer"})
    )
    import app.repositories.agent_run_inbox_repository as inbox_repo_mod
    import app.repositories.agent_runs_repository as runs_repo_mod

    monkeypatch.setattr(runs_repo_mod, "get_agent_runs_repository", lambda: runs_repo)
    monkeypatch.setattr(
        inbox_repo_mod, "get_agent_run_inbox_repository", lambda: inbox_repo
    )

    store = MagicMock()
    store.append_user_message = AsyncMock()
    store_cls = MagicMock(return_value=store)
    store_cls.display_attachments = staticmethod(lambda a: a)
    monkeypatch.setattr(r, "ConversationsAiStore", store_cls)
    # Task 4 moved the busy branch's session append into
    # services/issues/inbox_or_dispatch; patch the source module so every
    # importer sees the same stand-in.
    import app.services.ai.chat.conversations_ai_store as _store_mod

    monkeypatch.setattr(_store_mod, "ConversationsAiStore", store_cls)

    wake = AsyncMock(return_value=False)
    monkeypatch.setattr(r, "_try_wake_waiting_workflow", wake)
    dispatch = MagicMock()
    monkeypatch.setattr(r, "_dispatch_respond_to_issue_reply", dispatch)
    # Task 4 moved the dispatcher into services/issues/issue_reply_dispatch
    # (two of its three callers are services). Patch it THERE — the router
    # keeps only a private alias.
    import app.services.issues.issue_reply_dispatch as _dispatch_mod

    monkeypatch.setattr(_dispatch_mod, "dispatch_respond_to_issue_reply", dispatch)
    return r, runs_repo, inbox_repo, store, wake, dispatch


@pytest.mark.asyncio
async def test_running_root_run_diverts_comment_to_inbox(monkeypatch):
    r, runs_repo, inbox_repo, store, wake, dispatch = _wire(monkeypatch, running=42)
    auth = SimpleNamespace(user_id=UUID(ME))
    resp = await r.post_issue_message(5, IssueMessagePost(body="tighten act two"), auth)

    runs_repo.running_root_run_id.assert_awaited_once_with(
        issue_id=5, conversation_id=55
    )
    # the human's words are kept on the thread …
    store.append_user_message.assert_awaited_once()
    assert store.append_user_message.await_args.kwargs["content"] == "tighten act two"
    # … and delivered to the inbox under the issue target, by the commenter
    kw = inbox_repo.enqueue.await_args.kwargs
    assert (kw["target_kind"], kw["target_id"], kw["kind"]) == ("issue", 5, "steer")
    assert kw["user_id"] == ME and kw["content"]["body"] == "tighten act two"
    # no second turn
    wake.assert_not_awaited()
    dispatch.assert_not_called()
    assert resp.diverted_to_inbox is True and resp.inbox_id == "310819108761499"
    assert resp.agent_dispatched is False


@pytest.mark.asyncio
async def test_idle_issue_takes_the_wake_path(monkeypatch):
    r, runs_repo, inbox_repo, store, wake, dispatch = _wire(monkeypatch, running=None)
    auth = SimpleNamespace(user_id=UUID(ME))
    resp = await r.post_issue_message(5, IssueMessagePost(body="go"), auth)
    inbox_repo.enqueue.assert_not_awaited()
    dispatch.assert_called_once()
    assert resp.diverted_to_inbox is False and resp.agent_dispatched is True
