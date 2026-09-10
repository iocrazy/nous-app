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
import uuid
from types import SimpleNamespace

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


async def test_an_issue_awaiting_input_is_not_drained():
    """I1. The park branch at the loop top has no ``wait_rounds`` ceiling, so a
    drain ``continue`` on a ``needs_input`` turn re-entered it and suspended
    the dispatch for the recv TTL (72 h by default) — holding the execution
    lock the whole time and draining nothing, because a parked workflow runs
    no turns and reaches no step boundary.

    The other half of the same gate: with the wait-gate deps absent the drain
    would instead run a turn that swallows the agent's question. Either way an
    issue that is waiting on a person is not drained here — the item waits for
    the answer, exactly as the sweeper leaves paused/awaiting issues alone.
    """
    rec = _Recorder(outcome="needs_input")
    out = await _run(rec, _pending([1]))
    assert rec.turns == [False], "a parked turn must not buy a drain turn"
    assert out["outcome"] == "needs_input"


async def test_a_finished_turn_is_still_drained():
    """Negative control for the gate above — without it the gate could be
    ``False`` for every outcome and the test above would still pass."""
    rec = _Recorder(outcome="completed")
    await _run(rec, _pending([1, 0]))
    assert rec.turns == [False, True]


async def test_every_exit_reports_how_many_drain_turns_it_ran():
    """M3. ``inbox_drains`` was only on the normal exit, so the preempt and
    pause exits described a different shape of dispatch than the one beside
    them."""
    rec = _Recorder(outcome="completed")
    normal = await _run(rec, _pending([0]))
    assert normal["inbox_drains"] == 0

    paused = await _run_dispatch(
        _Recorder(), load_issue=_load_issue_with(paused_at="2026-09-10T00:00:00Z")
    )
    assert "inbox_drains" in paused, paused

    preempted = await _run_dispatch(
        _Recorder(), load_issue=_load_issue_with(status="done")
    )
    assert preempted.get("preempted") is True and "inbox_drains" in preempted


def _load_issue_with(**over):
    async def _load(issue_id):
        return {"id": issue_id, "status": "in_progress", **over}

    return _load


async def _run_dispatch(rec, *, load_issue):
    from app.workflows.issue_lifecycle import _run_dispatch_with_continuation

    return await _run_dispatch_with_continuation(
        1,
        {"id": 1},
        "agent-1",
        "user-1",
        run_turn=rec.run_turn,
        set_status=rec.set_status,
        load_issue=load_issue,
        pending_inbox=_pending([0]),
    )


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


async def test_the_production_wiring_uses_the_real_probe(monkeypatch):
    """A layer that only ever runs with an injected fake is a layer that never
    runs. With no probe injected — the production call — the loop must reach
    the repository itself.

    Behavioural on purpose: the source-substring version of this test passed
    while the call sat in a branch that never executed, and went red on a
    rename that changed nothing.
    """
    import app.repositories.agent_run_inbox_repository as inbox_mod
    from app.workflows.issue_lifecycle import _run_dispatch_with_continuation

    seen: list = []

    async def _pending_count(*, target_kind, target_id):
        seen.append((target_kind, target_id))
        return 1 if len(seen) == 1 else 0

    monkeypatch.setattr(
        inbox_mod,
        "get_agent_run_inbox_repository",
        lambda: SimpleNamespace(pending_count=_pending_count),
    )

    rec = _Recorder()
    await _run_dispatch_with_continuation(
        7,
        {"id": 7},
        "agent-1",
        "user-1",
        run_turn=rec.run_turn,
        set_status=rec.set_status,
        load_issue=_load_issue,
        max_continuations=2,
    )
    assert seen and seen[0] == ("issue", 7)
    assert rec.turns == [False, True], "the real probe never bought a drain turn"


# ── layer (b) lives in test_inbox_drain_chain.py ────────────────────────
#
# The sweeper half moved there when the drain was split into a scan step and a
# body dispatch (fix round 1, C1): those cases have to drive the REAL workflow
# body to be worth anything, and they carry the route-C guards.


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


async def test_pending_issue_targets_skips_an_issue_whose_turn_lock_is_held():
    """Task 7b defect C. Layer (a) — ``issue_lifecycle``'s in-turn drain —
    decides to run one more turn at the end of a run, but the new ``agent_runs``
    row only appears seconds later. In that gap none of the three busy signals
    is up: no running root run, not paused, no ``dispatching`` marker. On
    2026-09-10 the sweeper's tick landed inside an 11 s gap and dispatched the
    same inbox item layer (a) had already taken, costing a real billed turn
    that reached the user as an unexplained "Continue working on this issue".

    ``execution_locked_at`` is the signal that WAS up: ``execute_issue`` holds
    it for the whole lifetime of the workflow. The scan excludes those issues,
    so the drain and the backstop can no longer both act on one item."""
    from sqlalchemy.dialects import postgresql

    from app.repositories.agent_run_inbox_repository import pending_issue_targets_stmt

    sql = str(
        pending_issue_targets_stmt(20).compile(dialect=postgresql.dialect())
    ).lower()
    assert "execution_locked_at is not null" in sql
    assert "not in" in sql, "the locked issues must be EXCLUDED, not selected"


async def test_the_listing_users_see_is_not_narrowed_by_the_turn_lock():
    """Negative control. ``pending_summary_stmt`` feeds the "2 queued" chips:
    an item queued on an issue that is mid-turn is exactly the thing that chip
    exists to show. Only the sweeper's own query gets the new exclusion."""
    from sqlalchemy.dialects import postgresql

    from app.repositories.agent_run_inbox_repository import pending_summary_stmt

    sql = str(
        pending_summary_stmt(str(uuid.uuid4())).compile(dialect=postgresql.dialect())
    ).lower()
    assert "execution_locked_at" not in sql


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
