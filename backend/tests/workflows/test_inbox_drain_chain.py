"""The idle-drain chain: the @DBOS.step only SCANS; the workflow BODY dispatches.

Task 7a fix round 1, C1. The first cut ran ``deliver_or_dispatch`` inside
``@DBOS.step()``. Its idle arm reaches ``DBOS.start_workflow``, and DBOS
asserts ``is_workflow()`` there with an EMPTY message; ``dispatch_issue_reply``
caught it and returned ``skipped/dispatch_failed:``, which the sweeper logged
at INFO as "left in place" and counted as zero. The backstop for defect 2 could
therefore never fire in production, and no surface said so — the exact silent
class route C exists to prevent (``scheduled_master.py:105`` records the same
rule, and Task 5's wake-up dispatch is split this way for this reason).

The unit tests could not catch it because they replaced ``deliver_or_dispatch``
wholesale — the one call that would have hit the assertion. So this file drives
the REAL workflow body and pins the split two ways: behaviourally (the stub
asserts it is called with no step on the stack) and structurally (an AST guard,
because a stub cannot reproduce the assertion itself).
"""

from __future__ import annotations

import ast
import inspect

import pytest

from app.services.issues.inbox_or_dispatch import DeliverResult
from app.workflows import agent_runs_sweeper as sw

pytestmark = pytest.mark.unit


def _body(fn):
    """The undecorated workflow body — ``@DBOS.scheduled`` + ``@DBOS.workflow``
    wrap it twice and refuse to run before DBOS is initialised."""
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _quiet(monkeypatch, **over):
    """Stub every OTHER step in the tick so only the drain is under test."""
    from unittest.mock import AsyncMock

    for name, value in (
        ("mark_heartbeat_lost_step", 0),
        ("recompute_monthly_budgets_step", 0),
        ("expire_orphan_inbox_step", 0),
        ("reconcile_issue_execution_state_step", 0),
    ):
        monkeypatch.setattr(sw, name, AsyncMock(return_value=value))
    for name, value in over.items():
        monkeypatch.setattr(sw, name, value)


ORDER = {
    "issue_id": 348020765598796,
    "user_id": "u-1",
    "kind": "steer",
    "pending_count": 2,
}


# ── the split itself ────────────────────────────────────────────────────


async def test_the_step_only_scans_and_returns_orders(monkeypatch):
    from types import SimpleNamespace

    import app.repositories.agent_run_inbox_repository as inbox_mod

    async def _targets(*, limit):
        assert limit == sw.INBOX_DRAIN_LIMIT
        return [{"target_id": 348020765598796, "count": 2, "oldest_at": None}]

    async def _oldest(*, target_kind, target_id):
        assert (target_kind, target_id) == ("issue", 348020765598796)
        return {"id": 5, "kind": "steer", "user_id": "u-1"}

    monkeypatch.setattr(
        inbox_mod,
        "get_agent_run_inbox_repository",
        lambda: SimpleNamespace(
            pending_issue_targets=_targets, oldest_pending_for_target=_oldest
        ),
    )
    assert await sw.scan_idle_inbox_step() == [ORDER]


#: Every ``@DBOS.step`` in the sweeper, by name. The behavioural guard below
#: asserts none of them is on the call stack when the dispatch happens —
#: without a DBOS runtime there is no context object to interrogate, but the
#: stack says the same thing and says it in the caller's own terms.
STEP_NAMES = {
    "mark_heartbeat_lost_step",
    "reconcile_issue_execution_state_step",
    "expire_orphan_inbox_step",
    "scan_idle_inbox_step",
    "recompute_monthly_budgets_step",
}


