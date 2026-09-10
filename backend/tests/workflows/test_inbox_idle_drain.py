"""An inbox item that lands after the last step boundary must still be read.

Task 7a defect 2 (2026-09-10 real-stack acceptance). Claiming happens only at
a step boundary (``InboxClaimHook``), and between the last boundary of a run
and the row going ``completed`` there is a window with no boundary left. Two
independent items landed in that window during acceptance — a
``subagent_result`` and a scheduled ``steer`` — and BOTH sat at
``claimed_at IS NULL`` until the observation stopped (15 min / 8.8 min), with
the schedule row cheerfully reporting ``fire_count=1``. Nothing re-checked the
inbox when the issue went idle; the only backstop marked such items
``expired`` after a day, silently.

Two layers are pinned here, because neither alone is enough:

* in-workflow — before the dispatch loop declares itself over, a pending item
  buys one more turn (bounded, so a steady stream cannot loop forever);
* sweeper — an idle issue that still holds a pending item is dispatched on the
  existing idle path, and an item old enough to expire now says so out loud.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit


# ── layer (a): the dispatch loop drains before it declares itself over ──


class _Recorder:
    def __init__(self, outcome="completed"):
        self.turns: list[bool] = []
        self._outcome = outcome
        self.status_calls: list[str] = []

    async def run_turn(self, issue_row, agent_id, user_id, *, is_continuation):
        self.turns.append(is_continuation)
        return {"content": "x", "outcome": self._outcome, "reason": None}

    async def set_status(self, issue_id, status, **kw):
        self.status_calls.append(status)


async def _load_issue(issue_id):
    return {"id": issue_id, "status": "in_progress"}


def _pending(counts: list[int]):
    """A pending-inbox probe walking a script, repeating its last entry."""
    seq = list(counts)
    calls = {"n": 0}

    async def _probe(issue_id):
        idx = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[idx]

    _probe.calls = calls
    return _probe


async def _run(rec: _Recorder, pending_inbox, max_continuations=2):
    from app.workflows.issue_lifecycle import _run_dispatch_with_continuation

    return await _run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent-1",
        "user-1",
        run_turn=rec.run_turn,
        set_status=rec.set_status,
        load_issue=_load_issue,
        max_continuations=max_continuations,
        pending_inbox=pending_inbox,
    )


async def test_an_item_that_landed_after_the_last_boundary_buys_one_more_turn():
    """The run said ``completed``; an item arrived while it was closing. The
    dispatch must not end until something has had a boundary to claim it."""
    rec = _Recorder()
    await _run(rec, _pending([1, 0]))
    assert rec.turns == [False, True], "the stranded item never got a turn"


async def test_no_pending_item_means_no_extra_turn():
    rec = _Recorder()
    await _run(rec, _pending([0]))
    assert rec.turns == [False]


async def test_the_drain_turns_are_bounded():
    """A steady stream of items must not keep one dispatch running forever —
    past the bound the sweeper's idle-drain takes over."""
    from app.workflows.issue_lifecycle import MAX_INBOX_DRAIN_TURNS

    rec = _Recorder()
    await _run(rec, _pending([1]))  # always pending
    assert len(rec.turns) == 1 + MAX_INBOX_DRAIN_TURNS
    assert MAX_INBOX_DRAIN_TURNS >= 1


async def test_an_unreadable_inbox_does_not_break_the_dispatch(monkeypatch):
    """The probe is a sharpening, not a dependency: a read that fails leaves
    the dispatch exactly as it was — and says so in the log."""
    import app.repositories.agent_run_inbox_repository as inbox_mod
    from app.workflows.issue_lifecycle import _pending_inbox_count

    async def _boom(**kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        inbox_mod,
        "get_agent_run_inbox_repository",
        lambda: SimpleNamespace(pending_count=_boom),
    )
    assert await _pending_inbox_count(1) == 0

    rec = _Recorder()
    await _run(rec, _pending_inbox_count)
    assert rec.turns == [False]


async def test_the_production_wiring_passes_a_real_probe():
    """A layer that only ever runs with an injected fake is a layer that never
    runs. ``None`` must resolve to the repository-backed probe."""
    import inspect

    from app.workflows import issue_lifecycle as lifecycle

    src = inspect.getsource(lifecycle._run_dispatch_with_continuation)
    assert "_pending_inbox_count" in src


# ── layer (b): the sweeper picks up what the run left behind ────────────


def _fake_inbox_repo(targets, *, item_user="11111111-1111-1111-1111-111111111111"):
    async def pending_issue_targets(*, limit):
        assert limit == 20
        return targets

    async def list_for_target(*, target_kind, target_id, pending_only, limit=100):
        return [{"id": 5, "kind": "steer", "user_id": item_user}]

    return SimpleNamespace(
        pending_issue_targets=pending_issue_targets,
        list_for_target=list_for_target,
    )


