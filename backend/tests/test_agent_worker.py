"""Unit tests for the M3 agent worker runtime.

Covers the contract a worker must honour:
- Happy path: queued/assigned → in_progress → done + outbox row
- Refuses non-persistent agents
- Refuses tasks whose inherited depth exceeds MAX_INHERITED_DEPTH
- Refuses tasks already past 'assigned' (idempotent on retry)
- Translates runtime exceptions to lifecycle_status='failed'
- Routes outbox to user vs agent based on inbox sender_kind
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.services.workforce.agent_worker import (
    MAX_INHERITED_DEPTH,
    run_one_task,
)

# ─── helpers ──────────────────────────────────────────────────────────


def _task(
    *,
    agent_id: UUID | None = None,
    user_id: UUID | None = None,
    prompt: str = "do the thing",
    depth: int = 0,
    parent_run_id: UUID | None = None,
    inbox_message_id: UUID | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"prompt": prompt}
    if depth:
        payload["delegated_at_depth"] = depth
    if parent_run_id:
        payload["parent_run_id"] = str(parent_run_id)
    task_id = str(uuid4())
    return {
        "id": task_id,
        "agent_id": str(agent_id or uuid4()),
        "user_id": str(user_id or uuid4()),
        "lifecycle_status": "assigned",
        "payload": payload,
        "inbox_message_id": str(inbox_message_id) if inbox_message_id else None,
        # Every task that reaches the worker was dispatched, and dispatch is
        # what mints this. run_one_task refuses a task without it — see
        # test_missing_workflow_id_is_refused_not_coerced, which builds its own
        # dict precisely to omit it.
        "workforce_workflow_id": f"workforce-{task_id}-1",
    }


def _persistent_agent(
    *,
    agent_id: UUID,
    slug: str = "summarize",
    persistent: bool = True,
    model: str = "doubao-seed-2-0-pro-260215",
) -> dict[str, Any]:
    return {
        "id": str(agent_id),
        "slug": slug,
        "name": slug,
        "model": model,
        "persistent": persistent,
        "fallback_models": [],
        "budget_per_run_cents": None,
    }


def _build_runner_stack_mock(content: str = "OK"):
    """Returns a MagicMock that mimics AgentRunnerStack with a runner
    whose run_turn returns ``{'content': content}``."""
    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": content})
    stack = MagicMock()
    stack.runner = runner
    stack.graph_facts = []
    stack.user_context = None
    stack.primary_model = "doubao-seed-2-0-pro-260215"
    stack.fallback_chain_active = False
    return stack


def _run_recorder_cm(run_id: UUID):
    """Build an async-context-manager mock that returns a RunRecorder
    stub with the given run_id and noop set_summaries."""
    recorder = MagicMock()
    recorder.run_id = run_id
    recorder.prompt_tokens = 10
    recorder.completion_tokens = 5
    recorder.set_summaries = MagicMock()

    class _CM:
        async def __aenter__(self):
            return recorder

        async def __aexit__(self, exc_type, exc, tb):
            return False

    return _CM(), recorder


# ─── happy path ───────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_happy_path_queued_to_done_with_outbox():
    agent_id = uuid4()
    user_id = uuid4()
    task = _task(agent_id=agent_id, user_id=user_id, prompt="summarise this PR")

    workforce = MagicMock()
    workforce.INBOX_TABLE = "agent_inbox"
    workforce.claim_task = AsyncMock(
        return_value={**task, "lifecycle_status": "assigned"}
    )
    workforce.update_task_status = AsyncMock(return_value=True)
    workforce.enqueue_outbox = AsyncMock(return_value={"id": str(uuid4())})

    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(return_value=_persistent_agent(agent_id=agent_id))

    stack = _build_runner_stack_mock(content="Done summary.")
    cm, recorder = _run_recorder_cm(run_id=uuid4())

    with (
        patch(
            "app.services.workforce.agent_worker.get_agent_workforce_repository",
            return_value=workforce,
        ),
        patch(
            "app.services.workforce.agent_worker.get_agent_repository",
            return_value=agent_repo,
        ),
        patch(
            "app.services.workforce.agent_worker.get_skill_repository",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.workforce.agent_worker.build_agent_runner_stack",
            AsyncMock(return_value=stack),
        ),
        patch("app.services.workforce.agent_worker.PromptComposer") as PC,
        patch("app.services.workforce.agent_worker.RunRecorder", return_value=cm),
        patch(
            "app.services.workforce.agent_worker._lookup_inbox_message",
            AsyncMock(return_value=None),
        ),
        patch("app.services.workforce.agent_worker._attach_to_parent_run", AsyncMock()),
    ):
        composer = MagicMock()
        composer.compose = AsyncMock(
            return_value=MagicMock(
                agent_id=agent_id,
                agent_slug="summarize",
                model="doubao-seed-2-0-pro-260215",
            )
        )
        PC.return_value = composer

        result = await run_one_task(task)

    assert result["status"] == "done"
    assert result["run_id"] == str(recorder.run_id)

    # Lifecycle: in_progress then done
    statuses = [
        c.kwargs["lifecycle_status"]
        for c in workforce.update_task_status.await_args_list
    ]
    assert statuses == ["in_progress", "done"]

    # Outbox row written with task_result content
    workforce.enqueue_outbox.assert_awaited_once()
    outbox_kwargs = workforce.enqueue_outbox.await_args.kwargs
    assert outbox_kwargs["sender_agent_id"] == agent_id
    assert (
        outbox_kwargs["recipient_kind"] == "user"
    )  # default fallback when no inbox_msg
    assert outbox_kwargs["payload"]["content"] == "Done summary."
    assert outbox_kwargs["message_type"] == "task_result"


# ─── refuses non-persistent agent ────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refuses_non_persistent_agent():
    agent_id = uuid4()
    task = _task(agent_id=agent_id)

    workforce = MagicMock()
    workforce.INBOX_TABLE = "agent_inbox"
    workforce.claim_task = AsyncMock(
        return_value={**task, "lifecycle_status": "assigned"}
    )
    workforce.update_task_status = AsyncMock(return_value=True)

    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(
        return_value=_persistent_agent(agent_id=agent_id, persistent=False)
    )

    with (
        patch(
            "app.services.workforce.agent_worker.get_agent_workforce_repository",
            return_value=workforce,
        ),
        patch(
            "app.services.workforce.agent_worker.get_agent_repository",
            return_value=agent_repo,
        ),
    ):
        result = await run_one_task(task)

    assert result["status"] == "failed"
    update_kwargs = workforce.update_task_status.await_args.kwargs
    assert update_kwargs["lifecycle_status"] == "failed"
    assert update_kwargs["error_code"] == "not_persistent"


# ─── refuses depth > MAX ─────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refuses_depth_exceeded():
    """delegated_at_depth + 1 > MAX_INHERITED_DEPTH → failed.

    The depth check runs AFTER the claim (2b-2 T3 fix round): writing a failure
    onto a row this worker does not own would stamp someone else's task."""
    task = _task(depth=MAX_INHERITED_DEPTH)  # +1 = MAX+1, over the line

    workforce = MagicMock()
    workforce.INBOX_TABLE = "agent_inbox"
    workforce.claim_task = AsyncMock(
        return_value={**task, "lifecycle_status": "assigned"}
    )
    workforce.update_task_status = AsyncMock(return_value=True)

    with patch(
        "app.services.workforce.agent_worker.get_agent_workforce_repository",
        return_value=workforce,
    ):
        result = await run_one_task(task)

    assert result["status"] == "failed"
    update_kwargs = workforce.update_task_status.await_args.kwargs
    assert update_kwargs["error_code"] == "depth_exceeded"


