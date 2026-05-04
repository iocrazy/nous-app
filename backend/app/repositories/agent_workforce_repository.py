"""Repository for the M2 Persistent Workforce: workers / inbox / tasks /
state_history / outbox.

Five tables form one bounded context (the workforce lifecycle), so they
share a single repository. Sections below mirror the migration:

    1. workers             — runtime state per agent
    2. inbox               — incoming messages (user + system + cross-agent)
    3. tasks               — per-agent queue with lifecycle state machine
    4. state_history       — audit trail for state transitions
    5. outbox              — outgoing messages (Realtime delivery + audit)

Writes go through the admin client (RLS bypass — workforce dispatch is
service-role logic). Read paths can be called either by service code (admin)
or via user-scoped clients (RLS enforces ownership).

The state-machine logic lives ONE LEVEL UP in
``app.services.workforce.state_machine`` — this repo only exposes the raw
DB primitives. Advisory locks are held by the state machine, not here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin

# ─────────────────────────────────────────────────────────────────
# Type aliases for clarity at call-sites
# ─────────────────────────────────────────────────────────────────
# 'idle' | 'working' | 'waiting_for_other' | 'blocked' | 'paused' | 'terminated'
WorkerState = str
# 'queued' | 'assigned' | 'in_progress' | 'waiting_for_other' | 'blocked' |
# 'done' | 'failed' | 'cancelled'
TaskLifecycle = str
SenderKind = str  # 'user' | 'agent' | 'system' | 'schedule'
# 'task' | 'question' | 'notification' | 'approval_request' | 'cancel' | 'status_query'
MessageType = str
RecipientKind = str  # 'user' | 'agent' | 'broadcast'


class AgentWorkforceRepository:
    """Data access layer for the persistent workforce (M2)."""

    WORKERS_TABLE = "agent_workers"
    INBOX_TABLE = "agent_inbox"
    TASKS_TABLE = "agent_tasks"
    STATE_HISTORY_TABLE = "agent_state_history"
    OUTBOX_TABLE = "agent_outbox"

    async def _get_client(self):
        return await get_async_supabase_admin()

    # ═════════════════════════════════════════════════════════════
    # 1. Workers
    # ═════════════════════════════════════════════════════════════

    async def get_worker(self, agent_id: UUID) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.WORKERS_TABLE)
                .select("*")
                .eq("agent_id", str(agent_id))
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.exception(f"Failed to get worker {agent_id}: {e}")
            return None

    async def upsert_worker(
        self,
        *,
        agent_id: UUID,
        state: WorkerState = "idle",
        worker_pid: Optional[int] = None,
        worker_hostname: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Register or refresh a worker. Idempotent on agent_id (PK)."""
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "agent_id": str(agent_id),
            "state": state,
            "worker_pid": worker_pid,
            "worker_hostname": worker_hostname,
            "heartbeat_at": now_iso,
            "state_changed_at": now_iso,
        }
        try:
            client = await self._get_client()
            result = (
                await client.table(self.WORKERS_TABLE)
                .upsert(payload, on_conflict="agent_id")
                .execute()
            )
            return (result.data or [None])[0]
        except Exception as e:
            logger.exception(f"Failed to upsert worker {agent_id}: {e}")
            return None

    async def update_worker_state(
        self,
        *,
        agent_id: UUID,
        state: WorkerState,
        current_task_id: Optional[UUID] = None,
        bump_heartbeat: bool = True,
    ) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat()
        payload: Dict[str, Any] = {
            "state": state,
            "state_changed_at": now_iso,
            "current_task_id": str(current_task_id) if current_task_id else None,
        }
        if bump_heartbeat:
            payload["heartbeat_at"] = now_iso
        try:
            client = await self._get_client()
            result = (
                await client.table(self.WORKERS_TABLE)
                .update(payload)
                .eq("agent_id", str(agent_id))
                .execute()
            )
            return bool(result.data)
        except Exception as e:
            logger.exception(f"Failed to update worker state {agent_id}: {e}")
            return False

    async def heartbeat(self, agent_id: UUID) -> bool:
        """Lightweight heartbeat refresh — no state change."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.WORKERS_TABLE)
                .update({"heartbeat_at": datetime.now(timezone.utc).isoformat()})
                .eq("agent_id", str(agent_id))
                .execute()
            )
            return bool(result.data)
        except Exception as e:
            logger.exception(f"Failed heartbeat {agent_id}: {e}")
            return False

    async def list_stale_workers(
        self, *, stale_before: datetime
    ) -> List[Dict[str, Any]]:
        """Workers whose heartbeat is older than `stale_before` and still in
        an active state. Sweeper marks them terminated and re-queues their
        in-flight task."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.WORKERS_TABLE)
                .select("*")
                .in_("state", ["idle", "working", "waiting_for_other"])
                .lt("heartbeat_at", stale_before.isoformat())
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.exception(f"Failed to list stale workers: {e}")
            return []

    # ═════════════════════════════════════════════════════════════
    # 2. Inbox
    # ═════════════════════════════════════════════════════════════

    async def enqueue_inbox(
        self,
        *,
        recipient_agent_id: UUID,
        sender_kind: SenderKind,
        message_type: MessageType,
        payload: Dict[str, Any],
        sender_user_id: Optional[UUID] = None,
        sender_agent_id: Optional[UUID] = None,
        priority: int = 5,
        dedup_key: Optional[str] = None,
        reply_to_message_id: Optional[UUID] = None,
        expires_at: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Insert a message into an agent's inbox.

        Returns the inserted row (or the existing row when a unique-violation
        on dedup_key swallows the insert). On unique-violation we look up the
        existing row so callers can still reply to the original message id.
        """
        record: Dict[str, Any] = {
            "recipient_agent_id": str(recipient_agent_id),
            "sender_kind": sender_kind,
            "sender_user_id": str(sender_user_id) if sender_user_id else None,
            "sender_agent_id": str(sender_agent_id) if sender_agent_id else None,
            "message_type": message_type,
            "payload": payload,
            "priority": priority,
            "dedup_key": dedup_key,
            "reply_to_message_id": (
                str(reply_to_message_id) if reply_to_message_id else None
            ),
            "expires_at": expires_at.isoformat() if expires_at else None,
        }
        try:
            client = await self._get_client()
            result = await client.table(self.INBOX_TABLE).insert(record).execute()
            return (result.data or [None])[0]
        except Exception as e:
            # Dedup collision → resolve to the existing row instead of failing.
            if dedup_key and "duplicate" in str(e).lower():
                logger.info(
                    f"Inbox dedup hit (recipient={recipient_agent_id}, key={dedup_key})"
                )
                return await self._find_inbox_by_dedup(
                    recipient_agent_id=recipient_agent_id, dedup_key=dedup_key
                )
            logger.exception(f"Failed to enqueue inbox for {recipient_agent_id}: {e}")
            return None

    async def _find_inbox_by_dedup(
        self, *, recipient_agent_id: UUID, dedup_key: str
    ) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.INBOX_TABLE)
                .select("*")
                .eq("recipient_agent_id", str(recipient_agent_id))
                .eq("dedup_key", dedup_key)
                .in_("status", ["unread", "reading"])
                .limit(1)
                .execute()
            )
            return (result.data or [None])[0]
        except Exception as e:
            logger.exception(f"Failed inbox dedup lookup: {e}")
            return None

    async def claim_next_unread(
        self,
        *,
        recipient_agent_id: UUID,
        claimed_by: str,
    ) -> Optional[Dict[str, Any]]:
        """Atomic claim: pick the highest-priority unread message and flip
        status to 'reading' in one round-trip.

        The state machine layer holds the per-agent advisory lock around this
        call so the read-then-update window is safe. Returns None when the
        inbox is empty.
        """
        try:
            client = await self._get_client()
            # Highest priority, oldest first
            picked = (
                await client.table(self.INBOX_TABLE)
                .select("id")
                .eq("recipient_agent_id", str(recipient_agent_id))
                .eq("status", "unread")
                .order("priority", desc=True)
                .order("created_at", desc=False)
                .limit(1)
                .execute()
            )
            if not picked.data:
                return None
            message_id = picked.data[0]["id"]

            updated = (
                await client.table(self.INBOX_TABLE)
                .update(
                    {
                        "status": "reading",
                        "reading_claimed_at": datetime.now(timezone.utc).isoformat(),
                        "reading_claimed_by": claimed_by,
                    }
                )
                .eq("id", message_id)
                .eq("status", "unread")  # CAS guard against double-claim
                .execute()
            )
            return (updated.data or [None])[0]
        except Exception as e:
            logger.exception(f"Failed to claim unread (agent={recipient_agent_id}): {e}")
            return None

    async def mark_inbox_processed(
        self,
        *,
        message_id: UUID,
        task_id: Optional[UUID] = None,
        status: str = "processed",
    ) -> bool:
        """Finalise an inbox message: 'processed' / 'dismissed' / 'expired'."""
        payload: Dict[str, Any] = {
            "status": status,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        if task_id is not None:
            payload["task_id"] = str(task_id)
        try:
            client = await self._get_client()
            result = (
                await client.table(self.INBOX_TABLE)
                .update(payload)
                .eq("id", str(message_id))
                .execute()
            )
            return bool(result.data)
        except Exception as e:
            logger.exception(f"Failed to mark inbox {message_id} {status}: {e}")
            return False

    async def list_inbox(
        self,
        *,
        recipient_agent_id: UUID,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            base = (
                client.table(self.INBOX_TABLE)
                .select("*", count="exact")
                .eq("recipient_agent_id", str(recipient_agent_id))
            )
            if status:
                base = base.eq("status", status)
            result = (
                await base.order("created_at", desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )
            return {"items": result.data or [], "total": result.count or 0}
        except Exception as e:
            logger.exception(f"Failed to list inbox {recipient_agent_id}: {e}")
            return {"items": [], "total": 0}

    # ═════════════════════════════════════════════════════════════
    # 3. Tasks
    # ═════════════════════════════════════════════════════════════

    async def create_task(
        self,
        *,
        agent_id: UUID,
        user_id: UUID,
        payload: Dict[str, Any],
        title: Optional[str] = None,
        parent_task_id: Optional[UUID] = None,
        root_task_id: Optional[UUID] = None,
        inbox_message_id: Optional[UUID] = None,
    ) -> Optional[Dict[str, Any]]:
        record: Dict[str, Any] = {
            "agent_id": str(agent_id),
            "user_id": str(user_id),
            "title": title,
            "payload": payload,
            "parent_task_id": str(parent_task_id) if parent_task_id else None,
            "root_task_id": str(root_task_id) if root_task_id else None,
            "inbox_message_id": str(inbox_message_id) if inbox_message_id else None,
        }
        try:
            client = await self._get_client()
            result = await client.table(self.TASKS_TABLE).insert(record).execute()
            row = (result.data or [None])[0]
            # Self-reference root_task_id when it's the tree root.
            if row and not row.get("root_task_id"):
                await client.table(self.TASKS_TABLE).update(
                    {"root_task_id": row["id"]}
                ).eq("id", row["id"]).execute()
                row["root_task_id"] = row["id"]
            return row
        except Exception as e:
            logger.exception(f"Failed to create task (agent={agent_id}): {e}")
            return None

    async def claim_next_queued(self, *, agent_id: UUID) -> Optional[Dict[str, Any]]:
        """Atomic claim: pick the oldest queued task for this agent and flip
        to 'assigned'. Caller (state machine) holds the advisory lock so the
        read-then-update gap is safe."""
        try:
            client = await self._get_client()
            picked = (
                await client.table(self.TASKS_TABLE)
                .select("id")
                .eq("agent_id", str(agent_id))
                .eq("lifecycle_status", "queued")
                .order("created_at", desc=False)
                .limit(1)
                .execute()
            )
            if not picked.data:
                return None
            task_id = picked.data[0]["id"]
            updated = (
                await client.table(self.TASKS_TABLE)
                .update(
                    {
                        "lifecycle_status": "assigned",
                        "assigned_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                .eq("id", task_id)
                .eq("lifecycle_status", "queued")  # CAS guard
                .execute()
            )
            return (updated.data or [None])[0]
        except Exception as e:
            logger.exception(f"Failed to claim queued task (agent={agent_id}): {e}")
            return None

    async def update_task_status(
        self,
        *,
        task_id: UUID,
        lifecycle_status: TaskLifecycle,
        current_run_id: Optional[UUID] = None,
        result: Optional[Dict[str, Any]] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Generic status update. The state machine validates legal transitions;
        this just persists the result.

        For terminal statuses ('done' | 'failed' | 'cancelled') sets ended_at;
        for 'in_progress' sets started_at if NULL; trigger handles updated_at.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        payload: Dict[str, Any] = {"lifecycle_status": lifecycle_status}
        if current_run_id is not None:
            payload["current_run_id"] = str(current_run_id)
        if result is not None:
            payload["result"] = result
        if error_code is not None:
            payload["error_code"] = error_code
        if error_message is not None:
            payload["error_message"] = error_message
        if lifecycle_status == "in_progress":
            payload["started_at"] = now_iso
        if lifecycle_status in ("done", "failed", "cancelled"):
            payload["ended_at"] = now_iso

        try:
            client = await self._get_client()
            result_resp = (
                await client.table(self.TASKS_TABLE)
                .update(payload)
                .eq("id", str(task_id))
                .execute()
            )
            return bool(result_resp.data)
        except Exception as e:
            logger.exception(f"Failed to update task {task_id} → {lifecycle_status}: {e}")
            return False

    async def get_task(self, task_id: UUID) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TASKS_TABLE)
                .select("*")
                .eq("id", str(task_id))
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.exception(f"Failed to get task {task_id}: {e}")
            return None

    async def list_tasks(
        self,
        *,
        agent_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        statuses: Optional[List[TaskLifecycle]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        try:
            client = await self._get_client()
            base = client.table(self.TASKS_TABLE).select("*", count="exact")
            if agent_id:
                base = base.eq("agent_id", str(agent_id))
            if user_id:
                base = base.eq("user_id", str(user_id))
            if statuses:
                base = base.in_("lifecycle_status", statuses)
            result = (
                await base.order("created_at", desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )
            return {"items": result.data or [], "total": result.count or 0}
        except Exception as e:
            logger.exception(f"Failed to list tasks: {e}")
            return {"items": [], "total": 0}

    async def requeue_task(self, task_id: UUID) -> bool:
        """Sweeper helper: when a worker is reaped mid-flight, send its task
        back to the queue. Wipes assigned_at / started_at so it looks fresh."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TASKS_TABLE)
                .update(
                    {
                        "lifecycle_status": "queued",
                        "assigned_at": None,
                        "started_at": None,
                        "current_run_id": None,
                    }
                )
                .eq("id", str(task_id))
                .in_("lifecycle_status", ["assigned", "in_progress"])
                .execute()
            )
            return bool(result.data)
        except Exception as e:
            logger.exception(f"Failed to requeue task {task_id}: {e}")
            return False

    # ═════════════════════════════════════════════════════════════
    # 4. State history
    # ═════════════════════════════════════════════════════════════

    async def log_state_transition(
        self,
        *,
        agent_id: UUID,
        from_state: Optional[WorkerState],
        to_state: WorkerState,
        trigger: str,
        task_id: Optional[UUID] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        record = {
            "agent_id": str(agent_id),
            "from_state": from_state,
            "to_state": to_state,
            "trigger": trigger,
            "task_id": str(task_id) if task_id else None,
            "metadata_json": metadata or {},
        }
        try:
            client = await self._get_client()
            result = (
                await client.table(self.STATE_HISTORY_TABLE).insert(record).execute()
            )
            return bool(result.data)
        except Exception as e:
            logger.exception(f"Failed to log state transition for {agent_id}: {e}")
            return False

    async def list_state_history(
        self,
        *,
        agent_id: UUID,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.STATE_HISTORY_TABLE)
                .select("*")
                .eq("agent_id", str(agent_id))
                .order("changed_at", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.exception(f"Failed to list state history {agent_id}: {e}")
            return []

    # ═════════════════════════════════════════════════════════════
    # 5. Outbox
    # ═════════════════════════════════════════════════════════════

    async def enqueue_outbox(
        self,
        *,
        sender_agent_id: UUID,
        recipient_kind: RecipientKind,
        message_type: str,
        payload: Dict[str, Any],
        recipient_user_id: Optional[UUID] = None,
        recipient_agent_id: Optional[UUID] = None,
        task_id: Optional[UUID] = None,
    ) -> Optional[Dict[str, Any]]:
        record = {
            "sender_agent_id": str(sender_agent_id),
            "recipient_kind": recipient_kind,
            "recipient_user_id": str(recipient_user_id) if recipient_user_id else None,
            "recipient_agent_id": (
                str(recipient_agent_id) if recipient_agent_id else None
            ),
            "message_type": message_type,
            "payload": payload,
            "task_id": str(task_id) if task_id else None,
        }
        try:
            client = await self._get_client()
            result = await client.table(self.OUTBOX_TABLE).insert(record).execute()
            return (result.data or [None])[0]
        except Exception as e:
            logger.exception(f"Failed to enqueue outbox from {sender_agent_id}: {e}")
            return None

    async def mark_outbox_delivered(self, message_id: UUID) -> bool:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.OUTBOX_TABLE)
                .update(
                    {
                        "delivered": True,
                        "delivered_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                .eq("id", str(message_id))
                .execute()
            )
            return bool(result.data)
        except Exception as e:
            logger.exception(f"Failed to mark outbox {message_id} delivered: {e}")
            return False

    async def list_undelivered_outbox(
        self, *, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Outbox dispatcher reads this every tick to push messages over
        Realtime to recipient_user_id channels and into recipient agent inboxes."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.OUTBOX_TABLE)
                .select("*")
                .eq("delivered", False)
                .order("created_at", desc=False)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.exception(f"Failed to list undelivered outbox: {e}")
            return []
