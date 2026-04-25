"""Inbox processor — turns unread agent_inbox rows into agent_tasks.

Runs once per beat tick. Per agent with unread messages:

    1. State machine transition idle -> working (advisory lock per agent)
    2. Claim the highest-priority unread message (CAS guard)
    3. Create an agent_task from the message payload
    4. Mark the inbox message processed and link to the task

The actual LLM work (the running of that task) happens in a separate
Celery task dispatched after the row is created — the processor is
queue-shaping only, not execution. This separation matches paperclip's
"inbox-then-task" pattern: the inbox is the durable mailbox, the task
queue is the unit of execution.

Failure handling: any exception inside one agent's processing is logged
and skipped — other agents in the tick continue. The state machine
guards against partial state via the advisory lock.
"""

from __future__ import annotations

import socket
from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin
from app.repositories.agent_workforce_repository import AgentWorkforceRepository
from app.services.workforce.state_machine import (
    InvalidTransitionError,
    LockNotAcquiredError,
    WorkerStateMachine,
)


def _claimed_by_tag() -> str:
    """Tag identifying THIS process — written into reading_claimed_by so we
    can attribute mid-flight messages back to the worker that grabbed them."""
    import os

    return f"{socket.gethostname()}:{os.getpid()}"


class InboxProcessor:
    """Per-tick coordinator that drains unread inbox rows into the task queue."""

    def __init__(
        self,
        repo: Optional[AgentWorkforceRepository] = None,
        state_machine: Optional[WorkerStateMachine] = None,
    ) -> None:
        self.repo = repo or AgentWorkforceRepository()
        self.state_machine = state_machine or WorkerStateMachine(repo=self.repo)

    # ────────────────────────────────────────────────────────────
    # Tick entry point
    # ────────────────────────────────────────────────────────────

    async def tick(self) -> Dict[str, int]:
        """One pass over agents with pending inbox messages.

        Returns aggregate counters for telemetry. Errors per agent are
        swallowed so one bad agent doesn't poison the entire tick.
        """
        agents_with_unread = await self._agents_with_unread_messages()
        stats = {"agents_processed": 0, "tasks_created": 0, "errors": 0}

        for agent_id in agents_with_unread:
            try:
                created = await self._process_one_agent(agent_id)
                if created:
                    stats["tasks_created"] += 1
                stats["agents_processed"] += 1
            except LockNotAcquiredError:
                # Lock held by another worker — fine, we'll retry next tick.
                logger.debug(f"[inbox] agent={agent_id} lock contended, skipping tick")
            except Exception as err:
                stats["errors"] += 1
                logger.exception(f"[inbox] agent={agent_id} processing failed: {err}")

        return stats

    # ────────────────────────────────────────────────────────────
    # Per-agent flow
    # ────────────────────────────────────────────────────────────

    async def _process_one_agent(self, agent_id: UUID) -> bool:
        """Returns True iff a task row was created this turn."""
        message = await self.repo.claim_next_unread(
            recipient_agent_id=agent_id, claimed_by=_claimed_by_tag()
        )
        if not message:
            return False

        # Some messages are control-plane and don't spawn tasks (cancel,
        # status_query). Route them to dedicated handlers and finish.
        message_type = message.get("message_type")
        if message_type == "cancel":
            return await self._handle_cancel(agent_id, message)
        if message_type == "status_query":
            return await self._handle_status_query(agent_id, message)

        # Task-producing messages: 'task' | 'question' | 'approval_request'
        # | 'notification'. The current contract treats notification as
        # task-equivalent — agent decides what to do with it.
        return await self._spawn_task_from_message(agent_id, message)

    async def _spawn_task_from_message(
        self, agent_id: UUID, message: Dict[str, Any]
    ) -> bool:
        """Create an agent_task, link it to the inbox row, fire state move."""
        sender_user_id = message.get("sender_user_id")
        if not sender_user_id:
            # No user attribution → treat as system task. Use the agent's
            # owner as the user_id so RLS still has something to enforce.
            sender_user_id = await self._lookup_agent_owner(agent_id)
        if not sender_user_id:
            logger.warning(
                f"[inbox] agent={agent_id} message={message['id']} "
                f"has no resolvable user_id — dropping"
            )
            await self.repo.mark_inbox_processed(
                message_id=UUID(message["id"]),
                status="dismissed",
            )
            return False

        task = await self.repo.create_task(
            agent_id=agent_id,
            user_id=UUID(sender_user_id),
            payload=message.get("payload") or {},
            title=(message.get("payload") or {}).get("title"),
            inbox_message_id=UUID(message["id"]),
        )
        if not task:
            return False

        # Drive the worker into 'working' BEFORE marking the inbox processed.
        # If the state machine raises (lock contention, invalid transition),
        # we don't want a "processed" inbox message paired with an idle worker
        # and an orphan queued task. The CAS guard in claim_next_unread set
        # the message to 'reading' so concurrent ticks won't re-pick it.
        try:
            await self.state_machine.transition(
                agent_id=agent_id,
                trigger="task_assigned",
                task_id=UUID(task["id"]),
                current_task_id=UUID(task["id"]),
            )
        except InvalidTransitionError:
            logger.warning(
                f"[inbox] agent={agent_id} couldn't accept task "
                f"(current state forbids task_assigned); marking blocked"
            )
            try:
                await self.state_machine.transition(
                    agent_id=agent_id,
                    trigger="error",
                    task_id=UUID(task["id"]),
                    metadata={"reason": "rejected_task_assigned"},
                )
            except Exception:  # pragma: no cover — defensive fallthrough
                pass
        except LockNotAcquiredError:
            # Another worker beat us to the lock for this agent. Re-queue
            # the task (back to 'queued') and revert the inbox claim so the
            # next tick (or other worker) retries cleanly.
            logger.debug(
                f"[inbox] agent={agent_id} lock contended after task "
                f"create — requeuing and reverting inbox"
            )
            await self.repo.requeue_task(UUID(task["id"]))
            # Note: revert-to-unread isn't exposed on the repo yet; the
            # message stays in 'reading' until the next tick reclaims it
            # via the orphan-reading sweeper (M2 follow-up TODO).
            return False

        # State move succeeded — now safe to finalise the inbox row.
        await self.repo.mark_inbox_processed(
            message_id=UUID(message["id"]),
            task_id=UUID(task["id"]),
            status="processed",
        )
        return True

    async def _handle_cancel(self, agent_id: UUID, message: Dict[str, Any]) -> bool:
        target_task_id = (message.get("payload") or {}).get("task_id")
        if target_task_id:
            await self.repo.update_task_status(
                task_id=UUID(target_task_id),
                lifecycle_status="cancelled",
                error_code="user_cancel",
                error_message="Cancelled via inbox message",
            )
        await self.repo.mark_inbox_processed(
            message_id=UUID(message["id"]), status="processed"
        )
        return False

    async def _handle_status_query(
        self, agent_id: UUID, message: Dict[str, Any]
    ) -> bool:
        # Push a status snapshot via outbox; the dispatcher delivers it.
        worker = await self.repo.get_worker(agent_id)
        sender_user_id = message.get("sender_user_id")
        if sender_user_id:
            await self.repo.enqueue_outbox(
                sender_agent_id=agent_id,
                recipient_kind="user",
                recipient_user_id=UUID(sender_user_id),
                message_type="status_response",
                payload={
                    "state": (worker or {}).get("state"),
                    "current_task_id": (worker or {}).get("current_task_id"),
                    "in_reply_to": message["id"],
                },
            )
        await self.repo.mark_inbox_processed(
            message_id=UUID(message["id"]), status="processed"
        )
        return False

    # ────────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────────

    async def _agents_with_unread_messages(self) -> List[UUID]:
        """Distinct recipient_agent_id for inbox rows in 'unread' status.

        The inbox table doesn't have a materialised "agents with work"
        view, so we read the recipient column for unread rows and dedupe
        in Python. At expected volumes (low hundreds of agents at most)
        this is fine; if the inbox grows large, swap in a SQL DISTINCT.
        """
        try:
            client = await get_async_supabase_admin()
            result = (
                await client.table("agent_inbox")
                .select("recipient_agent_id")
                .eq("status", "unread")
                .limit(500)
                .execute()
            )
            seen: List[UUID] = []
            seen_ids = set()
            for row in result.data or []:
                rid = row.get("recipient_agent_id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    seen.append(UUID(rid))
            return seen
        except Exception as err:
            logger.error(f"[inbox] failed to load agents-with-unread: {err}")
            return []

    async def _lookup_agent_owner(self, agent_id: UUID) -> Optional[str]:
        try:
            client = await get_async_supabase_admin()
            result = (
                await client.table("ai_agents")
                .select("user_id")
                .eq("id", str(agent_id))
                .maybe_single()
                .execute()
            )
            if result and result.data:
                return result.data.get("user_id")
            return None
        except Exception as err:
            logger.warning(f"[inbox] agent-owner lookup failed: {err}")
            return None
