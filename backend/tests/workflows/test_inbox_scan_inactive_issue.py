"""FH3 T1 (E2): the idle-drain backstop re-checks the issue before it buys a turn.

``deliver_or_dispatch`` only refuses terminal/hidden issues and busy ones, so a
``needs_followup`` / ``in_review`` issue with a pending item reads as IDLE and
gets a billed continuation turn. That is fine for what a person sent and wrong
for what the agent scheduled for itself: ``_fire_issue_wakeup`` already refuses
an agent wake-up on an issue waiting on a person (``issue_not_active``), but a
wake-up queued while the issue was parked bypasses that guard, and once the
needs_input gate times out (marker cleared, lock released) the next sweeper
tick dispatched it anyway (recon §4, defect B's second door).

The scan now expires the agent's items on such an issue — with a WARN naming
the reason — and only dispatches if something a person (or a sub-agent) sent
is still pending.
"""

from __future__ import annotations

import copy

import pytest

import app.repositories.agent_run_inbox_repository as inbox_mod
import app.services.issues.inbox_or_dispatch as deliver_mod
from app.services.issues.inbox_or_dispatch import DeliverResult
from app.workflows import agent_runs_sweeper as sw

pytestmark = pytest.mark.unit

ISSUE = 348020765598796
USER = "7b1c2f9e-0000-4000-8000-000000000001"


def _body(fn):
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _src(by: str) -> dict:
    return {"text": "wake", "source": {"kind": "schedule", "created_by": by}}


class _Inbox:
    """Just enough of the repository to model one issue's queue."""

    def __init__(
        self, issue: dict | None, items: list[dict], *, scan_sees_locked=False
    ):
        self.issue = issue
        # the real target list excludes locked issues (pending_issue_targets_stmt)
        self.scan_sees_locked = scan_sees_locked
        self.items = [
            {"id": i + 1, "kind": "steer", "user_id": USER, "expired": False, **it}
            for i, it in enumerate(items)
        ]
        self.expire_calls: list[dict] = []

    def _pending(self):
        return [it for it in self.items if not it["expired"]]

    async def pending_issue_targets(self, *, limit):
        locked = bool((self.issue or {}).get("execution_locked_at"))
        if locked and not self.scan_sees_locked:
            return []
        pend = self._pending()
        return (
            [{"target_id": ISSUE, "count": len(pend), "oldest_at": None}]
            if pend
            else []
        )

    async def oldest_pending_for_target(self, *, target_kind, target_id):
        pend = self._pending()
        return copy.deepcopy(pend[0]) if pend else None

    async def issue_activity(self, issue_id):
        return self.issue

    async def expire_agent_items_for_target(self, *, target_kind, target_id, reason):
        self.expire_calls.append({"target": (target_kind, target_id), "reason": reason})
        n = 0
        for it in self._pending():
            if ((it.get("content") or {}).get("source") or {}).get(
                "created_by"
            ) == "agent":
                it["expired"] = True
                n += 1
        return n


def _issue(status: str, *, parked: bool = False) -> dict:
    return {
        "status": status,
        "execution_locked_at": "2026-09-25T00:00:00+00:00" if parked else None,
        "execution_state": (
            {"awaiting_input": {"prompt": "q", "since": "x"}} if parked else {}
        ),
    }


@pytest.fixture
def tick(monkeypatch):
    """Run one real sweeper body against ``inbox``; return the dispatches."""
    from unittest.mock import AsyncMock

    for name, value in (
        ("mark_heartbeat_lost_step", 0),
        ("recompute_monthly_budgets_step", 0),
        ("expire_orphan_inbox_step", 0),
        ("reconcile_issue_execution_state_step", 0),
        ("force_settle_stale_pending_trees_step", 0),
        ("reap_preempted_input_waits_step", 0),
        ("reap_stale_workforce_tasks_step", {}),
    ):
        monkeypatch.setattr(sw, name, AsyncMock(return_value=value))

    async def _run(inbox: _Inbox) -> list[int]:
        dispatched: list[int] = []

        async def _deliver(issue_id, **kw):
            dispatched.append(issue_id)
            return DeliverResult("dispatched", workflow_id="wf-1")

        monkeypatch.setattr(inbox_mod, "get_agent_run_inbox_repository", lambda: inbox)
        monkeypatch.setattr(deliver_mod, "deliver_or_dispatch", _deliver)
        await _body(sw.agent_runs_sweeper_workflow)(None, None)
        return dispatched

    return _run


@pytest.mark.parametrize("status", ["needs_followup", "in_review"])
async def test_an_agent_wakeup_on_an_issue_waiting_on_a_person_buys_no_turn(
    tick, status
):
    inbox = _Inbox(_issue(status), [{"content": _src("agent")}])
    assert await tick(inbox) == []
    assert inbox.items[0]["expired"] is True
    assert inbox.expire_calls == [
        {"target": ("issue", ISSUE), "reason": "issue_not_active"}
    ]


