"""Route C on the workforce path: the STEP decides, the workflow BODY dispatches.

Task 7b defect B. ``_run_subagent_task`` runs inside ``run_one_task_step``
(a ``@DBOS.step``) and called ``deliver_or_dispatch`` there. Its idle arm
reaches ``DBOS.start_workflow``, which DBOS refuses from inside a step with
``AssertionError: assert cur_ctx.is_workflow()``. The 2026-09-10 re-verification
caught it firing on EVERY background sub-agent result delivered to an idle
issue — four times in ten minutes across three issues — and the Task 7a sweeper
masked it, so the only visible symptom was a ≤60 s wake-up delay plus an ERROR
with a full traceback in the worker log.

Same shape as ``test_inbox_drain_chain.py``: a behavioural guard driving the
real workflow body, and a structural (AST) guard, because a stub cannot
reproduce DBOS's own assertion.
"""

from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.issues.inbox_or_dispatch import DeliverResult
from app.workflows import agent_workforce as wf

pytestmark = pytest.mark.unit


def _body(fn):
    """The undecorated workflow body — ``@DBOS.workflow`` wraps it and refuses
    to run before DBOS is initialised."""
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


# ── structural guard: the worker module may not dispatch at all ─────────


DISPATCHERS = {"deliver_or_dispatch", "dispatch_issue_reply"}


def test_the_worker_module_never_names_a_dispatcher():
    """The whole module, not just the one function: ``run_one_task`` and
    everything it calls execute inside ``run_one_task_step``, so ANY reference
    to a dispatcher here is a workflow start from inside a step.

    Names, attributes and imports all count — ``from … import
    deliver_or_dispatch`` and ``deliver_mod.deliver_or_dispatch`` are the same
    call with different spelling."""
    from app.services.workforce import agent_worker

    tree = ast.parse(inspect.getsource(agent_worker))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in DISPATCHERS:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in DISPATCHERS:
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            found |= {a.name for a in node.names} & DISPATCHERS
    assert not found, (
        f"agent_worker.py dispatches {sorted(found)} — that code runs inside "
        "run_one_task_step (@DBOS.step) and DBOS asserts on start_workflow "
        "there. Return an idle_dispatch order and let the workflow body do it."
    )


# ── the step's half: an order, not a dispatch ───────────────────────────

PARENT_RUN_ID = "900"


def _task(**payload_kw):
    payload = {
        "kind": "subagent",
        "parent_run_id": PARENT_RUN_ID,
        "caller_agent_id": str(uuid4()),
        "subagent_type": "librarian",
        "prompt": "dig",
        "description": "d",
        "child_run_id": None,
        "reply_to": {"target_kind": "issue", "target_id": 7},
        "user_id": str(uuid4()),
        "agent_depth": 0,
    }
    payload.update(payload_kw)
    task_id = str(uuid4())
    return {
        "id": task_id,
        "agent_id": str(uuid4()),
        "user_id": payload["user_id"],
        "lifecycle_status": "assigned",
        "payload": payload,
        "workforce_workflow_id": f"workforce-{task_id}-1",
    }


async def _run_worker(task):
    from app.services.workforce.agent_worker import run_one_task

    workforce = MagicMock()
    workforce.update_task_status = AsyncMock(return_value=True)
    workforce.enqueue_outbox = AsyncMock(return_value={"id": "ob-1"})
    workforce.claim_task = AsyncMock(return_value=task)

    import app.repositories.agent_run_inbox_repository as inbox_mod
    from app.services.ai.runner import run_recorder as recorder_mod

    stack = [
        patch(
            "app.services.workforce.agent_worker.get_agent_workforce_repository",
            return_value=workforce,
        ),
        patch(
            "app.services.workforce.agent_worker.get_agent_repository",
            return_value=MagicMock(),
        ),
        patch.object(
            inbox_mod,
            "get_agent_run_inbox_repository",
            lambda: SimpleNamespace(enqueue=AsyncMock(return_value={"id": 1})),
        ),
        patch.object(
            recorder_mod.RunEventWriter,
            "for_run",
            AsyncMock(return_value=SimpleNamespace(append=AsyncMock())),
        ),
        patch(
            "app.services.ai.runner.subagent_task_service.SubAgentTaskService."
            "run_background_task",
            AsyncMock(
                return_value={
                    "status": "success",
                    "summary": "s",
                    "sub_run_id": "52",
                    "tokens_used": 9,
                }
            ),
        ),
    ]
    for p in stack:
        p.start()
    try:
        return await run_one_task(task)
    finally:
        for p in reversed(stack):
            p.stop()


async def test_an_issue_target_comes_back_as_an_order():
    task = _task()
    out = await _run_worker(task)
    assert out["idle_dispatch"] == {
        "issue_id": 7,
        "user_id": task["payload"]["user_id"],
    }


async def test_a_conversation_target_carries_no_order():
    """Only an issue has turns to start. A conversation result is filed and
    that is the whole delivery — an order here would dispatch an issue reply
    against an id that is not an issue."""
    out = await _run_worker(
        _task(reply_to={"target_kind": "conversation", "target_id": 42})
    )
    assert out["idle_dispatch"] is None


