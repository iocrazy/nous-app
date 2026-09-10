"""``deliver_or_dispatch`` — the one running→inbox / idle→dispatch decision.

Three trigger paths share it (comment, schedule wake, background sub-agent
result), so the three-state outcome is pinned here rather than in each
caller's tests.
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

ME = "11111111-1111-1111-1111-111111111111"


def _wire(monkeypatch, *, issue, running=None, order=None):
    """Patch every seam ``deliver_or_dispatch`` resolves at call time."""
    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod
    import app.repositories.issue_repository as issue_mod
    import app.services.ai.chat.conversations_ai_store as store_mod
    import app.services.issues.issue_session as session_mod

    monkeypatch.setattr(
        issue_mod,
        "issue_repository",
        SimpleNamespace(get_by_id=AsyncMock(return_value=issue)),
    )
    monkeypatch.setattr(
        session_mod, "get_or_create_issue_session", AsyncMock(return_value="55")
    )
    monkeypatch.setattr(
        runs_mod,
        "get_agent_runs_repository",
        lambda: SimpleNamespace(running_root_run_id=AsyncMock(return_value=running)),
    )

    async def _enqueue(**kwargs):
        if order is not None:
            order.append("enqueue")
        return {"id": 310819108761499}

    inbox_repo = SimpleNamespace(enqueue=AsyncMock(side_effect=_enqueue))
    monkeypatch.setattr(inbox_mod, "get_agent_run_inbox_repository", lambda: inbox_repo)

    async def _append(**kwargs):
        if order is not None:
            order.append("append")
        return {}

    store = MagicMock()
    store.append_user_message = AsyncMock(side_effect=_append)
    store_cls = MagicMock(return_value=store)
    store_cls.display_attachments = staticmethod(lambda a: a)
    monkeypatch.setattr(store_mod, "ConversationsAiStore", store_cls)

    importlib.import_module("app.api.issue_messages_router")
    router = sys.modules["app.api.issue_messages_router"]

    def _dispatch(*args, **kwargs):
        if order is not None:
            order.append("dispatch")

    dispatch = MagicMock(side_effect=_dispatch)
    monkeypatch.setattr(router, "_dispatch_respond_to_issue_reply", dispatch)
    # Task 4 moved the dispatcher into services/issues/issue_reply_dispatch
    # (two of its three callers are services). Patch it THERE — the router
    # keeps only a private alias.
    import app.services.issues.issue_reply_dispatch as _dispatch_mod

    monkeypatch.setattr(_dispatch_mod, "dispatch_respond_to_issue_reply", dispatch)

    thread_rows: list[dict] = []

    async def _thread(**kwargs):
        if order is not None:
            order.append("thread")
        thread_rows.append(kwargs)

    import app.services.issues.inbox_or_dispatch as mod

    monkeypatch.setattr(mod, "_insert_issue_message", _thread)
    return inbox_repo, store, dispatch, thread_rows


def _issue(**kw):
    base = {
        "id": 5,
        "status": "in_progress",
        "hidden_at": None,
        "paused_at": None,
        "execution_state": {},
        "created_by_user_id": ME,
    }
    return {**base, **kw}


async def test_running_issue_gets_the_inbox_not_a_second_turn(monkeypatch):
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    inbox_repo, _store, dispatch, _rows = _wire(
        monkeypatch, issue=_issue(), running=777
    )
    out = await deliver_or_dispatch(
        5, kind="subagent_result", content={"a": 1}, user_id=ME
    )
    assert out.mode == "inbox" and out.inbox_id == 310819108761499
    assert inbox_repo.enqueue.await_args.kwargs["kind"] == "subagent_result"
    dispatch.assert_not_called()


async def test_dispatch_window_counts_as_busy(monkeypatch):
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    marker = {
        "dispatching": {
            "workflow_id": "w",
            "at": datetime.now(timezone.utc).isoformat(),
        }
    }
    inbox_repo, _store, dispatch, _rows = _wire(
        monkeypatch, issue=_issue(execution_state=marker), running=None
    )
    out = await deliver_or_dispatch(5, kind="steer", content={"body": "x"}, user_id=ME)
    assert out.mode == "inbox"
    inbox_repo.enqueue.assert_awaited_once()
    dispatch.assert_not_called()


async def test_already_enqueued_does_not_double_insert(monkeypatch):
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    inbox_repo, _store, dispatch, _rows = _wire(
        monkeypatch, issue=_issue(), running=777
    )
    out = await deliver_or_dispatch(
        5,
        kind="subagent_result",
        content={"a": 1},
        user_id=ME,
        already_enqueued=True,
    )
    assert out.mode == "inbox" and out.reason == "already_enqueued"
    inbox_repo.enqueue.assert_not_awaited()
    dispatch.assert_not_called()


async def test_idle_issue_records_the_body_then_dispatches(monkeypatch):
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    order: list[str] = []
    _inbox, store, dispatch, rows = _wire(
        monkeypatch, issue=_issue(), running=None, order=order
    )
    out = await deliver_or_dispatch(
        5,
        kind="schedule_fired",
        content={"body": "wake"},
        user_id=ME,
        message_body="wake",
        source={"kind": "schedule", "schedule_id": "s1", "created_by": "agent"},
    )
    assert out.mode == "dispatched" and out.workflow_id
    # The thread row lands BEFORE the dispatch — a turn that starts first
    # would render a reply to a message nobody can see.
    assert order.index("thread") < order.index("dispatch")
    store.append_user_message.assert_awaited_once()
    assert rows[0]["meta"] == {
        "source": {"kind": "schedule", "schedule_id": "s1", "created_by": "agent"}
    }
    assert rows[0]["kind"] == "comment" and rows[0]["author_user_id"] == ME


async def test_no_source_writes_no_source_key(monkeypatch):
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    _inbox, _store, _dispatch, rows = _wire(monkeypatch, issue=_issue(), running=None)
    await deliver_or_dispatch(
        5, kind="steer", content={}, user_id=ME, message_body="hi"
    )
    assert rows[0]["meta"] == {}


async def test_terminal_issue_is_skipped(monkeypatch):
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    inbox_repo, _store, dispatch, _rows = _wire(
        monkeypatch, issue=_issue(status="done"), running=None
    )
    out = await deliver_or_dispatch(5, kind="steer", content={}, user_id=ME)
    assert out.mode == "skipped" and out.reason == "issue_terminal"
    inbox_repo.enqueue.assert_not_awaited()
    dispatch.assert_not_called()


async def test_dispatch_failure_is_typed_not_raised(monkeypatch):
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    _inbox, _store, dispatch, _rows = _wire(monkeypatch, issue=_issue(), running=None)
    dispatch.side_effect = RuntimeError("dbos is down")
    out = await deliver_or_dispatch(5, kind="steer", content={}, user_id=ME)
    assert out.mode == "skipped" and out.reason.startswith("dispatch_failed")


async def test_a_bodiless_delivery_dispatches_the_continuation_nudge(monkeypatch):
    """``respond_to_issue_reply`` appends its body as the turn's user message
    and has no branch for an empty one — the turn would open with a blank
    bubble and a blank input_summary. A background sub-agent's result carries
    no text (the run reads it off the inbox), so it starts on the same nudge
    the bounded continuation loop uses."""
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch
    from app.services.issues.issue_agent_executor import CONTINUATION_NUDGE

    _inbox, _store, dispatch, _rows = _wire(monkeypatch, issue=_issue(), running=None)
    out = await deliver_or_dispatch(
        5, kind="subagent_result", content={"summary": "s"}, user_id=ME
    )
    assert out.mode == "dispatched"
    body = dispatch.call_args.args[2]
    assert body == CONTINUATION_NUDGE and body.strip()


async def test_a_failed_read_is_not_reported_as_a_missing_issue(monkeypatch):
    """A probe that cannot reach its target has not proved the target is
    absent — the deploy-frontend.yml lesson, in one reason string."""
    import app.repositories.issue_repository as issue_mod
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    _wire(monkeypatch, issue=_issue(), running=None)
    monkeypatch.setattr(
        issue_mod,
        "issue_repository",
        SimpleNamespace(get_by_id=AsyncMock(side_effect=RuntimeError("pg is down"))),
    )
    out = await deliver_or_dispatch(5, kind="steer", content={}, user_id=ME)
    assert out.mode == "skipped" and out.reason == "issue_unreadable"


async def test_a_genuinely_absent_issue_still_reads_as_missing(monkeypatch):
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    _wire(monkeypatch, issue=None, running=None)
    out = await deliver_or_dispatch(5, kind="steer", content={}, user_id=ME)
    assert out.mode == "skipped" and out.reason == "issue_missing"


async def test_a_failed_read_with_a_caller_row_decides_on_that_row(monkeypatch):
    """The busy half keeps its degrade-to-stale behaviour: refusing to decide
    would lose the human's comment."""
    import app.repositories.issue_repository as issue_mod
    from app.services.issues.inbox_or_dispatch import divert_to_inbox_if_busy

    inbox_repo, _store, _dispatch, _rows = _wire(
        monkeypatch, issue=_issue(), running=777
    )
    monkeypatch.setattr(
        issue_mod,
        "issue_repository",
        SimpleNamespace(get_by_id=AsyncMock(side_effect=RuntimeError("pg is down"))),
    )
    out = await divert_to_inbox_if_busy(
        5, kind="steer", content={}, user_id=ME, issue=_issue()
    )
    assert out.mode == "inbox"
    inbox_repo.enqueue.assert_awaited_once()


