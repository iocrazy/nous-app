"""The dispatch window: _dispatch_execute_issue returns before atomic_checkout
runs, so between them running_root_run_id AND execution_locked_at are both
empty. A fork in that window swings ai_session_id away and the workflow's own
checkout then silently returns {"skipped": True}. The marker closes it."""

import importlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app.services.issues import issue_dispatch

pytestmark = pytest.mark.unit


def _issue(at):
    return {"execution_state": {"dispatching": {"workflow_id": "wf-1", "at": at}}}


def _ns(**kw):
    from types import SimpleNamespace

    return SimpleNamespace(**kw)


def _async(value, *, record=None):
    async def _call(*a, **kw):
        if record is not None:
            record.append((a, kw))
        return value

    return _call


def test_a_fresh_marker_means_busy():
    now = datetime.now(timezone.utc)
    assert issue_dispatch.is_dispatching(_issue(now.isoformat()), now=now) is True


def test_stale_null_and_malformed_markers_are_all_not_busy():
    """A crashed dispatch must not wedge the issue forever, and an unparseable
    marker is not a guard we can trust."""
    now = datetime.now(timezone.utc)
    assert (
        issue_dispatch.is_dispatching(
            _issue((now - timedelta(seconds=61)).isoformat()), now=now
        )
        is False
    )
    for state in (
        {},
        {"dispatching": None},
        {"dispatching": {}},
        {"dispatching": {"at": "not-a-time"}},
    ):
        assert (
            issue_dispatch.is_dispatching({"execution_state": state}, now=now) is False
        )
    # Exactly at the TTL is already expired: the boundary belongs to "the
    # workflow never came up", not to "still dispatching".
    assert (
        issue_dispatch.is_dispatching(
            _issue((now - timedelta(seconds=60)).isoformat()), now=now
        )
        is False
    )


# ── The marker lives on the DBOS seam itself ──────────────────────────────
#
# Review round 1, item 2: five call sites reach ``execute_issue``, and four of
# them (autopilot, pipeline relay, routine schedule, stranded recovery) never
# go through ``start_execute_issue``. Those four are the UNATTENDED dispatches
# — the ones most likely to collide with a human's fork. So the marker is
# written by ``issues_router._dispatch_execute_issue``, the one seam all five
# already call, rather than at four new patch points.


def _router():
    """importlib + sys.modules, not ``import app.api.issues_router as m``: the
    package re-exports the APIRouter under that very name, so the plain import
    binds the router OBJECT."""
    importlib.import_module("app.api.issues_router")
    return sys.modules["app.api.issues_router"]


@pytest.fixture
def seam(monkeypatch):
    """The DBOS enqueue stubbed out, every execution_state write captured."""
    from app.services.infra import dbos_orchestrator

    enqueued: list = []

    class _Client:
        def enqueue(self, options, *args):
            enqueued.append((options, args))
            return "handle"

    monkeypatch.setattr(dbos_orchestrator, "_client", _Client())
    monkeypatch.setattr(dbos_orchestrator, "_resolve_pinned_app_version", lambda: None)

    writes: list = []

    async def _merge(issue_id, patch):
        writes.append((issue_id, patch, len(enqueued)))

    monkeypatch.setattr(issue_dispatch, "merge_execution_state", _merge)
    return _ns(enqueued=enqueued, writes=writes)


def test_a_naive_timestamp_on_either_side_is_read_as_utc():
    """``at`` comes back from jsonb as whatever string was written, and ``now``
    is a public kwarg later Tasks were told to use. Neither may raise: an
    aware-minus-naive subtraction is a TypeError, which would take down the
    dispatch seam over a formatting detail."""
    aware = datetime.now(timezone.utc)
    naive = aware.replace(tzinfo=None)

    assert issue_dispatch.is_dispatching(_issue(naive.isoformat()), now=aware) is True
    assert issue_dispatch.is_dispatching(_issue(aware.isoformat()), now=naive) is True
    assert issue_dispatch.is_dispatching(_issue(naive.isoformat()), now=naive) is True
    old = (naive - timedelta(seconds=61)).isoformat()
    assert issue_dispatch.is_dispatching(_issue(old), now=naive) is False


def test_a_null_execution_state_column_is_not_busy():
    """``issues.execution_state`` is nullable, so this is the shape most real
    rows have — and the one no dict-valued fixture exercises."""
    assert issue_dispatch.is_dispatching({"execution_state": None}) is False
    assert issue_dispatch.is_dispatching({}) is False
    assert issue_dispatch.is_dispatching({"execution_state": {"turn": 3}}) is False


