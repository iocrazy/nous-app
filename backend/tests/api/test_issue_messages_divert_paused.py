"""Phase 2a Task 5: while an issue is paused, comments queue on its inbox
(claimed by the resumed run) instead of waking a turn; typed answers are
refused until resume."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.schemas.issue_message import IssueMessagePost

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"
AUTH = SimpleNamespace(user_id=UUID(ME))


def _router_module():
    importlib.import_module("app.api.issue_messages_router")
    return sys.modules["app.api.issue_messages_router"]


def _wire(monkeypatch, *, running, paused_at):
    r = _router_module()
    issue_row = {
        "id": 5,
        "assignee_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "created_by_user_id": ME,
        "assignee_user_id": None,
        "paused_at": paused_at,
        "execution_state": {},
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
    return r, inbox_repo, store, wake, dispatch


async def test_comment_on_a_paused_idle_issue_is_diverted_to_the_inbox(monkeypatch):
    r, inbox_repo, store, wake, dispatch = _wire(
        monkeypatch, running=None, paused_at="2026-09-08T00:00:00+00:00"
    )
    resp = await r.post_issue_message(5, IssueMessagePost(body="later, do X"), AUTH)
    assert resp.diverted_to_inbox is True and resp.inbox_id == "310819108761499"
    store.append_user_message.assert_awaited_once()
    kw = inbox_repo.enqueue.await_args.kwargs
    assert (kw["target_kind"], kw["target_id"], kw["kind"]) == ("issue", 5, "steer")
    wake.assert_not_awaited()
    dispatch.assert_not_called()


async def test_comment_on_an_unpaused_idle_issue_still_wakes(monkeypatch):
    r, inbox_repo, store, wake, dispatch = _wire(
        monkeypatch, running=None, paused_at=None
    )
    resp = await r.post_issue_message(5, IssueMessagePost(body="go"), AUTH)
    assert resp.diverted_to_inbox is False
    inbox_repo.enqueue.assert_not_awaited()
    dispatch.assert_called_once()


async def test_typed_answer_while_paused_wakes_the_parked_workflow(monkeypatch):
    """Spec §2: an answer IS a wake. It is neither refused nor queued while
    paused — the parked workflow's recv TTL keeps counting during a pause, so
    a refusal could strand the question."""
    r, inbox_repo, store, wake, dispatch = _wire(
        monkeypatch, running=None, paused_at="2026-09-08T00:00:00+00:00"
    )
    pending = SimpleNamespace(
        question_id="q:1:2",
        value="A",
        kind="user",
        run_id="1",
        workflow_id="wf",
        wake=True,
    )
    monkeypatch.setattr(r, "_validate_typed_answer", AsyncMock(return_value=pending))
    commit = AsyncMock()
    monkeypatch.setattr(r, "_commit_typed_answer", commit)
    wake.return_value = True
    resp = await r.post_issue_message(
        5, IssueMessagePost(body="A", answer_to="q:1:2"), AUTH
    )
    assert resp.diverted_to_inbox is False and resp.agent_dispatched is True
    inbox_repo.enqueue.assert_not_awaited()
    wake.assert_awaited_once()
    commit.assert_awaited_once_with(pending)


async def test_run_state_read_failure_is_not_treated_as_idle(monkeypatch):
    """A failed running_root_run_id read must not fall through to the wake
    path (that would start a second turn behind a live one)."""
    r, inbox_repo, store, wake, dispatch = _wire(
        monkeypatch, running=None, paused_at=None
    )
    import app.repositories.agent_runs_repository as runs_repo_mod

    runs_repo_mod.get_agent_runs_repository().running_root_run_id.side_effect = (
        RuntimeError("db down")
    )
    with pytest.raises(RuntimeError):
        await r.post_issue_message(5, IssueMessagePost(body="go"), AUTH)
    wake.assert_not_awaited()
    dispatch.assert_not_called()
