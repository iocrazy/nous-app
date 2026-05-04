"""Agent worker runtime — M3 execution layer.

Where M2 stops: it wrote inbox/task/outbox tables + a state machine,
but nothing actually picked tasks off the queue and ran them. This
module is what the AgentWorkerPool dispatches to. It mirrors the
ChatPanel chat path (compose → AgentRunner.run_turn → RunRecorder)
but reads input from ``agent_tasks.payload`` and writes output to
``agent_outbox`` so the calling agent can see the result.

## Lifecycle of a task as it flows through here

    queued (set by inbox processor)
        ↓ claim_next_queued — CAS guard, flips to 'assigned'
    assigned
        ↓ this module enters
    in_progress  ← started_at set
        ↓ run_turn() — LLM call(s), tool dispatch
    done | failed  ← ended_at set, result/error fields populated
        ↓ outbox row written (delivers result to calling user/agent)

## What the calling user/agent sees

When this worker finishes a task, it writes an ``agent_outbox`` row
addressed to the original sender (user or agent). The OutboxDispatcher
loop (M2) picks it up and:
  - For ``recipient_kind='user'``: marks delivered (Realtime fires the
    INSERT event to subscribed frontends — the user's ChatPanel sees
    the response in their inbox subscription).
  - For ``recipient_kind='agent'``: re-enqueues into recipient agent's
    inbox so the calling agent can read the result on its next turn.

## Failure semantics

Any exception inside ``run_one_task`` is caught and translated into a
``lifecycle_status='failed'`` UPDATE with ``error_code`` + ``error_message``
populated. The worker pool above swallows the exception so a crash
doesn't poison other agents.

The state machine doesn't run on failure paths from this module — task
lifecycle and worker state are tracked separately. Worker state moves
(idle → working → idle) are wired in by the inbox processor (M2) when
it claims an unread message.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from app.core.config import settings
from app.repositories.agent_repository import AgentRepository
from app.repositories.agent_workforce_repository import AgentWorkforceRepository
from app.repositories.skill_repository import SkillRepository
from app.services.ai_adapters.factory import provider_key_for_model
from app.services.ai_library_chat_wiring import build_agent_runner_stack
from app.services.prompt_composer import ComposerInput, PromptComposer
from app.services.run_recorder import AgentPausedError, RunRecorder

logger = logging.getLogger(__name__)


# Hard cap on inherited delegation depth before the worker refuses to run
# anything. Mirrors DelegateToolService.MAX_DELEGATION_DEPTH so deeply-
# nested chains never reach a LLM call.
MAX_INHERITED_DEPTH = 3


async def run_one_task(task: dict[str, Any]) -> dict[str, Any]:
    """Execute one ``agent_task`` end-to-end.

    Idempotent on retry: if the task is no longer in 'assigned' state
    (e.g. another worker already picked it up after a previous
    dispatch), this function early-returns. The CAS guard inside
    ``update_task_status`` prevents duplicate work.

    Returns a small dict for observability:
      ``{"task_id": str, "status": str, "run_id": str|None}``
    """
    task_id_str = task.get("id")
    if not task_id_str:
        logger.error(f"[agent-worker] task missing id: {task}")
        return {"task_id": None, "status": "skipped", "reason": "missing_id"}
    task_id = UUID(task_id_str)
    agent_id = UUID(task["agent_id"])
    user_id = UUID(task["user_id"])

    workforce = AgentWorkforceRepository()

    # Refuse to enter the run if depth budget is already blown. The
    # inbox processor should have caught this earlier via DelegateTool,
    # but defense-in-depth is cheap.
    payload = task.get("payload") or {}
    inherited_depth = int(payload.get("delegated_at_depth") or 0) + 1
    if inherited_depth > MAX_INHERITED_DEPTH:
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="failed",
            error_code="depth_exceeded",
            error_message=f"inherited depth {inherited_depth} > max {MAX_INHERITED_DEPTH}",
        )
        return {"task_id": str(task_id), "status": "failed", "run_id": None}

    # Flip queued/assigned → in_progress. The CAS inside update_task_status
    # accepts any current state for the new value, but we also want to
    # bail if someone else already moved it past 'assigned'.
    fresh = await workforce.get_task(task_id)
    if not fresh or fresh.get("lifecycle_status") not in ("queued", "assigned"):
        logger.info(
            f"[agent-worker] task {task_id} no longer claimable "
            f"(status={fresh.get('lifecycle_status') if fresh else 'gone'})"
        )
        return {"task_id": str(task_id), "status": "skipped", "reason": "not_claimable"}

    # Resolve the agent record for model + budget + identity.
    agent_repo = AgentRepository()
    skill_repo = SkillRepository()
    agent = await agent_repo.get_by_id(agent_id)
    if not agent:
        logger.error(f"[agent-worker] agent {agent_id} not found for task {task_id}")
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="failed",
            error_code="agent_not_found",
            error_message=f"agent_id {agent_id} no longer exists",
        )
        return {"task_id": str(task_id), "status": "failed", "run_id": None}

    # Persistent flag is the gate: refuse to run a non-persistent agent
    # via the workforce path. (The DelegateTool already rejects this on
    # the dispatch side; this is the runner-side equivalent.)
    if not agent.get("persistent"):
        logger.warning(
            f"[agent-worker] agent {agent['slug']} is not persistent — refusing"
        )
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="failed",
            error_code="not_persistent",
            error_message=f"agent '{agent['slug']}' lacks persistent=true",
        )
        return {"task_id": str(task_id), "status": "failed", "run_id": None}

    # Mark in_progress before the LLM call so observability shows what
    # the worker is doing right now.
    parent_run_id_raw = payload.get("parent_run_id")
    parent_run_id: Optional[UUID] = (
        UUID(parent_run_id_raw) if parent_run_id_raw else None
    )

    user_query = payload.get("prompt") or ""
    if not user_query:
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="failed",
            error_code="empty_prompt",
            error_message="task payload missing 'prompt'",
        )
        return {"task_id": str(task_id), "status": "failed", "run_id": None}

    await workforce.update_task_status(
        task_id=task_id,
        lifecycle_status="in_progress",
    )

    # ── Build the runner stack (parent_run_id + depth inherited) ─────
    try:
        stack = await build_agent_runner_stack(
            agent=agent,
            skill_repo=skill_repo,
            user_id=user_id,
            session_id=None,  # workforce runs aren't bound to a chat session
            user_query=user_query,
            settings=settings,
            parent_run_id=parent_run_id,
            agent_depth=inherited_depth,
        )

        composer = PromptComposer(agent_repo, skill_repo)
        composed = await composer.compose(
            ComposerInput(
                agent_slug=agent["slug"],
                request_instructions=(
                    "You are running as a workforce-dispatched agent. "
                    "A peer agent has handed you a task to complete. "
                    "Produce a complete, self-contained response — the "
                    "result will be delivered back as a single message."
                ),
                recalled_memories=stack.recalled_memories,
            )
        )

        # ── Run the turn under RunRecorder ───────────────────────────
        model = composed.model or ""
        try:
            provider = provider_key_for_model(model) if model else None
        except ValueError:
            provider = None

        async with RunRecorder(
            agent_id=composed.agent_id,
            user_id=user_id,
            trigger="workforce",
            session_id=None,
            team_id=None,
            project_id=None,
            model=model or None,
            provider=provider,
            input_summary=user_query,
            metadata={
                "task_id": str(task_id),
                "parent_run_id": str(parent_run_id) if parent_run_id else None,
                "agent_depth": inherited_depth,
            },
        ) as recorder:
            # Tag the run with the parent chain so cost rollup queries
            # (root_run_id) work for delegated trees. The runs repo
            # exposes this; AgentRunsRepository wraps the UPDATE.
            if parent_run_id is not None:
                await _attach_to_parent_run(
                    run_id=recorder.run_id,
                    parent_run_id=parent_run_id,
                    agent_depth=inherited_depth,
                )

            result = await stack.runner.run_turn(
                composed,
                user_messages=[{"role": "user", "content": user_query}],
                recorder=recorder,
            )
            run_id = recorder.run_id
            assistant_content = result.get("content") or ""
            recorder.set_summaries(output_summary=assistant_content)

    except AgentPausedError as err:
        logger.opt(exception=True).warning(f"[agent-worker] agent paused: {err}")
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="failed",
            error_code="agent_paused",
            error_message=str(err),
        )
        await _move_worker_back_to_idle(agent_id, task_id, trigger="task_completed")
        return {"task_id": str(task_id), "status": "failed", "run_id": None}
    except Exception as err:
        logger.exception(f"[agent-worker] turn failed for task {task_id}: {err}")
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="failed",
            error_code="runtime_error",
            error_message=str(err)[:500],
        )
        await _move_worker_back_to_idle(agent_id, task_id, trigger="task_completed")
        return {"task_id": str(task_id), "status": "failed", "run_id": None}

    # ── Persist task result + outbox delivery ───────────────────────
    await workforce.update_task_status(
        task_id=task_id,
        lifecycle_status="done",
        current_run_id=run_id,
        result={
            "content": assistant_content,
            "run_id": str(run_id),
            "depth": inherited_depth,
        },
    )

    # Deliver the response back to whoever sent the task. The inbox row
    # references the sender; we route the outbox accordingly.
    inbox_message_id_raw = task.get("inbox_message_id")
    inbox_msg = None
    if inbox_message_id_raw:
        inbox_msg = await _lookup_inbox_message(workforce, UUID(inbox_message_id_raw))

    sender_kind = (inbox_msg or {}).get("sender_kind") or "user"
    sender_user_id_raw = (inbox_msg or {}).get("sender_user_id")
    sender_agent_id_raw = (inbox_msg or {}).get("sender_agent_id")

    await workforce.enqueue_outbox(
        sender_agent_id=agent_id,
        recipient_kind="agent" if sender_kind == "agent" else "user",
        recipient_user_id=(UUID(sender_user_id_raw) if sender_user_id_raw else user_id),
        recipient_agent_id=(UUID(sender_agent_id_raw) if sender_agent_id_raw else None),
        message_type="task_result",
        payload={
            "task_id": str(task_id),
            "run_id": str(run_id),
            "content": assistant_content,
            "depth": inherited_depth,
        },
        task_id=task_id,
    )

    # Worker is done with this task — move state machine back to idle so
    # the inbox processor can dispatch the next message. Without this, the
    # agent stays in 'working' forever and ALLOWED_TRANSITIONS rejects the
    # next 'task_assigned'.
    await _move_worker_back_to_idle(agent_id, task_id, trigger="task_completed")

    return {"task_id": str(task_id), "status": "done", "run_id": str(run_id)}


async def _move_worker_back_to_idle(
    agent_id: UUID, task_id: UUID, *, trigger: str
) -> None:
    """Best-effort post-turn worker state transition.

    Fires the state machine so worker state goes back to 'idle' (on
    task_completed) or 'blocked' (on error). Without this, the agent
    stays in 'working' after the first task and ALLOWED_TRANSITIONS
    rejects the next ``task_assigned``.

    Failure is logged + swallowed — ``agent_tasks.lifecycle_status`` is
    the source of truth for whether the work succeeded; worker state
    row is just routing metadata.
    """
    from app.services.workforce.state_machine import (
        InvalidTransitionError,
        WorkerStateMachine,
    )

    try:
        await WorkerStateMachine().transition(
            agent_id=agent_id,
            trigger=trigger,
            task_id=task_id,
            current_task_id=None,
        )
    except InvalidTransitionError as err:
        # Worker is already in idle/terminated/etc — nothing to do.
        logger.debug(
            f"[agent-worker] no state move needed "
            f"(agent={agent_id}, trigger={trigger}): {err}"
        )
    except Exception as err:
        logger.exception(
            f"[agent-worker] post-turn transition failed "
            f"(agent={agent_id}, trigger={trigger}): {err}"
        )


async def _lookup_inbox_message(
    workforce: AgentWorkforceRepository, message_id: UUID
) -> Optional[dict[str, Any]]:
    """Read one inbox row to find the original sender. Best-effort —
    if the row is gone (cascade delete, RLS race), we fall back to the
    task's user_id as the user recipient."""
    try:
        from app.db.supabase_client import get_async_supabase_admin

        client = await get_async_supabase_admin()
        result = (
            await client.table(workforce.INBOX_TABLE)
            .select("sender_kind,sender_user_id,sender_agent_id")
            .eq("id", str(message_id))
            .maybe_single()
            .execute()
        )
        return result.data if result and result.data else None
    except Exception as err:
        logger.opt(exception=True).warning(f"[agent-worker] inbox lookup failed: {err}")
        return None