@pytest.mark.asyncio
async def test_the_dbos_seam_marks_before_it_enqueues(seam):
    await _router()._dispatch_execute_issue(7, "issue-7-abc")

    assert len(seam.enqueued) == 1
    issue_id, patch, enqueued_before = seam.writes[0]
    # Written BEFORE the enqueue: a marker stamped after it would leave open
    # exactly the window it exists to close.
    assert enqueued_before == 0
    assert issue_id == 7
    assert list(patch["dispatching"]) == ["workflow_id", "at"]
    assert patch["dispatching"]["workflow_id"] == "issue-7-abc"


@pytest.mark.asyncio
async def test_an_autopilot_dispatch_leaves_the_marker_set(seam, monkeypatch):
    """The unattended seam. ``node_start._dispatch_node`` never touches
    ``start_execute_issue``; before the marker moved onto the DBOS seam this
    path dispatched with no guard at all."""
    from app.services.workflow import node_start

    await node_start._dispatch_node(42, auto=True)

    assert len(seam.enqueued) == 1
    issue_id, patch, _ = seam.writes[0]
    assert issue_id == 42 and patch["dispatching"]["workflow_id"].startswith(
        "issue-42-"
    )
    # And the issue now reads busy to all three readers.
    assert issue_dispatch.is_dispatching({"execution_state": patch}) is True


@pytest.mark.asyncio
async def test_a_failed_enqueue_clears_the_marker(seam, monkeypatch):
    from app.services.infra import dbos_orchestrator

    class _Boom:
        def enqueue(self, *a, **k):
            raise RuntimeError("dbos is down")

    monkeypatch.setattr(dbos_orchestrator, "_client", _Boom())
    with pytest.raises(RuntimeError):
        await _router()._dispatch_execute_issue(7, "issue-7-abc")

    # cleared, not left to rot for 60s
    assert [p for _, p, _ in seam.writes][-1] == {"dispatching": None}


@pytest.mark.asyncio
async def test_a_duplicate_enqueue_leaves_the_marker_for_the_workflow_to_clear(
    seam, monkeypatch
):
    """A duplicate is a soft success — DBOS already holds that workflow, and
    its own atomic_checkout will remove the key."""
    from app.services.infra import dbos_orchestrator

    class _Dup:
        def enqueue(self, *a, **k):
            raise RuntimeError("workflow already exists")

    monkeypatch.setattr(dbos_orchestrator, "_client", _Dup())
    with pytest.raises(RuntimeError):
        await _router()._dispatch_execute_issue(7, "issue-7-abc")

    assert len(seam.writes) == 1  # the mark only; no clear


@pytest.mark.asyncio
async def test_a_marker_write_failure_never_blocks_the_dispatch(
    seam, monkeypatch, caplog
):
    """Review round 1, item 4: the marker is advisory, not a lock. If writing
    it fails the correct outcome is "this dispatch runs unguarded" — i.e. the
    behaviour before this feature existed — not "this issue cannot run".
    atomic_checkout's CAS is still the backstop."""
    import logging

    async def _boom(issue_id, patch):
        raise RuntimeError("issues table is angry")

    monkeypatch.setattr(issue_dispatch, "merge_execution_state", _boom)
    with caplog.at_level(logging.WARNING):
        await _router()._dispatch_execute_issue(7, "issue-7-abc")

    assert len(seam.enqueued) == 1


@pytest.mark.asyncio
async def test_start_execute_issue_maps_a_real_failure_to_dispatch_failed(monkeypatch):
    router_mod = _router()

    async def _boom(issue_id, workflow_id, **kw):
        raise RuntimeError("dbos is down")

    monkeypatch.setattr(router_mod, "_dispatch_execute_issue", _boom)
    with pytest.raises(issue_dispatch.DispatchFailed):
        await issue_dispatch.start_execute_issue(7)


@pytest.mark.asyncio
async def test_start_execute_issue_treats_a_duplicate_as_a_soft_success(monkeypatch):
    router_mod = _router()
    persisted = []

    async def _dup(issue_id, workflow_id, **kw):
        raise RuntimeError("workflow already exists")

    async def _persist(issue_id, workflow_id):
        persisted.append((issue_id, workflow_id))

    monkeypatch.setattr(router_mod, "_dispatch_execute_issue", _dup)
    monkeypatch.setattr(router_mod, "_persist_workflow_id", _persist)
    wf = await issue_dispatch.start_execute_issue(7)
    assert persisted == [(7, wf)]


