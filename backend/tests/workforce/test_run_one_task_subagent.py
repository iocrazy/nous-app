"""The worker's ``payload.kind == 'subagent'`` branch (phase 2b-2 §2.3).

Three things it owes the parent: the result on the inbox, a ``subagent_done``
on the PARENT run's transcript, and a decision about whether to also start a
turn — without enqueueing the result twice.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.workforce.agent_worker import run_one_task

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

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


def _wire(envelope=None, *, agent_repo_raises=False):
    task_holder: dict = {}
    workforce = MagicMock()
    workforce.update_task_status = AsyncMock(return_value=True)
    workforce.enqueue_outbox = AsyncMock(return_value={"id": "ob-1"})

    async def _claim(task_id, *, workflow_id):
        return task_holder["task"]

    workforce.claim_task = AsyncMock(side_effect=_claim)

    inbox_repo = SimpleNamespace(enqueue=AsyncMock(return_value={"id": 1}))
    writer = SimpleNamespace(append=AsyncMock())
    for_run = AsyncMock(return_value=writer)
    deliver = AsyncMock(return_value=SimpleNamespace(mode="dispatched"))
    run_bg = AsyncMock(
        return_value=envelope
        or {
            "status": "success",
            "summary": "s",
            "sub_run_id": "52",
            "tokens_used": 9,
        }
    )

    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(
        side_effect=(
            AssertionError("the persistent gate must not be reached")
            if agent_repo_raises
            else None
        )
    )

    return SimpleNamespace(
        task_holder=task_holder,
        workforce=workforce,
        inbox_repo=inbox_repo,
        writer=writer,
        deliver=deliver,
        run_bg=run_bg,
        agent_repo=agent_repo,
        for_run=for_run,
    )


def _patches(w):
    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.services.issues.inbox_or_dispatch as deliver_mod
    from app.services.ai.runner import run_recorder as recorder_mod

    return [
        patch(
            "app.services.workforce.agent_worker.get_agent_workforce_repository",
            return_value=w.workforce,
        ),
        patch(
            "app.services.workforce.agent_worker.get_agent_repository",
            return_value=w.agent_repo,
        ),
        patch.object(inbox_mod, "get_agent_run_inbox_repository", lambda: w.inbox_repo),
        patch.object(deliver_mod, "deliver_or_dispatch", w.deliver),
        patch.object(recorder_mod.RunEventWriter, "for_run", w.for_run),
        patch(
            "app.services.ai.runner.subagent_task_service.SubAgentTaskService."
            "run_background_task",
            w.run_bg,
        ),
    ]


async def _run(w, task):
    w.task_holder["task"] = task
    stack = _patches(w)
    for p in stack:
        p.start()
    try:
        return await run_one_task(task)
    finally:
        for p in reversed(stack):
            p.stop()


async def test_result_lands_on_the_inbox_as_subagent_result():
    w = _wire(agent_repo_raises=True)
    task = _task()
    out = await _run(w, task)

    assert out["status"] == "success" and out["run_id"] == "52"
    kw = w.inbox_repo.enqueue.await_args.kwargs
    assert kw["kind"] == "subagent_result"
    assert (kw["target_kind"], kw["target_id"]) == ("issue", 7)
    assert kw["content"]["child_run_id"] == "52"
    assert kw["content"]["subagent_type"] == "librarian"
    assert kw["content"]["summary"] == "s"


async def test_subagent_done_is_written_on_the_parent_run():
    """The event belongs to the PARENT's trajectory. Writing it on the child
    would put it on a transcript nobody reading the parent will ever open."""
    w = _wire(agent_repo_raises=True)
    task = _task()
    await _run(w, task)

    assert w.for_run.await_args.args[0] == int(PARENT_RUN_ID)
    event_type, payload = w.writer.append.await_args.args
    assert event_type == "subagent_done"
    assert payload["mode"] == "async" and payload["child_run_id"] == "52"
    assert payload["task_id"] == task["id"] and payload["status"] == "success"


async def test_issue_target_asks_whether_to_start_a_turn_without_re_enqueueing():
    w = _wire(agent_repo_raises=True)
    await _run(w, _task())

    w.deliver.assert_awaited_once()
    kw = w.deliver.await_args.kwargs
    assert kw["kind"] == "subagent_result" and kw["already_enqueued"] is True
    # Exactly one row: deliver_or_dispatch must not add a second.
    w.inbox_repo.enqueue.assert_awaited_once()


async def test_conversation_target_does_not_dispatch_an_issue_turn():
    w = _wire(agent_repo_raises=True)
    await _run(w, _task(reply_to={"target_kind": "conversation", "target_id": 42}))
    w.deliver.assert_not_awaited()
    assert w.inbox_repo.enqueue.await_args.kwargs["target_kind"] == "conversation"


async def test_a_failed_child_marks_the_task_failed():
    w = _wire(
        envelope={
            "status": "failed",
            "error": "boom",
            "summary": "",
            "sub_run_id": None,
            "tokens_used": 0,
        },
        agent_repo_raises=True,
    )
    out = await _run(w, _task())
    assert out["status"] == "failed"
    assert w.workforce.update_task_status.await_args.kwargs["lifecycle_status"] == (
        "failed"
    )


async def test_a_malformed_reply_target_is_typed_not_raised():
    """The child already ran and cost money. An exception here would lose its
    result and leave the task row un-finalised — so it is reported, not
    raised. But a result with nowhere to go is a FAILURE: calling it done
    would be the silent no-op this whole path exists to avoid."""
    w = _wire(agent_repo_raises=True)
    out = await _run(w, _task(reply_to={"target_kind": "issue"}))

    assert out["status"] == "failed"
    w.inbox_repo.enqueue.assert_not_awaited()
    w.deliver.assert_not_awaited()
    kw = w.workforce.update_task_status.await_args.kwargs
    assert kw["lifecycle_status"] == "failed"
    assert kw["error_code"] == "no_reply_target"


# ─── C1: nothing after the child ran may escape ───────────────────────


def _codes(w):
    return [
        c.kwargs.get("error_code")
        for c in w.workforce.update_task_status.await_args_list
    ]


async def test_a_crashing_child_still_emits_done_and_finalises():
    """The three awaits after the child ran used to sit OUTSIDE run_one_task's
    try. Any of them raising stranded the task at 'assigned' AND left the
    parent's children.async_pending permanently +1 — the spawn event had
    already told the user a child was out there."""
    w = _wire(agent_repo_raises=True)
    w.run_bg.side_effect = RuntimeError("provider exploded")

    out = await _run(w, _task())

    assert out["status"] == "failed"
    w.writer.append.assert_awaited_once()
    event_type, payload = w.writer.append.await_args.args
    assert event_type == "subagent_done" and payload["status"] == "failed"
    assert _codes(w) == ["subagent_crashed"]


async def test_a_failed_inbox_write_still_emits_done_and_finalises():
    w = _wire(agent_repo_raises=True)
    w.inbox_repo.enqueue.side_effect = RuntimeError("pg is down")

    out = await _run(w, _task())

    assert out["status"] == "failed"
    assert w.writer.append.await_args.args[0] == "subagent_done"
    assert _codes(w) == ["result_delivery_failed"]


async def test_a_failed_wake_still_emits_done_and_finalises():
    """``deliver_or_dispatch`` reads ``running_root_run_id``, which raises by
    design when the read fails (a busy gate must never read a failed query as
    'nothing running'). One DB blip used to strand the task."""
    w = _wire(agent_repo_raises=True)
    w.deliver.side_effect = RuntimeError("running_root_run_id blew up")

    out = await _run(w, _task())

    assert out["status"] == "failed"
    assert w.writer.append.await_args.args[0] == "subagent_done"
    assert _codes(w) == ["wake_failed"]
    # The result still reached the inbox — the wake is the part that failed.
    w.inbox_repo.enqueue.assert_awaited_once()


# ─── spec §2.2 item 4: the audit outbox row ───────────────────────────


async def test_the_audit_outbox_row_is_still_written():
    w = _wire(agent_repo_raises=True)
    task = _task()
    await _run(w, task)

    kw = w.workforce.enqueue_outbox.await_args.kwargs
    assert kw["message_type"] == "task_result"
    assert kw["recipient_kind"] == "user"
    assert str(kw["recipient_user_id"]) == task["payload"]["user_id"]
    assert kw["payload"]["run_id"] == "52"
    assert str(kw["task_id"]) == task["id"]


async def test_a_failed_audit_write_does_not_fail_a_delivered_task():
    """The outbox is an audit trail beside the delivery, not the delivery.
    Losing it is logged; calling the task failed would misreport a result the
    parent has already received."""
    w = _wire(agent_repo_raises=True)
    w.workforce.enqueue_outbox.side_effect = RuntimeError("outbox is down")

    out = await _run(w, _task())

    assert out["status"] == "success"
    assert (
        w.workforce.update_task_status.await_args.kwargs["lifecycle_status"] == "done"
    )


# ─── I2: the child run belongs to the issue ───────────────────────────


async def test_the_issue_id_reaches_the_child_service():
    w = _wire(agent_repo_raises=True)
    seen: dict = {}

    import app.services.ai.runner.subagent_task_service as svc_mod

    real_cls = svc_mod.SubAgentTaskService

    class _Recording(real_cls):
        def __init__(self, **kwargs):
            seen.update(kwargs)
            super().__init__(**kwargs)

    with patch.object(svc_mod, "SubAgentTaskService", _Recording):
        await _run(w, _task(issue_id=7))

    assert seen["issue_id"] == 7
    assert str(seen["parent_run_id"]) == PARENT_RUN_ID
