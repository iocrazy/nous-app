"""The workforce chain, end to end with a stubbed DBOS: the @DBOS.step only
SHAPES the queue and returns orders; the workflow BODY dispatches. Enqueuing
from inside a step is route-C forbidden (the empty-string AssertionError of
PR #495) — a source guard pins that, because a stub cannot reproduce it."""

from __future__ import annotations

import ast
import inspect

import pytest

from app.services.workforce.dbos_pool import DispatchRecord as _Record
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

    rows = [
        {"id": "t-1", "agent_id": "a-1", "payload": {"prompt": "long"}},
        {"id": "t-2", "agent_id": "a-1", "dispatch_attempt": 3, "payload": {}},
    ]

    async def _list():
        return rows

    monkeypatch.setattr(wd, "_inbox_processor", lambda: _Proc())
    monkeypatch.setattr(wd, "_list_undispatched", _list)
    out = await wd.inbox_dispatch_tick_step()
    assert out["tasks_created"] == 1
    # Envelopes, not whole rows — see test_orders_carry_only_what_the_dispatch_needs.
    assert out["orders"] == [
        {"id": "t-1", "agent_id": "a-1", "dispatch_attempt": 0},
        {"id": "t-2", "agent_id": "a-1", "dispatch_attempt": 3},
    ]


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
            return _Record(workflow_id="workforce-t-1-1", attempt=1)

    async def _mark(tid, *, workflow_id, attempt):
        marked.append((tid, workflow_id, attempt))

    monkeypatch.setattr(wd, "inbox_dispatch_tick_step", _tick)
    monkeypatch.setattr(wd, "_pool", lambda: _Pool())
    monkeypatch.setattr(wd, "_mark_dispatched", _mark)
    await _body(wd.inbox_dispatch_workflow)(None, None)
    assert [t["id"] for t in dispatched] == ["t-1"]
    # The id the pool actually enqueued is what gets persisted — recomputing it
    # here would let the stored token drift from the enqueued one, and the
    # replay re-entry check compares them for equality.
    assert marked == [("t-1", "workforce-t-1-1", 1)]


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
            return _Record(workflow_id=f"workforce-{task['id']}-1", attempt=1)

    async def _mark(tid, *, workflow_id, attempt):
        marked.append(tid)

    monkeypatch.setattr(wd, "inbox_dispatch_tick_step", _tick)
    monkeypatch.setattr(wd, "_pool", lambda: _Pool())
    monkeypatch.setattr(wd, "_mark_dispatched", _mark)
    await _body(wd.inbox_dispatch_workflow)(None, None)
    assert [t["id"] for t in dispatched] == ["good"]
    # The failed order is NOT marked dispatched — the next tick retries it.
    assert marked == ["good"]


def test_no_dispatch_call_inside_any_step_body():
    """Source guard: a stub cannot reproduce DBOS's in-step enqueue assertion.

    Walks the AST rather than matching text. The regex this replaced only saw a
    bare ``@DBOS.step()`` followed immediately by ``async def`` — so
    ``@DBOS.step(retries_allowed=True)``, a sync step, or a second decorator on
    the same function would slip past silently, and ``assert bodies`` only
    proves SOMETHING matched, never that nothing was missed. It also swallowed
    every module-level helper between one step and the next ``@``, so the
    outbox step's "body" included four functions that are not in it."""
    tree = ast.parse(inspect.getsource(wd))

    def _is_dbos_step(dec: ast.expr) -> bool:
        node = dec.func if isinstance(dec, ast.Call) else dec  # step() or step
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
    assert steps, "no @DBOS.step functions found — the guard itself is broken"
    # Both steps in this module must be covered, so the count is pinned: a new
    # step added without a thought about route C fails here rather than being
    # quietly waved through.
    assert {s.name for s in steps} == {
        "outbox_dispatch_tick_step",
        "inbox_dispatch_tick_step",
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
        assert "dispatch" not in called, f"{step.name} enqueues from inside a step"
        assert "_pool" not in called, f"{step.name} reaches the queue pool"


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


@pytest.mark.asyncio
async def test_a_dropped_dispatch_is_not_stamped_as_sent(monkeypatch):
    """``dispatch`` returning None means nothing reached the queue.

    Stamping ``dispatched_at`` anyway would hide the row from every later tick
    while no workflow exists to run it — the row is then queued forever and
    executed never, with the dispatch counter claiming success. The absent
    stamp is what makes the next tick pick it up again."""
    marked = []

    async def _tick():
        return {
            "tasks_created": 0,
            "errors": 0,
            "orders": [{"id": "t-9", "agent_id": "a-1"}],
        }

    class _Pool:
        async def dispatch(self, task):
            return None  # closed pool / malformed row / enqueue failure

    async def _mark(tid, *, workflow_id, attempt):
        marked.append(tid)

    monkeypatch.setattr(wd, "inbox_dispatch_tick_step", _tick)
    monkeypatch.setattr(wd, "_pool", lambda: _Pool())
    monkeypatch.setattr(wd, "_mark_dispatched", _mark)
    await _body(wd.inbox_dispatch_workflow)(None, None)
    assert marked == []


def test_orders_carry_only_what_the_dispatch_needs():
    """The step's return value is checkpointed into ``dbos.operation_outputs``
    every 10 seconds, forever. Whole task rows — prompt text included — would
    be written there for up to 100 tasks a tick, in addition to the copy DBOS
    already stores as the workflow input. The order is an envelope; the worker
    reads the authoritative row back from the database when it claims it, which
    also means it acts on the CURRENT row rather than a dispatch-time snapshot."""
    order = wd._order_envelope(
        {
            "id": "t-1",
            "agent_id": "a-1",
            "user_id": "u-1",
            "dispatch_attempt": 2,
            "payload": {"prompt": "x" * 10_000},
            "title": "unused by dispatch",
        }
    )
    assert order == {"id": "t-1", "agent_id": "a-1", "dispatch_attempt": 2}