# ── The three readers that were blind to the window ────────────────────────


def _marker(seconds_ago: float = 0.0) -> dict:
    at = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return {"dispatching": {"workflow_id": "wf-1", "at": at.isoformat()}}


class _ForkDeps:
    """Only what fork_run touches before the busy checks — the refusal must
    land before any rebuild work, so nothing further is ever reached."""

    def __init__(self, execution_state: dict):
        self.issue = {
            "id": 9,
            "status": "in_progress",
            "ai_session_id": 100,
            "hidden_at": None,
            "execution_locked_at": None,
            "dbos_workflow_id": None,
            "execution_state": execution_state,
        }
        self.events_read = False

    async def get_run(self, run_id, user_id):
        return {"id": 42, "issue_id": 9, "conversation_id": 100}

    async def get_issue(self, issue_id):
        return self.issue

    async def running_root_run_id(self, issue_id, conversation_id):
        return None

    async def list_events(self, run_id, at_seq):
        self.events_read = True
        return []


async def test_fork_refuses_while_a_dispatch_is_in_flight():
    from app.services.issues import issue_fork

    deps = _ForkDeps(_marker())
    with pytest.raises(issue_fork.ForkRejected) as ei:
        await issue_fork.fork_run(42, at_seq=2, steer=None, user_id="u", deps=deps)
    assert (ei.value.code, ei.value.status) == ("issue_busy", 409)
    assert deps.events_read is False


async def test_fork_proceeds_once_the_marker_has_expired():
    """The guard is a window, not a lock: a marker older than the TTL must not
    keep an issue unforkable forever."""
    from app.services.issues import issue_fork

    deps = _ForkDeps(_marker(seconds_ago=61))
    with pytest.raises(issue_fork.ForkRejected) as ei:
        await issue_fork.fork_run(42, at_seq=2, steer=None, user_id="u", deps=deps)
    # Got past the busy checks and refused on the boundary instead.
    assert ei.value.code == "not_a_step_boundary"
    assert deps.events_read is True


async def test_resume_refuses_while_a_dispatch_is_in_flight(monkeypatch):
    import importlib

    from fastapi import HTTPException

    r = importlib.import_module("app.api.issues_router")
    from tests.api.test_issues_pause_resume import AUTH, _issue

    issue = _issue(paused_at="2026-09-08T12:00:00+00:00", execution_state=_marker())
    monkeypatch.setattr(r, "is_issue_visible", _async(True))
    monkeypatch.setattr(
        r, "issue_repository", _ns(get_by_id=_async(issue), set_paused_at=_async(issue))
    )
    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod

    monkeypatch.setattr(
        runs_mod,
        "get_agent_runs_repository",
        lambda: _ns(running_root_run_id=_async(None)),
    )
    monkeypatch.setattr(
        inbox_mod,
        "get_agent_run_inbox_repository",
        lambda: _ns(pending_count=_async(1)),
    )
    dispatched = []
    monkeypatch.setattr(r, "_start_execute_issue", _async("wf", record=dispatched))

    with pytest.raises(HTTPException) as ei:
        await r.resume_issue(7, AUTH)
    assert ei.value.status_code == 409
    assert ei.value.detail == {
        "code": "issue_busy",
        "message": "a dispatch is in flight",
    }
    assert dispatched == []  # never a second dispatch into the same window