async def test_the_workflow_body_dispatches_each_order(monkeypatch):
    """Behavioural half of the route-C guard: the stub refuses to answer if a
    step is on the stack, so a dispatch that migrates back inside one fails
    here rather than silently in production."""
    seen: list = []

    async def _deliver(issue_id, **kw):
        on_stack = {f.function for f in inspect.stack()} & STEP_NAMES
        assert not on_stack, f"deliver_or_dispatch ran INSIDE {on_stack}"
        seen.append((issue_id, kw))
        return DeliverResult("dispatched", workflow_id="wf-1")

    async def _scan():
        return [ORDER]

    import app.services.issues.inbox_or_dispatch as deliver_mod

    _quiet(monkeypatch, scan_idle_inbox_step=_scan)
    monkeypatch.setattr(deliver_mod, "deliver_or_dispatch", _deliver)

    await _body(sw.agent_runs_sweeper_workflow)(None, None)

    assert [i for i, _ in seen] == [348020765598796]
    assert seen[0][1]["already_enqueued"] is True
    assert seen[0][1]["user_id"] == "u-1"
    assert seen[0][1]["kind"] == "steer"


async def test_a_busy_issue_is_left_alone_and_a_failure_is_not_silent(monkeypatch):
    """ "the issue is busy" and "the dispatch itself failed" are different
    facts. The first cut logged both at INFO as "left in place"."""
    from loguru import logger

    import app.services.issues.inbox_or_dispatch as deliver_mod

    async def _scan():
        return [
            {**ORDER, "issue_id": 1},
            {**ORDER, "issue_id": 2},
        ]

    async def _deliver(issue_id, **kw):
        if issue_id == 1:
            return DeliverResult("inbox", reason="already_enqueued")
        return DeliverResult("skipped", reason="dispatch_failed: boom")

    _quiet(monkeypatch, scan_idle_inbox_step=_scan)
    monkeypatch.setattr(deliver_mod, "deliver_or_dispatch", _deliver)

    lines: list[str] = []
    sink = logger.add(lines.append, level="ERROR", format="{message}")
    try:
        await _body(sw.agent_runs_sweeper_workflow)(None, None)
    finally:
        logger.remove(sink)

    text = "".join(lines)
    assert "2" in text and "dispatch_failed" in text
    assert "issue 1" not in text, "a busy issue is not an error"


async def test_one_bad_order_never_starves_the_rest(monkeypatch):
    import app.services.issues.inbox_or_dispatch as deliver_mod

    done: list[int] = []

    async def _scan():
        return [{**ORDER, "issue_id": 1}, {**ORDER, "issue_id": 2}]

    async def _deliver(issue_id, **kw):
        if issue_id == 1:
            raise RuntimeError("boom")
        done.append(issue_id)
        return DeliverResult("dispatched", workflow_id="wf-2")

    _quiet(monkeypatch, scan_idle_inbox_step=_scan)
    monkeypatch.setattr(deliver_mod, "deliver_or_dispatch", _deliver)
    await _body(sw.agent_runs_sweeper_workflow)(None, None)
    assert done == [2]


async def test_a_scan_that_cannot_read_yields_no_orders(monkeypatch):
    """A probe that failed has not proved there is nothing stranded."""
    from types import SimpleNamespace

    import app.repositories.agent_run_inbox_repository as inbox_mod

    async def _boom(**kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        inbox_mod,
        "get_agent_run_inbox_repository",
        lambda: SimpleNamespace(pending_issue_targets=_boom),
    )
    assert await sw.scan_idle_inbox_step() == []


# ── structural guard (a stub cannot reproduce DBOS's assertion) ─────────


def test_no_step_in_this_module_dispatches_a_workflow():
    tree = ast.parse(inspect.getsource(sw))

    def _is_dbos_step(dec: ast.expr) -> bool:
        node = dec.func if isinstance(dec, ast.Call) else dec
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "step"
            and isinstance(node.value, ast.Name)
            and node.value.id == "DBOS"
        )

    steps = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(_is_dbos_step(d) for d in n.decorator_list)
    ]
    # Pinned so a new step cannot be added without a thought about route C.
    assert {s.name for s in steps} == {
        "mark_heartbeat_lost_step",
        "reconcile_issue_execution_state_step",
        "expire_orphan_inbox_step",
        "scan_idle_inbox_step",
        "recompute_monthly_budgets_step",
    }
    for step in steps:
        called = {
            n.func.attr
            for n in ast.walk(step)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        } | {
            n.func.id
            for n in ast.walk(step)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        for forbidden in (
            "deliver_or_dispatch",
            "dispatch_issue_reply",
            "start_workflow",
        ):
            assert forbidden not in called, f"{step.name} dispatches from inside a step"