# ── dedupe_key: one delivery per key across a body replay ───────────────────


async def test_the_inbox_branch_forwards_the_dedupe_key(monkeypatch):
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    inbox_repo, _store, _dispatch, _rows = _wire(
        monkeypatch, issue=_issue(), running=777
    )
    out = await deliver_or_dispatch(
        5,
        kind="steer",
        content={"text": "wake"},
        user_id=ME,
        dedupe_key="sched:s1:2026-09-11T09:00:00+00:00",
    )
    assert out.mode == "inbox"
    assert (
        inbox_repo.enqueue.await_args.kwargs["dedupe_key"]
        == "sched:s1:2026-09-11T09:00:00+00:00"
    )


async def test_the_dispatch_branch_pins_the_workflow_id_to_the_key(monkeypatch):
    """The body's writes are not step-recorded, so a replay would start a
    SECOND turn. A pinned workflow id makes DBOS collapse the two."""
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    _inbox, _store, dispatch, _rows = _wire(monkeypatch, issue=_issue(), running=None)
    key = "sched:s1:2026-09-11T09:00:00+00:00"
    out = await deliver_or_dispatch(
        5,
        kind="steer",
        content={"text": "wake"},
        user_id=ME,
        message_body="wake",
        dedupe_key=key,
    )
    assert out.mode == "dispatched" and out.workflow_id == key
    assert dispatch.call_args.args[4] == key


async def test_without_a_key_the_workflow_id_stays_random(monkeypatch):
    """Every other caller must keep a unique id — a fixed one would make a
    legitimate re-dispatch a silent no-op."""
    from app.services.issues.inbox_or_dispatch import deliver_or_dispatch

    _inbox, _store, dispatch, _rows = _wire(monkeypatch, issue=_issue(), running=None)
    out = await deliver_or_dispatch(
        5, kind="steer", content={}, user_id=ME, message_body="hi"
    )
    assert out.workflow_id.startswith("issue-reply-5-")