async def test_a_comment_lands_on_the_inbox_while_a_dispatch_is_in_flight(monkeypatch):
    """The window's third reader. running_root_run_id is None (no run row yet)
    and the issue is not paused — before the marker this fell through to the
    wake path and started a SECOND turn."""
    import importlib
    from unittest.mock import AsyncMock, MagicMock

    importlib.import_module("app.api.issue_messages_router")
    import sys

    r = sys.modules["app.api.issue_messages_router"]

    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod

    monkeypatch.setattr(
        runs_mod,
        "get_agent_runs_repository",
        lambda: _ns(running_root_run_id=_async(None)),
    )
    enqueue = AsyncMock(return_value={"id": 310819108761499})
    monkeypatch.setattr(
        inbox_mod, "get_agent_run_inbox_repository", lambda: _ns(enqueue=enqueue)
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

    inbox_id = await r._divert_to_inbox_if_running(
        5,
        "55",
        "11111111-1111-1111-1111-111111111111",
        _ns(user_id="11111111-1111-1111-1111-111111111111"),
        "hold on",
        None,
        paused=False,
        issue_row={"id": 5, "execution_state": _marker()},
    )
    assert inbox_id == "310819108761499"
    enqueue.assert_awaited_once()


async def test_the_comment_guard_re_reads_the_issue_instead_of_trusting_its_caller(
    monkeypatch,
):
    """Review round 1, item 5. The caller loads ``issue_row`` many awaits
    earlier (session create, typed-answer validation) while
    ``running_root_run_id`` beside it is read fresh. An unattended dispatch
    landing in that gap has to be seen, or the comment wakes a SECOND turn
    racing the one being dispatched."""
    import importlib
    import sys
    from unittest.mock import AsyncMock, MagicMock

    importlib.import_module("app.api.issue_messages_router")
    r = sys.modules["app.api.issue_messages_router"]

    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod

    monkeypatch.setattr(
        runs_mod,
        "get_agent_runs_repository",
        lambda: _ns(running_root_run_id=_async(None)),
    )
    enqueue = AsyncMock(return_value={"id": 310819108761499})
    monkeypatch.setattr(
        inbox_mod, "get_agent_run_inbox_repository", lambda: _ns(enqueue=enqueue)
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

    # The row the caller is holding is stale: it was loaded before the
    # dispatch. The database already knows better.
    monkeypatch.setattr(
        r.issue_repository,
        "get_by_id",
        AsyncMock(return_value={"id": 5, "execution_state": _marker()}),
    )

    inbox_id = await r._divert_to_inbox_if_running(
        5,
        "55",
        "11111111-1111-1111-1111-111111111111",
        _ns(user_id="11111111-1111-1111-1111-111111111111"),
        "hold on",
        None,
        paused=False,
        issue_row={"id": 5, "execution_state": {}},
    )
    assert inbox_id == "310819108761499"


async def test_a_failed_re_read_falls_back_to_the_row_the_caller_had(monkeypatch):
    """The re-read is a sharpening, not a new dependency: if it raises, the
    decision still gets made from the stale row rather than losing the
    comment."""
    import importlib
    import sys
    from unittest.mock import AsyncMock, MagicMock

    importlib.import_module("app.api.issue_messages_router")
    r = sys.modules["app.api.issue_messages_router"]

    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod

    monkeypatch.setattr(
        runs_mod,
        "get_agent_runs_repository",
        lambda: _ns(running_root_run_id=_async(None)),
    )
    monkeypatch.setattr(
        inbox_mod,
        "get_agent_run_inbox_repository",
        lambda: _ns(enqueue=AsyncMock(return_value={"id": 7})),
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
    monkeypatch.setattr(
        r.issue_repository, "get_by_id", AsyncMock(side_effect=RuntimeError("db"))
    )

    inbox_id = await r._divert_to_inbox_if_running(
        5,
        "55",
        "11111111-1111-1111-1111-111111111111",
        _ns(user_id="11111111-1111-1111-1111-111111111111"),
        "hold on",
        None,
        paused=False,
        issue_row={"id": 5, "execution_state": _marker()},
    )
    assert inbox_id == "7"


async def test_an_idle_issue_still_falls_through_to_the_wake_path(monkeypatch):
    """Negative control: without a marker the diversion must not fire, or every
    comment on an idle issue would silently queue instead of waking a turn."""
    import importlib
    import sys

    importlib.import_module("app.api.issue_messages_router")
    r = sys.modules["app.api.issue_messages_router"]

    import app.repositories.agent_runs_repository as runs_mod

    monkeypatch.setattr(
        runs_mod,
        "get_agent_runs_repository",
        lambda: _ns(running_root_run_id=_async(None)),
    )
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        r.issue_repository,
        "get_by_id",
        AsyncMock(return_value={"id": 5, "execution_state": {}}),
    )
    assert (
        await r._divert_to_inbox_if_running(
            5,
            "55",
            "11111111-1111-1111-1111-111111111111",
            _ns(user_id="11111111-1111-1111-1111-111111111111"),
            "hold on",
            None,
            paused=False,
            issue_row={"id": 5, "execution_state": {}},
        )
        is None
    )