async def test_sweeper_dispatches_an_idle_issue_that_still_holds_an_item(
    monkeypatch,
):
    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.services.issues.inbox_or_dispatch as deliver_mod
    from app.services.issues.inbox_or_dispatch import DeliverResult
    from app.workflows import agent_runs_sweeper as sw

    monkeypatch.setattr(
        inbox_mod,
        "get_agent_run_inbox_repository",
        lambda: _fake_inbox_repo(
            [{"target_id": 348020765598796, "count": 1, "oldest_at": None}]
        ),
    )
    deliver = AsyncMock(return_value=DeliverResult("dispatched", workflow_id="wf-1"))
    monkeypatch.setattr(deliver_mod, "deliver_or_dispatch", deliver)

    assert await sw.drain_idle_inbox_step() == 1
    assert deliver.await_args.args[0] == 348020765598796
    # the item is already on the inbox — the drain must not write a second row
    assert deliver.await_args.kwargs["already_enqueued"] is True


async def test_sweeper_leaves_a_busy_issue_alone(monkeypatch):
    """``deliver_or_dispatch`` owns the three busy signals (running root run,
    paused, dispatch in flight) — the drain must not re-implement them, and a
    busy answer counts as nothing dispatched."""
    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.services.issues.inbox_or_dispatch as deliver_mod
    from app.services.issues.inbox_or_dispatch import DeliverResult
    from app.workflows import agent_runs_sweeper as sw

    monkeypatch.setattr(
        inbox_mod,
        "get_agent_run_inbox_repository",
        lambda: _fake_inbox_repo([{"target_id": 7, "count": 2, "oldest_at": None}]),
    )
    monkeypatch.setattr(
        deliver_mod,
        "deliver_or_dispatch",
        AsyncMock(return_value=DeliverResult("inbox", reason="already_enqueued")),
    )

    assert await sw.drain_idle_inbox_step() == 0


async def test_one_failing_issue_does_not_stop_the_rest(monkeypatch):
    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.services.issues.inbox_or_dispatch as deliver_mod
    from app.services.issues.inbox_or_dispatch import DeliverResult
    from app.workflows import agent_runs_sweeper as sw

    monkeypatch.setattr(
        inbox_mod,
        "get_agent_run_inbox_repository",
        lambda: _fake_inbox_repo(
            [
                {"target_id": 1, "count": 1, "oldest_at": None},
                {"target_id": 2, "count": 1, "oldest_at": None},
            ]
        ),
    )

    async def _deliver(issue_id, **kw):
        if issue_id == 1:
            raise RuntimeError("boom")
        return DeliverResult("dispatched", workflow_id="wf-2")

    monkeypatch.setattr(deliver_mod, "deliver_or_dispatch", _deliver)
    assert await sw.drain_idle_inbox_step() == 1


def test_the_tick_runs_the_drain_step():
    import inspect

    from app.workflows import agent_runs_sweeper as sw

    assert "drain_idle_inbox_step()" in inspect.getsource(
        sw.agent_runs_sweeper_workflow
    )


async def test_pending_issue_targets_selects_only_unclaimed_unexpired_issues():
    from sqlalchemy.dialects import postgresql

    from app.repositories.agent_run_inbox_repository import pending_issue_targets_stmt

    sql = str(
        pending_issue_targets_stmt(20).compile(dialect=postgresql.dialect())
    ).lower()
    assert "claimed_at is null" in sql and "expired_at is null" in sql
    assert "group by" in sql and "target_kind = " in sql
    # oldest first: an item stranded longest is the one a user is waiting on
    assert "order by" in sql and "limit" in sql


# ── expiry stops being silent ──────────────────────────────────────────


async def test_expiry_warns_with_the_issue_and_the_count(monkeypatch):
    """A discarded wake-up used to leave no trace at all. Expiry is still the
    right end state for a day-old item — being quiet about it is not."""
    from loguru import logger

    from app.repositories import agent_run_inbox_repository as repo_mod

    rows = [("issue", 348020765598796), ("issue", 348020765598796), ("issue", 7)]

    class _Session:
        async def execute(self, stmt):
            return SimpleNamespace(all=lambda: rows)

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(repo_mod, "write_scope", lambda: _Scope())

    lines: list[str] = []
    sink = logger.add(lines.append, level="WARNING", format="{message}")
    try:
        n = await repo_mod.AgentRunInboxRepository().expire_stale(
            older_than=dt.datetime.now(dt.timezone.utc), skip_paused_issues=True
        )
    finally:
        logger.remove(sink)

    assert n == 3
    text = "".join(lines)
    assert "348020765598796" in text and " 7" in text
    assert "2 " in text, f"the per-issue count is not in the warning: {text!r}"