async def _attach_to_parent_run(
    *,
    run_id: UUID,
    parent_run_id: UUID,
    agent_depth: int,
) -> None:
    """Set ``agent_runs.parent_run_id`` and propagate ``root_run_id``.

    RunRecorder doesn't take these on construction (M2 added them as a
    schema-only change), so we patch them in via a follow-up UPDATE.
    The parent's ``root_run_id`` is read first; if NULL (parent is root),
    we use the parent's id as the root.
    """

    try:
        from app.db.supabase_client import get_async_supabase_admin

        client = await get_async_supabase_admin()
        # Read parent's root_run_id (or use parent_run_id as fallback if
        # parent is itself a root).
        parent_row = (
            await client.table("agent_runs")
            .select("root_run_id")
            .eq("id", str(parent_run_id))
            .maybe_single()
            .execute()
        )
        parent_data = parent_row.data if parent_row and parent_row.data else None
        root_run_id = (parent_data or {}).get("root_run_id") or str(parent_run_id)

        await client.table("agent_runs").update(
            {
                "parent_run_id": str(parent_run_id),
                "root_run_id": root_run_id,
                "agent_depth": agent_depth,
            }
        ).eq("id", str(run_id)).execute()
    except Exception as err:
        logger.warning(
            f"[agent-worker] failed to attach run {run_id} to parent "
            f"{parent_run_id}: {err}"
        )
