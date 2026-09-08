"""Comment trigger disclosure + suppression, at the router boundary.

Two guarantees these pin:
  1. The preview endpoint and POST reach the SAME verdict for a given issue
     row — the chip cannot promise something the send path won't do.
  2. Suppressing leaves the note in the session (the agent reads it on its next
     turn) while starting nothing — and never touches issue_messages, whose
     rows are invisible to GET on agent-assigned issues (phantom-row trap).
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from app.schemas.issue_message import (
    CommentTriggerPreviewRequest,
    IssueMessagePost,
)

_AGENT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_OWNER = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _no_live_root_run(monkeypatch):
    """Phase 2a Task 5: running_root_run_id now RAISES on a failed read (an
    empty answer is not a negative result) instead of returning None. These
    tests have no DB; pin the "nothing running" answer explicitly."""
    from app.repositories.agent_runs_repository import AgentRunsRepository

    monkeypatch.setattr(
        AgentRunsRepository, "running_root_run_id", AsyncMock(return_value=None)
    )


def _router_module():
    importlib.import_module("app.api.issue_messages_router")
    return sys.modules["app.api.issue_messages_router"]


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _issue_row(**over) -> dict:
    return {
        "id": 5,
        "assignee_agent_id": _AGENT,
        "created_by_user_id": _OWNER,
        "assignee_user_id": None,
        "ai_session_id": "900001",
        **over,
    }


def _auth():
    return SimpleNamespace(user_id=UUID(_OWNER))


def _wire(monkeypatch, r, row):
    monkeypatch.setattr(r, "_assert_issue_visible", AsyncMock(return_value=row))
    monkeypatch.setattr(
        r, "get_or_create_issue_session", AsyncMock(return_value="900001")
    )


# ── preview endpoint ──────────────────────────────────────────────────────


def _preview_req(body=None) -> CommentTriggerPreviewRequest:
    return CommentTriggerPreviewRequest(body=body)


@pytest.mark.asyncio
async def test_preview_reports_wake_when_agent_assigned(monkeypatch):
    r = _router_module()
    _wire(monkeypatch, r, _issue_row())
    p = await r.comment_trigger_preview(5, _preview_req(), _auth())
    assert p.will_wake is True
    assert p.agent_id == _AGENT
    assert p.is_note is False


@pytest.mark.asyncio
async def test_preview_reports_no_wake_without_agent(monkeypatch):
    r = _router_module()
    _wire(monkeypatch, r, _issue_row(assignee_agent_id=None))
    p = await r.comment_trigger_preview(5, _preview_req(), _auth())
    assert p.will_wake is False
    assert p.agent_id is None


@pytest.mark.asyncio
async def test_preview_wakes_on_done_issue_unlike_dispatch_preview(monkeypatch):
    """The comment path has no terminal-status guard. dispatch-preview would say
    blocked here; this endpoint must not — that divergence is the whole reason
    it exists rather than reusing dispatch-preview."""
    r = _router_module()
    _wire(monkeypatch, r, _issue_row(status="done"))
    p = await r.comment_trigger_preview(5, _preview_req(), _auth())
    assert p.will_wake is True


@pytest.mark.asyncio
async def test_preview_reports_quiet_note_for_note_body(monkeypatch):
    """A /note draft flips will_wake off but still names the agent it won't wake,
    so the chip can render the quiet-note state."""
    r = _router_module()
    _wire(monkeypatch, r, _issue_row())
    p = await r.comment_trigger_preview(5, _preview_req("/note remember this"), _auth())
    assert p.will_wake is False
    assert p.is_note is True
    assert p.agent_id == _AGENT


@pytest.mark.asyncio
async def test_preview_notex_body_still_wakes(monkeypatch):
    """/notex is NOT the note command — it must read as a normal comment."""
    r = _router_module()
    _wire(monkeypatch, r, _issue_row())
    p = await r.comment_trigger_preview(
        5, _preview_req("/notex still a comment"), _auth()
    )
    assert p.will_wake is True
    assert p.is_note is False


@pytest.mark.asyncio
async def test_preview_and_post_agree(monkeypatch):
    """The anti-drift guarantee: same row → preview says wake, POST dispatches."""
    r = _router_module()
    row = _issue_row()
    _wire(monkeypatch, r, row)

    p = await r.comment_trigger_preview(5, _preview_req("go"), _auth())

    started = {}
    with (
        patch.object(
            r,
            "DBOS",
            SimpleNamespace(
                start_workflow=lambda fn, *a: started.update(fn=fn.__name__)
            ),
        ),
        patch.object(r, "SetWorkflowID", lambda *_a, **_k: _NullCtx()),
    ):
        await r.post_issue_message(5, IssueMessagePost(body="go"), _auth())

    assert p.will_wake is True
    assert started["fn"] == "respond_to_issue_reply"


# ── suppression ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_suppressed_comment_starts_nothing_and_leaves_a_note(monkeypatch):
    r = _router_module()
    _wire(monkeypatch, r, _issue_row())

    append = AsyncMock(return_value={"id": 1})
    started = {}

    with (
        patch.object(
            r,
            "DBOS",
            SimpleNamespace(
                start_workflow=lambda fn, *a: started.update(fn=fn.__name__)
            ),
        ),
        patch.object(r, "SetWorkflowID", lambda *_a, **_k: _NullCtx()),
        patch.object(r.ConversationsAiStore, "append_user_message", append),
    ):
        resp = await r.post_issue_message(
            5,
            IssueMessagePost(body="wait for the client", suppress_agent_ids=[_AGENT]),
            _auth(),
        )

    assert started == {}, "suppressed comment must not dispatch a turn"
    append.assert_awaited_once()
    kw = append.await_args.kwargs
    assert kw["session_id"] == 900001
    assert kw["content"] == "wait for the client"
    assert kw["user_id"] == _OWNER
    assert resp.agent_run is None
    assert resp.comment.body == "wait for the client"
    # Silent-no-op fix (final review, finding 5): the note path never starts
    # a workflow, so agent_dispatched must be False (agent_run stays null on
    # every path and can't be used to tell them apart).
    assert resp.agent_dispatched is False


@pytest.mark.asyncio
async def test_note_prefix_comment_starts_nothing_and_stores_body_verbatim(monkeypatch):
    """A /note body takes the note path with NO suppress list — the prefix alone
    zeroes the wake. The body is appended verbatim (prefix and all)."""
    r = _router_module()
    _wire(monkeypatch, r, _issue_row())

    append = AsyncMock(return_value={"id": 1})
    started = {}

    with (
        patch.object(
            r,
            "DBOS",
            SimpleNamespace(
                start_workflow=lambda fn, *a: started.update(fn=fn.__name__)
            ),
        ),
        patch.object(r, "SetWorkflowID", lambda *_a, **_k: _NullCtx()),
        patch.object(r.ConversationsAiStore, "append_user_message", append),
    ):
        resp = await r.post_issue_message(
            5,
            IssueMessagePost(body="/note check the license before shipping"),
            _auth(),
        )

    assert started == {}, "a /note comment must not dispatch a turn"
    append.assert_awaited_once()
    kw = append.await_args.kwargs
    assert kw["content"] == "/note check the license before shipping"
    assert resp.agent_run is None
    assert resp.comment.body == "/note check the license before shipping"
    assert resp.agent_dispatched is False


@pytest.mark.asyncio
async def test_notex_prefix_comment_still_dispatches(monkeypatch):
    """/notex is a normal comment — it must wake the agent, not take the note
    path (guards the "followed by whitespace or end" rule at the router)."""
    r = _router_module()
    _wire(monkeypatch, r, _issue_row())

    started = {}
    with (
        patch.object(
            r,
            "DBOS",
            SimpleNamespace(
                start_workflow=lambda fn, *a: started.update(fn=fn.__name__)
            ),
        ),
        patch.object(r, "SetWorkflowID", lambda *_a, **_k: _NullCtx()),
    ):
        await r.post_issue_message(5, IssueMessagePost(body="/notex ship it"), _auth())

    assert started["fn"] == "respond_to_issue_reply"


@pytest.mark.asyncio
async def test_non_matching_suppress_id_still_dispatches(monkeypatch):
    """Subtractive semantics: the client can only skip an agent the server
    itself computed. A stale id (assignee changed since preview) is a no-op."""
    r = _router_module()
    _wire(monkeypatch, r, _issue_row())

    started = {}
    with (
        patch.object(
            r,
            "DBOS",
            SimpleNamespace(
                start_workflow=lambda fn, *a: started.update(fn=fn.__name__)
            ),
        ),
        patch.object(r, "SetWorkflowID", lambda *_a, **_k: _NullCtx()),
    ):
        await r.post_issue_message(
            5,
            IssueMessagePost(body="go", suppress_agent_ids=["some-other-agent"]),
            _auth(),
        )

    assert started["fn"] == "respond_to_issue_reply"


@pytest.mark.asyncio
async def test_unsuppressed_comment_still_dispatches(monkeypatch):
    """Today's behaviour must not regress: no suppress list → wake."""
    r = _router_module()
    _wire(monkeypatch, r, _issue_row())

    started = {}
    with (
        patch.object(
            r,
            "DBOS",
            SimpleNamespace(
                start_workflow=lambda fn, *a: started.update(fn=fn.__name__)
            ),
        ),
        patch.object(r, "SetWorkflowID", lambda *_a, **_k: _NullCtx()),
    ):
        await r.post_issue_message(5, IssueMessagePost(body="go"), _auth())

    assert started["fn"] == "respond_to_issue_reply"


