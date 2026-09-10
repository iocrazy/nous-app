"""The workforce chain, end to end with a stubbed DBOS: the @DBOS.step only
SHAPES the queue and returns orders; the workflow BODY dispatches. Enqueuing
from inside a step is route-C forbidden (the empty-string AssertionError of
PR #495) — a source guard pins that, because a stub cannot reproduce it."""

from __future__ import annotations

import inspect
import re

import pytest

from app.workflows import workforce_dispatch as wd

pytestmark = pytest.mark.unit


def _body(fn):
    """The undecorated workflow body. ``@DBOS.scheduled`` + ``@DBOS.workflow``
    wrap it twice, and the outer wrappers refuse to run before DBOS is
    initialised — same ``__wrapped__`` unwrap the rest of the suite uses."""
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.mark.asyncio
async def test_step_returns_the_undispatched_queue_and_does_not_dispatch(monkeypatch):
    class _Proc:
        async def tick(self):
            return {"agents_processed": 1, "tasks_created": 1, "errors": 0}

    rows = [{"id": "t-1", "agent_id": "a-1"}, {"id": "t-2", "agent_id": "a-1"}]

    async def _list():
        return rows

    monkeypatch.setattr(wd, "_inbox_processor", lambda: _Proc())
    monkeypatch.setattr(wd, "_list_undispatched", _list)
    out = await wd.inbox_dispatch_tick_step()
    assert out["tasks_created"] == 1 and out["orders"] == rows


@pytest.mark.asyncio
async def test_workflow_body_dispatches_each_order_then_marks_it(monkeypatch):
    dispatched, marked = [], []

    async def _tick():
        return {
            "tasks_created": 1,
            "errors": 0,
            "orders": [{"id": "t-1", "agent_id": "a-1"}],
        }

    class _Pool:
        async def dispatch(self, task):
            dispatched.append(task)

    async def _mark(tid):
        marked.append(tid)

    monkeypatch.setattr(wd, "inbox_dispatch_tick_step", _tick)
    monkeypatch.setattr(wd, "_pool", lambda: _Pool())
    monkeypatch.setattr(wd, "_mark_dispatched", _mark)
    await _body(wd.inbox_dispatch_workflow)(None, None)
    assert [t["id"] for t in dispatched] == ["t-1"]
    assert marked == ["t-1"]


@pytest.mark.asyncio
async def test_one_bad_order_never_starves_the_rest(monkeypatch):
    """A dispatch that raises is logged and skipped — the remaining orders
    still go out. Without this the first poison task stalls the whole queue
    until someone notices, which is exactly the silent-stall class route C
    exists to prevent."""
    dispatched, marked = [], []

    async def _tick():
        return {
            "tasks_created": 0,
            "errors": 0,
            "orders": [
                {"id": "bad", "agent_id": "a-1"},
                {"id": "good", "agent_id": "a-1"},
            ],
        }

    class _Pool:
        async def dispatch(self, task):
            if task["id"] == "bad":
                raise RuntimeError("enqueue exploded")
            dispatched.append(task)

    async def _mark(tid):
        marked.append(tid)

    monkeypatch.setattr(wd, "inbox_dispatch_tick_step", _tick)
    monkeypatch.setattr(wd, "_pool", lambda: _Pool())
    monkeypatch.setattr(wd, "_mark_dispatched", _mark)
    await _body(wd.inbox_dispatch_workflow)(None, None)
    assert [t["id"] for t in dispatched] == ["good"]
    # The failed order is NOT marked dispatched — the next tick retries it.
    assert marked == ["good"]


def test_no_dispatch_call_inside_any_step_body():
    """Source guard: a stub cannot reproduce DBOS's in-step enqueue assertion."""
    bodies = re.findall(
        r"@DBOS\.step\(\)\s*\nasync def \w+\(.*?\n(.*?)(?=\n@|\Z)",
        inspect.getsource(wd),
        re.DOTALL,
    )
    assert bodies, "no @DBOS.step bodies found — the guard itself is broken"
    for body in bodies:
        assert "dispatch(" not in body, body[:200]


def test_inbox_processor_has_no_second_dispatch_path():
    """One dispatcher, one place. ``InboxProcessor`` used to accept a
    ``dispatcher`` and hand tasks off itself; nothing had passed one since
    PR-D8, and reviving it would mean the same task could be enqueued from
    inside a step (forbidden) AND from the workflow body — or, when the
    argument was omitted, from neither. The queue is shaped in one place and
    drained in one place."""
    from app.services.workforce.inbox_processor import InboxProcessor

    params = inspect.signature(InboxProcessor.__init__).parameters
    assert "dispatcher" not in params
    src = inspect.getsource(InboxProcessor)
    assert "self.dispatcher" not in src