# ─── idempotency: skip already-claimed task ──────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skips_task_no_longer_claimable():
    task = _task()

    workforce = MagicMock()
    workforce.INBOX_TABLE = "agent_inbox"
    # The CAS lost: another worker flipped queued → assigned first, so the
    # UPDATE matched zero rows. Zero rows is the ONLY honest signal here — the
    # read-then-check this replaced could see 'assigned' and still be the
    # second worker to act on it.
    workforce.claim_task = AsyncMock(return_value=None)
    workforce.update_task_status = AsyncMock(return_value=True)

    with patch(
        "app.services.workforce.agent_worker.get_agent_workforce_repository",
        return_value=workforce,
    ):
        result = await run_one_task({**task, "workforce_workflow_id": "wf-1"})

    assert result["status"] == "skipped"
    assert result["reason"] == "not_claimable"
    workforce.claim_task.assert_awaited_once_with(task["id"], workflow_id="wf-1")
    workforce.update_task_status.assert_not_called()


# ─── empty prompt ────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_prompt_fails_fast():
    agent_id = uuid4()
    task = _task(agent_id=agent_id, prompt="")  # missing prompt

    workforce = MagicMock()
    workforce.INBOX_TABLE = "agent_inbox"
    workforce.claim_task = AsyncMock(
        return_value={**task, "lifecycle_status": "assigned"}
    )
    workforce.update_task_status = AsyncMock(return_value=True)

    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(return_value=_persistent_agent(agent_id=agent_id))

    with (
        patch(
            "app.services.workforce.agent_worker.get_agent_workforce_repository",
            return_value=workforce,
        ),
        patch(
            "app.services.workforce.agent_worker.get_agent_repository",
            return_value=agent_repo,
        ),
    ):
        result = await run_one_task(task)

    assert result["status"] == "failed"
    update_kwargs = workforce.update_task_status.await_args.kwargs
    assert update_kwargs["error_code"] == "empty_prompt"