@pytest.mark.asyncio
async def test_suppress_on_unassigned_issue_leaves_legacy_path_alone(monkeypatch):
    """No agent → nothing to suppress. Must still take the issue_messages
    insert, not the note path (which has no session to write to)."""
    r = _router_module()
    _wire(monkeypatch, r, _issue_row(assignee_agent_id=None, ai_session_id=None))

    append = AsyncMock()
    legacy = AsyncMock(
        return_value=SimpleNamespace(
            comment=SimpleNamespace(body="note"), agent_run=None
        )
    )
    with (
        patch.object(r.ConversationsAiStore, "append_user_message", append),
        patch.object(r, "_insert_legacy_comment", legacy),
    ):
        await r.post_issue_message(
            5, IssueMessagePost(body="note", suppress_agent_ids=[_AGENT]), _auth()
        )

    append.assert_not_awaited()
    legacy.assert_awaited_once()


@pytest.mark.asyncio
async def test_suppressed_note_filters_attachments_like_a_real_turn(monkeypatch):
    """run_session_turn stores only display metadata under body['attachments'].
    A note must store the SAME shape, or history reload renders the two kinds of
    user message differently."""
    r = _router_module()
    _wire(monkeypatch, r, _issue_row())

    append = AsyncMock(return_value={"id": 1})
    with (
        patch.object(r.ConversationsAiStore, "append_user_message", append),
        patch.object(r, "SetWorkflowID", lambda *_a, **_k: _NullCtx()),
    ):
        await r.post_issue_message(
            5,
            IssueMessagePost(
                body="see this",
                suppress_agent_ids=[_AGENT],
                attachments=[
                    {
                        "kind": "image",
                        "resource_id": "123",
                        "mime": "image/png",
                        "data_url": "data:image/png;base64,AAAA",
                    }
                ],
            ),
            _auth(),
        )

    sent = append.await_args.kwargs["attachments"]
    assert sent is not None and len(sent) == 1
    assert "data_url" not in sent[0], "raw bytes must never reach the store"
    assert sent[0]["kind"] == "image"
    assert sent[0]["resource_id"] == "123"