async def test_an_unusable_payload_still_carries_the_key():
    """Every return from this branch answers the question, including the ones
    that never ran a child. An absent key would make the body guess."""
    out = await _run_worker(_task(caller_agent_id="not-a-uuid"))
    assert out["status"] == "failed"
    assert out["idle_dispatch"] is None


# ── the body's half: it dispatches, outside every step ──────────────────


#: Every ``@DBOS.step`` on this path. The behavioural guard asserts none of
#: them is on the call stack when the dispatch happens — with no DBOS runtime
#: there is no context object to interrogate, but the stack says the same.
STEP_NAMES = {"run_one_task_step"}


async def test_the_workflow_body_dispatches_the_order_outside_every_step(
    monkeypatch,
):
    seen: list = []

    async def _deliver(issue_id, **kw):
        on_stack = {f.function for f in inspect.stack()} & STEP_NAMES
        assert not on_stack, f"deliver_or_dispatch ran INSIDE {on_stack}"
        seen.append((issue_id, kw))
        return DeliverResult("dispatched", workflow_id="issue-reply-1")

    async def _step(task):
        return {
            "task_id": "t-1",
            "status": "success",
            "run_id": "52",
            "idle_dispatch": {"issue_id": 7, "user_id": "u-1"},
        }

    import app.services.issues.inbox_or_dispatch as deliver_mod

    monkeypatch.setattr(wf, "run_one_task_step", _step)
    monkeypatch.setattr(deliver_mod, "deliver_or_dispatch", _deliver)

    out = await _body(wf.agent_workforce_workflow)({"id": "t-1", "agent_id": "a"})

    assert out["status"] == "success"
    assert seen and seen[0][0] == 7
    assert seen[0][1]["already_enqueued"] is True
    assert seen[0][1]["user_id"] == "u-1"


async def test_no_order_means_no_dispatch(monkeypatch):
    called = MagicMock()

    async def _step(task):
        return {"task_id": "t-1", "status": "success", "run_id": None}

    import app.services.issues.inbox_or_dispatch as deliver_mod

    monkeypatch.setattr(wf, "run_one_task_step", _step)
    monkeypatch.setattr(deliver_mod, "deliver_or_dispatch", called)
    await _body(wf.agent_workforce_workflow)({"id": "t-1", "agent_id": "a"})
    called.assert_not_called()


async def test_a_dispatch_that_fails_is_loud_and_does_not_sink_the_workflow(
    monkeypatch,
):
    """The child already ran and its result is already on the inbox. Losing
    the wake-up costs at most the sweeper's next tick; raising here would
    fail a workflow whose real work succeeded."""
    from loguru import logger

    import app.services.issues.inbox_or_dispatch as deliver_mod

    async def _step(task):
        return {
            "task_id": "t-1",
            "status": "success",
            "run_id": "52",
            "idle_dispatch": {"issue_id": 7, "user_id": "u-1"},
        }

    async def _boom(issue_id, **kw):
        raise RuntimeError("running_root_run_id blew up")

    monkeypatch.setattr(wf, "run_one_task_step", _step)
    monkeypatch.setattr(deliver_mod, "deliver_or_dispatch", _boom)

    lines: list[str] = []
    sink = logger.add(lines.append, level="ERROR", format="{message}")
    try:
        out = await _body(wf.agent_workforce_workflow)({"id": "t-1", "agent_id": "a"})
    finally:
        logger.remove(sink)

    assert out["status"] == "success"
    assert "7" in "".join(lines)


async def test_a_typed_dispatch_failure_is_reported_as_an_error(monkeypatch):
    """``deliver_or_dispatch`` returns its idle-arm failure as a VALUE. Reading
    that as "nothing to do" is how the first cut of the sweeper's drain read
    as working while never firing."""
    from loguru import logger

    import app.services.issues.inbox_or_dispatch as deliver_mod

    async def _step(task):
        return {
            "task_id": "t-1",
            "status": "success",
            "run_id": "52",
            "idle_dispatch": {"issue_id": 7, "user_id": "u-1"},
        }

    async def _typed(issue_id, **kw):
        return DeliverResult("skipped", reason="dispatch_failed: boom")

    monkeypatch.setattr(wf, "run_one_task_step", _step)
    monkeypatch.setattr(deliver_mod, "deliver_or_dispatch", _typed)

    lines: list[str] = []
    sink = logger.add(lines.append, level="ERROR", format="{message}")
    try:
        await _body(wf.agent_workforce_workflow)({"id": "t-1", "agent_id": "a"})
    finally:
        logger.remove(sink)

    assert "dispatch_failed" in "".join(lines)


# ── the subprocess mode carries the order too ───────────────────────────


def test_the_isolated_envelope_carries_the_order():
    """``AGENT_RUN_ISOLATION=subprocess`` swaps the in-process call for a child
    process and re-serialises its result. A key dropped there is a wake-up
    that silently never happens in exactly one deployment mode."""
    from app.services.workforce.isolated_runner import IsolatedRunResult

    order = {"issue_id": 7, "user_id": "u-1"}
    result = IsolatedRunResult(
        status="success",
        task_id="t-1",
        run_id="52",
        error_code=None,
        error_message=None,
        exit_code=0,
        idle_dispatch=order,
    )
    assert result.as_worker_dict()["idle_dispatch"] == order