# ─── runtime exception → failed ──────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_turn_exception_marks_failed():
    agent_id = uuid4()
    task = _task(agent_id=agent_id, prompt="hi")

    workforce = MagicMock()
    workforce.INBOX_TABLE = "agent_inbox"
    workforce.claim_task = AsyncMock(
        return_value={**task, "lifecycle_status": "assigned"}
    )
    workforce.update_task_status = AsyncMock(return_value=True)

    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(return_value=_persistent_agent(agent_id=agent_id))

    stack = _build_runner_stack_mock()
    stack.runner.run_turn = AsyncMock(side_effect=RuntimeError("LLM 500"))
    cm, _ = _run_recorder_cm(run_id=uuid4())

    with (
        patch(
            "app.services.workforce.agent_worker.get_agent_workforce_repository",
            return_value=workforce,
        ),
        patch(
            "app.services.workforce.agent_worker.get_agent_repository",
            return_value=agent_repo,
        ),
        patch(
            "app.services.workforce.agent_worker.get_skill_repository",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.workforce.agent_worker.build_agent_runner_stack",
            AsyncMock(return_value=stack),
        ),
        patch("app.services.workforce.agent_worker.PromptComposer") as PC,
        patch("app.services.workforce.agent_worker.RunRecorder", return_value=cm),
        patch("app.services.workforce.agent_worker._attach_to_parent_run", AsyncMock()),
    ):
        composer = MagicMock()
        composer.compose = AsyncMock(
            return_value=MagicMock(
                agent_id=agent_id,
                agent_slug="summarize",
                model="doubao-seed-2-0-pro-260215",
            )
        )
        PC.return_value = composer

        result = await run_one_task(task)

    assert result["status"] == "failed"
    # Final update should be 'failed' with runtime_error
    statuses = [
        c.kwargs["lifecycle_status"]
        for c in workforce.update_task_status.await_args_list
    ]
    assert statuses[-1] == "failed"
    assert (
        workforce.update_task_status.await_args_list[-1].kwargs["error_code"]
        == "runtime_error"
    )


# ─── outbox routing ─────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outbox_routes_to_agent_when_sender_kind_agent():
    """If the inbox row says sender_kind='agent', the outbox should
    route back to that agent (cross-agent reply)."""
    agent_id = uuid4()
    user_id = uuid4()
    sender_agent = uuid4()
    inbox_id = uuid4()
    task = _task(agent_id=agent_id, user_id=user_id, inbox_message_id=inbox_id)

    workforce = MagicMock()
    workforce.INBOX_TABLE = "agent_inbox"
    workforce.claim_task = AsyncMock(
        return_value={**task, "lifecycle_status": "assigned"}
    )
    workforce.update_task_status = AsyncMock(return_value=True)
    workforce.enqueue_outbox = AsyncMock(return_value={"id": str(uuid4())})

    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(return_value=_persistent_agent(agent_id=agent_id))

    stack = _build_runner_stack_mock(content="reply")
    cm, _ = _run_recorder_cm(run_id=uuid4())

    inbox_lookup = AsyncMock(
        return_value={
            "sender_kind": "agent",
            "sender_user_id": str(user_id),
            "sender_agent_id": str(sender_agent),
        }
    )

    with (
        patch(
            "app.services.workforce.agent_worker.get_agent_workforce_repository",
            return_value=workforce,
        ),
        patch(
            "app.services.workforce.agent_worker.get_agent_repository",
            return_value=agent_repo,
        ),
        patch(
            "app.services.workforce.agent_worker.get_skill_repository",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.workforce.agent_worker.build_agent_runner_stack",
            AsyncMock(return_value=stack),
        ),
        patch("app.services.workforce.agent_worker.PromptComposer") as PC,
        patch("app.services.workforce.agent_worker.RunRecorder", return_value=cm),
        patch(
            "app.services.workforce.agent_worker._lookup_inbox_message", inbox_lookup
        ),
        patch("app.services.workforce.agent_worker._attach_to_parent_run", AsyncMock()),
    ):
        composer = MagicMock()
        composer.compose = AsyncMock(
            return_value=MagicMock(
                agent_id=agent_id,
                agent_slug="summarize",
                model="doubao-seed-2-0-pro-260215",
            )
        )
        PC.return_value = composer

        await run_one_task(task)

    outbox_kwargs = workforce.enqueue_outbox.await_args.kwargs
    assert outbox_kwargs["recipient_kind"] == "agent"
    assert outbox_kwargs["recipient_agent_id"] == sender_agent