@pytest.mark.parametrize("status", ["needs_followup", "in_review"])
@pytest.mark.parametrize(
    "item",
    [
        {"content": _src("user")},
        {"content": {"text": "a plain steer has no source"}},
        {"kind": "subagent_result", "content": {"summary": "s"}},
    ],
    ids=["user_wakeup", "plain_steer", "subagent_result"],
)
async def test_what_a_person_or_a_subagent_sent_still_dispatches(tick, status, item):
    inbox = _Inbox(_issue(status), [item])
    assert await tick(inbox) == [ISSUE]
    assert inbox.items[0]["expired"] is False


async def test_a_person_item_behind_an_agent_item_still_dispatches(tick):
    inbox = _Inbox(
        _issue("needs_followup"),
        [{"content": _src("agent")}, {"content": {"text": "hi"}}],
    )
    assert await tick(inbox) == [ISSUE]
    assert [it["expired"] for it in inbox.items] == [True, False]


async def test_a_live_issue_keeps_its_agent_wakeup(tick):
    """``in_progress`` is not inactive: the wake-up is the agent's own next
    step and still gets its turn, exactly as before."""
    inbox = _Inbox(_issue("in_progress"), [{"content": _src("agent")}])
    assert await tick(inbox) == [ISSUE]
    assert inbox.expire_calls == []


async def test_a_parked_issue_is_never_expired_by_the_scan(tick):
    """Parked = the gate is still waiting; the answer turn claims the item.
    (The scan's own target list excludes locked issues — this is the read
    that happens after it, in case the lock appeared in between.)"""
    inbox = _Inbox(
        _issue("needs_followup", parked=True),
        [{"content": _src("agent")}],
        scan_sees_locked=True,
    )
    assert await tick(inbox) == [ISSUE]  # deliver_or_dispatch decides, as before
    assert inbox.expire_calls == []
    assert inbox.items[0]["expired"] is False


async def test_the_gate_timing_out_does_not_buy_a_turn_on_the_agent_wakeup(tick):
    """Link test, recon RED 8. The wake-up fired while the issue was parked
    (so it went into the inbox, past the ``issue_not_active`` guard); the gate
    then timed out — marker cleared, lock released, status left at
    needs_followup. The next tick used to see an idle issue with a pending
    item and dispatch it."""
    inbox = _Inbox(
        _issue("needs_followup", parked=True),
        [{"content": {**_src("agent"), "dedupe_key": "sched:s1:t"}}],
    )
    assert await tick(inbox) == []  # parked: the scan excludes it upstream too
    inbox.issue = _issue("needs_followup", parked=False)  # the gate timed out
    assert await tick(inbox) == []
    assert inbox.items[0]["expired"] is True


async def test_the_inactive_expiry_is_counted_and_logged(tick):
    from loguru import logger

    lines: list[str] = []
    sink = logger.add(lines.append, level="INFO", format="{message}")
    try:
        await tick(_Inbox(_issue("needs_followup"), [{"content": _src("agent")}]))
    finally:
        logger.remove(sink)
    assert any("inbox_inactive=1" in line for line in lines), lines


async def test_an_unreadable_issue_is_skipped_not_dispatched(tick):
    """A read that failed has not proved the issue is active — and a wrong
    guess here costs a billed turn, so the target waits for the next tick."""
    inbox = _Inbox(_issue("needs_followup"), [{"content": _src("agent")}])

    async def _boom(issue_id):
        raise RuntimeError("db down")

    inbox.issue_activity = _boom
    assert await tick(inbox) == []
    assert inbox.items[0]["expired"] is False


async def test_a_failed_expiry_is_skipped_not_dispatched(tick):
    inbox = _Inbox(_issue("needs_followup"), [{"content": _src("agent")}])

    async def _boom(**kw):
        raise RuntimeError("db down")

    inbox.expire_agent_items_for_target = _boom
    assert await tick(inbox) == []


async def test_the_scan_step_returns_nothing_it_would_have_to_dispatch(monkeypatch):
    """The step's return value is checkpointed every minute: an inactive
    target contributes a small marker order, not the item."""
    inbox = _Inbox(_issue("needs_followup"), [{"content": _src("agent")}])
    monkeypatch.setattr(inbox_mod, "get_agent_run_inbox_repository", lambda: inbox)
    assert await sw.scan_idle_inbox_step() == [
        {"issue_id": ISSUE, "inactive_expired": 1}
    ]


def test_the_inactive_check_is_not_a_dbos_step():
    """It runs inside ``scan_idle_inbox_step``; a new recorded step there
    would shift the step ids of sweeper ticks recovered across a deploy."""
    import inspect

    fn = sw._expire_agent_items_if_inactive
    assert not hasattr(fn, "dbos_function_name")
    assert inspect.unwrap(fn) is fn
