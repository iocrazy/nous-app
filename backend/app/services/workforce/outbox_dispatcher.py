"""Outbox dispatcher — drains undelivered agent_outbox rows.

Two delivery channels:
  - recipient_kind='user'   → Supabase Realtime via INSERT into a public
                              channel that the frontend subscribes to.
                              Realtime fan-out is implicit: the row itself
                              IS the broadcast (subscribed clients see the
                              insert event). All we do is mark delivered.
  - recipient_kind='agent'  → re-enqueue into the recipient agent's inbox
                              with sender_kind='agent'. Cross-agent
                              dispatch (M2 Day 3) writes outbox rows; this
                              loop closes the round-trip.

  - recipient_kind='broadcast' → both. (Currently unused — reserved for
                              system-wide announcements.)

Per plan-eng-review the dispatcher runs as one Celery task per beat tick;
a top-level advisory lock keeps multiple beat workers from double-firing.
That lock-and-tick pattern is centralised in tasks/agent_workforce_tasks.py
(M2 Day 3-4); this module exposes the pure async function so unit tests
can drive it directly.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from loguru import logger

from app.repositories.agent_workforce_repository import AgentWorkforceRepository


class OutboxDispatcher:
    """One pass over agent_outbox.delivered=false."""

    def __init__(
        self,
        repo: Optional[AgentWorkforceRepository] = None,
    ) -> None:
        self.repo = repo or AgentWorkforceRepository()

    async def tick(self, *, batch_size: int = 100) -> Dict[str, int]:
        """One drain pass. Returns aggregate counters."""
        rows = await self.repo.list_undelivered_outbox(limit=batch_size)
        stats = {"delivered_user": 0, "delivered_agent": 0, "errors": 0}
        for row in rows:
            try:
                if row["recipient_kind"] == "user":
                    if await self._dispatch_to_user(row):
                        stats["delivered_user"] += 1
                elif row["recipient_kind"] == "agent":
                    if await self._dispatch_to_agent(row):
                        stats["delivered_agent"] += 1
                elif row["recipient_kind"] == "broadcast":
                    user_ok = await self._dispatch_to_user(row)
                    agent_ok = await self._dispatch_to_agent(row)
                    if user_ok:
                        stats["delivered_user"] += 1
                    if agent_ok:
                        stats["delivered_agent"] += 1
                else:
                    logger.warning(
                        f"[outbox] unknown recipient_kind={row['recipient_kind']}"
                    )
                    stats["errors"] += 1
                    continue
            except Exception as err:
                stats["errors"] += 1
                logger.exception(f"[outbox] dispatch failed for row {row['id']}: {err}")
        return stats

    # ────────────────────────────────────────────────────────────
    # Channel-specific delivery
    # ────────────────────────────────────────────────────────────

    async def _dispatch_to_user(self, row: Dict[str, Any]) -> bool:
        """User delivery is a no-op write at this layer: Supabase Realtime
        already fires on the outbox INSERT, and the frontend subscribes
        with ``recipient_user_id eq.<uid>`` on agent_outbox. So all we do
        is flip ``delivered=true`` so the row is excluded from the next tick.
        """
        if not row.get("recipient_user_id"):
            return False
        return await self.repo.mark_outbox_delivered(UUID(row["id"]))

    async def _dispatch_to_agent(self, row: Dict[str, Any]) -> bool:
        """Agent delivery: write the message into the recipient agent's
        inbox. Idempotent via dedup_key (the outbox.id) — replaying a
        stuck dispatch tick won't double-deliver."""
        recipient = row.get("recipient_agent_id")
        if not recipient:
            return False
        sender = row["sender_agent_id"]

        # Only mark delivered when the inbox insert actually landed. If
        # enqueue_inbox returns None (transient DB error, RLS misfire,
        # network blip), leave delivered=false so the next tick retries —
        # otherwise the recipient silently loses the message.
        inbox_row = await self.repo.enqueue_inbox(
            recipient_agent_id=UUID(recipient),
            sender_kind="agent",
            sender_agent_id=UUID(sender),
            message_type=self._map_message_type(row["message_type"]),
            payload=row.get("payload") or {},
            dedup_key=f"outbox:{row['id']}",
            reply_to_message_id=None,
        )
        if not inbox_row:
            return False
        return await self.repo.mark_outbox_delivered(UUID(row["id"]))

    @staticmethod
    def _map_message_type(outbox_type: str) -> str:
        """Project the freer-form outbox type into the inbox CHECK set."""
        valid = {
            "task",
            "question",
            "notification",
            "approval_request",
            "cancel",
            "status_query",
        }
        if outbox_type in valid:
            return outbox_type
        return "notification"