# ─── claim ownership + hydration ──────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_claim_is_keyed_on_this_run_s_own_workflow_id():
    """The worker claims with the DBOS workflow id it is running under.

    That id is what lets a replay of THIS run re-enter its own row after the
    worker died mid-flight, while still refusing a different worker. Passing
    anything else — a recomputed id, a stale one — turns the recovery arm into
    a lock nobody holds the key to."""
    task = _task()

    workforce = MagicMock()
    workforce.INBOX_TABLE = "agent_inbox"
    workforce.claim_task = AsyncMock(return_value=None)

    with patch(
        "app.services.workforce.agent_worker.get_agent_workforce_repository",
        return_value=workforce,
    ):
        await run_one_task({"id": task["id"], "workforce_workflow_id": "workforce-x-7"})

    workforce.claim_task.assert_awaited_once_with(
        task["id"], workflow_id="workforce-x-7"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_worker_hydrates_agent_user_and_payload_from_the_claim():
    """A dispatch order carries only id / agent_id / attempt; everything the
    run needs comes back from the claim.

    Reading the row at claim time rather than trusting the dispatch-time
    snapshot also means an order that sat in the queue acts on the CURRENT
    payload, not the one captured when it was enqueued."""
    agent_id = uuid4()
    user_id = uuid4()
    full = _task(agent_id=agent_id, user_id=user_id, prompt="summarise this PR")

    workforce = MagicMock()
    workforce.INBOX_TABLE = "agent_inbox"
    workforce.claim_task = AsyncMock(
        return_value={**full, "lifecycle_status": "assigned"}
    )
    workforce.update_task_status = AsyncMock(return_value=True)
    workforce.enqueue_outbox = AsyncMock(return_value={"id": str(uuid4())})

    agent_repo = MagicMock()
    agent_repo.get_by_id = AsyncMock(return_value=_persistent_agent(agent_id=agent_id))

    stack = _build_runner_stack_mock(content="Done.")
    cm, _recorder = _run_recorder_cm(run_id=uuid4())

    # The envelope: no agent_id, no user_id, no payload.
    envelope = {"id": full["id"], "workforce_workflow_id": "workforce-y-1"}

    with (
        patch(
            "app.services.workforce.agent_worker.get_agent_workforce_repository",
            return_value=workforce,
        ),
        patch(
            "app.services.workforce.agent_worker.get_agent_repository",
            return_value=agent_repo,
        ),
        patch(
            "app.services.workforce.agent_worker.get_skill_repository",
            return_value=MagicMock(list_for_agent=AsyncMock(return_value=[])),
        ),
        patch(
            "app.services.workforce.agent_worker.build_agent_runner_stack",
            return_value=stack,
        ),
        patch("app.services.workforce.agent_worker.RunRecorder", return_value=cm),
        patch(
            "app.services.workforce.agent_worker.resolve_dispatch_scope",
            return_value=MagicMock(team_id=None, project_id=None),
        ),
    ):
        result = await run_one_task(envelope)

    # It got far enough to resolve the agent — which it could only do with the
    # agent_id the claim returned.
    agent_repo.get_by_id.assert_awaited_once_with(agent_id)
    assert result["status"] != "skipped"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_workflow_id_is_refused_not_coerced():
    """An absent ownership token fails the task; it must not become "".

    The empty string is not a harmless default — ``claim_task`` WRITES whatever
    it is given into ``metadata.workforce_workflow_id``. Two tasks dispatched
    without an id would both store "", and each would then satisfy the other's
    re-entry arm: an ownership check that admits anyone. Refusing turns a
    future producer that forgets the key into a visible typed failure instead
    of a quiet correctness hole."""
    task = _task()

    workforce = MagicMock()
    workforce.INBOX_TABLE = "agent_inbox"
    workforce.claim_task = AsyncMock(return_value=None)
    workforce.update_task_status = AsyncMock(return_value=True)

    with patch(
        "app.services.workforce.agent_worker.get_agent_workforce_repository",
        return_value=workforce,
    ):
        result = await run_one_task({"id": task["id"]})  # no workforce_workflow_id

    assert result["status"] == "failed"
    assert workforce.update_task_status.await_args.kwargs["error_code"] == (
        "missing_workflow_id"
    )
    # It never reached the claim — an unowned claim is what we are preventing.
    workforce.claim_task.assert_not_awaited()
