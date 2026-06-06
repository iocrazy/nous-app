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

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union
from uuid import UUID

from loguru import logger

if TYPE_CHECKING:
    from app.repositories.agent_workforce_repository_orm import (
        AgentWorkforceRepositoryOrm,
    )

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


# ─────────────────────────────────────────────────────────────────
# A4 (migration 200): agent_tasks 合并到 task_tracking
#
# 上层调用方继续看到 agent_tasks 风格的字典（id/lifecycle_status/payload/...），
# repository 层在 task_tracking 的 8 列上做 shape 转换。
#
# task_tracking 的 status 列是 5 状态 (pending/processing/completed/failed/
# cancelled)；agent_task 的 lifecycle 8 状态精度通过 phase 列保留：
# ─────────────────────────────────────────────────────────────────
TASK_KIND_AGENT = "agent_task"
TASK_TYPE_AGENT = "agent_task"

LIFECYCLE_TO_STATUS: Dict[str, str] = {
    "queued": "pending",
    "assigned": "pending",
    "in_progress": "processing",
    "waiting_for_other": "processing",
    "blocked": "processing",
    "done": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}


# task_tracking PK 没有 default —— 必须应用层显式提供（migration 180 swap PK
# 后 dbos_workflow_id 没 gen_random_uuid default）。
def _new_task_id() -> str:
    return str(uuid.uuid4())


def tt_row_to_task_shape(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """task_tracking 行 → agent_tasks 风格字典（让上层无感切表）。

    公开 API：API router / delegate_tool / dashboard 等绕过 repository 直接
    查 task_tracking 时也复用这个 shape mapper。
    """
    if row is None:
        return None
    md = row.get("metadata") or {}
    return {
        "id": row.get("dbos_workflow_id"),
        "agent_id": row.get("agent_id"),
        "user_id": row.get("user_id"),
        "title": row.get("title"),
        "payload": md.get("agent_payload") or {},
        "lifecycle_status": row.get("phase") or "queued",
        "current_run_id": md.get("current_run_id"),
        "result": md.get("agent_result"),
        "error_code": md.get("error_code") or row.get("error_code"),
        "error_message": row.get("error_msg"),
        "parent_task_id": row.get("parent_task_id"),
        "root_task_id": row.get("root_task_id"),
        "inbox_message_id": row.get("inbox_message_id"),
        "created_at": row.get("created_at"),
        "assigned_at": md.get("assigned_at"),
        "started_at": row.get("started_at"),
        "ended_at": row.get("completed_at"),
        "updated_at": row.get("updated_at"),
    }


# Backwards-compat alias for older internal call sites.
_tt_row_to_task_shape = tt_row_to_task_shape


class AgentWorkforceRepository:
    """Data access layer for the persistent workforce (M2)."""

    WORKERS_TABLE = "agent_workers"
    INBOX_TABLE = "agent_inbox"
    # A4: agent_tasks 合并入 task_tracking (task_kind='agent_task')
    TASKS_TABLE = "task_tracking"
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
            logger.error(f"Failed to get worker {agent_id}: {e}")
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
            logger.error(f"Failed to upsert worker {agent_id}: {e}")
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
            logger.error(f"Failed to update worker state {agent_id}: {e}")
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
            logger.error(f"Failed heartbeat {agent_id}: {e}")
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
            logger.error(f"Failed to list stale workers: {e}")
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
            logger.error(f"Failed to enqueue inbox for {recipient_agent_id}: {e}")
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
            logger.error(f"Failed inbox dedup lookup: {e}")
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
            logger.error(f"Failed to claim unread (agent={recipient_agent_id}): {e}")
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
            logger.error(f"Failed to mark inbox {message_id} {status}: {e}")
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
            logger.error(f"Failed to list inbox {recipient_agent_id}: {e}")
            return {"items": [], "total": 0}

    # ═════════════════════════════════════════════════════════════
    # 3. Tasks  (A4: 物理表 task_tracking WHERE task_kind='agent_task')
    # ═════════════════════════════════════════════════════════════
    #
    # 命名映射（agent_tasks → task_tracking）：
    #   id              → dbos_workflow_id (TEXT, 应用层 uuid4 生成)
    #   lifecycle_status → phase (8 状态原值) + status (5 状态映射)
    #   payload         → metadata.agent_payload
    #   result          → metadata.agent_result
    #   current_run_id  → metadata.current_run_id
    #   error_code      → metadata.error_code (兜底列)
    #   error_message   → error_msg
    #   ended_at        → completed_at
    #   assigned_at     → metadata.assigned_at
    #
    # task_tracking.task_type 是业务标签 (VARCHAR 20)；agent task 行用
    # 'agent_task' 标。task_kind 列用作"哪个事实源管 status"的 dispatch。

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
        new_id = _new_task_id()
        # 如果上层没传 root，自身就是树根（取代以前 INSERT 后再 UPDATE 的 round-trip）。
        root_id_str = (
            str(root_task_id)
            if root_task_id
            else (str(parent_task_id) if parent_task_id else new_id)
        )
        # task_tracking PK 是 TEXT，parent/root 列也是 TEXT（A4 加的列）。
        record: Dict[str, Any] = {
            "dbos_workflow_id": new_id,
            "task_kind": TASK_KIND_AGENT,
            "task_type": TASK_TYPE_AGENT,
            "agent_id": str(agent_id),
            "user_id": str(user_id),
            "title": (title or "Agent task").strip() or "Agent task",
            "status": "pending",
            "phase": "queued",
            "progress": 0,
            "metadata": {
                "agent_payload": payload,
            },
            "parent_task_id": str(parent_task_id) if parent_task_id else None,
            "root_task_id": root_id_str,
            "inbox_message_id": str(inbox_message_id) if inbox_message_id else None,
        }
        try:
            client = await self._get_client()
            result = await client.table(self.TASKS_TABLE).insert(record).execute()
            row = (result.data or [None])[0]
            return _tt_row_to_task_shape(row)
        except Exception as e:
            logger.exception(f"Failed to create task (agent={agent_id}): {e}")
            return None

    async def claim_next_queued(self, *, agent_id: UUID) -> Optional[Dict[str, Any]]:
        """Atomic claim: pick the oldest queued task for this agent and flip
        to 'assigned'. Caller (state machine) holds the advisory lock so the
        read-then-update gap is safe."""
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            client = await self._get_client()
            picked = (
                await client.table(self.TASKS_TABLE)
                .select("dbos_workflow_id, metadata")
                .eq("task_kind", TASK_KIND_AGENT)
                .eq("agent_id", str(agent_id))
                .eq("phase", "queued")
                .order("created_at", desc=False)
                .limit(1)
                .execute()
            )
            if not picked.data:
                return None
            task_id = picked.data[0]["dbos_workflow_id"]
            existing_md = picked.data[0].get("metadata") or {}
            existing_md["assigned_at"] = now_iso
            updated = (
                await client.table(self.TASKS_TABLE)
                .update(
                    {
                        "phase": "assigned",
                        "status": LIFECYCLE_TO_STATUS["assigned"],
                        "metadata": existing_md,
                    }
                )
                .eq("dbos_workflow_id", task_id)
                .eq("phase", "queued")  # CAS guard
                .execute()
            )
            return _tt_row_to_task_shape((updated.data or [None])[0])
        except Exception as e:
            logger.exception(f"Failed to claim queued task (agent={agent_id}): {e}")
            return None

    async def update_task_status(
        self,
        *,
        task_id: UUID,
        lifecycle_status: TaskLifecycle,
        # agent_runs.id is BIGINT Snowflake (mig 232) → numeric string.
        current_run_id: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """Generic status update. The state machine validates legal transitions;
        this just persists the result.

        For terminal statuses ('done' | 'failed' | 'cancelled') sets completed_at;
        for 'in_progress' sets started_at; metadata is read-modify-write because
        Supabase JS client doesn't support jsonb merge — we read existing
        metadata, splice in the new fields, write back."""
        now_iso = datetime.now(timezone.utc).isoformat()
        task_id_str = str(task_id)
        client = await self._get_client()

        # Read-modify-write metadata to avoid blowing away unrelated keys.
        try:
            existing = (
                await client.table(self.TASKS_TABLE)
                .select("metadata")
                .eq("task_kind", TASK_KIND_AGENT)
                .eq("dbos_workflow_id", task_id_str)
                .maybe_single()
                .execute()
            )
            md: Dict[str, Any] = (existing.data or {}).get("metadata") or {}
        except Exception as e:
            logger.exception(f"Failed to read metadata for {task_id_str}: {e}")
            md = {}

        if current_run_id is not None:
            md["current_run_id"] = str(current_run_id)
        if result is not None:
            md["agent_result"] = result
        if error_code is not None:
            md["error_code"] = error_code
        if lifecycle_status == "assigned":
            md["assigned_at"] = now_iso

        payload: Dict[str, Any] = {
            "phase": lifecycle_status,
            "status": LIFECYCLE_TO_STATUS.get(lifecycle_status, "pending"),
            "metadata": md,
        }
        if error_message is not None:
            payload["error_msg"] = error_message
        if lifecycle_status == "in_progress":
            payload["started_at"] = now_iso
        if lifecycle_status in ("done", "failed", "cancelled"):
            payload["completed_at"] = now_iso

        try:
            result_resp = (
                await client.table(self.TASKS_TABLE)
                .update(payload)
                .eq("task_kind", TASK_KIND_AGENT)
                .eq("dbos_workflow_id", task_id_str)
                .execute()
            )
            return bool(result_resp.data)
        except Exception as e:
            logger.exception(
                f"Failed to update task {task_id_str} → {lifecycle_status}: {e}"
            )
            return False

    async def get_task(self, task_id: UUID) -> Optional[Dict[str, Any]]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TASKS_TABLE)
                .select("*")
                .eq("task_kind", TASK_KIND_AGENT)
                .eq("dbos_workflow_id", str(task_id))
                .maybe_single()
                .execute()
            )
            row = result.data if result and result.data else None
            return _tt_row_to_task_shape(row)
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
            base = (
                client.table(self.TASKS_TABLE)
                .select("*", count="exact")
                .eq("task_kind", TASK_KIND_AGENT)
            )
            if agent_id:
                base = base.eq("agent_id", str(agent_id))
            if user_id:
                base = base.eq("user_id", str(user_id))
            if statuses:
                # Filter on phase column (preserves 8-state lifecycle precision).
                base = base.in_("phase", statuses)
            result = (
                await base.order("created_at", desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )
            items = [_tt_row_to_task_shape(r) for r in (result.data or [])]
            return {"items": items, "total": result.count or 0}
        except Exception as e:
            logger.exception(f"Failed to list tasks: {e}")
            return {"items": [], "total": 0}

    async def requeue_task(self, task_id: UUID) -> bool:
        """Sweeper helper: when a worker is reaped mid-flight, send its task
        back to the queue. Wipes assigned/started timestamps so it looks fresh."""
        task_id_str = str(task_id)
        client = await self._get_client()
        # Wipe metadata.current_run_id + metadata.assigned_at via read-modify-write.
        try:
            existing = (
                await client.table(self.TASKS_TABLE)
                .select("metadata")
                .eq("task_kind", TASK_KIND_AGENT)
                .eq("dbos_workflow_id", task_id_str)
                .maybe_single()
                .execute()
            )
            md: Dict[str, Any] = (existing.data or {}).get("metadata") or {}
        except Exception as e:
            logger.exception(f"Failed to read metadata for requeue {task_id_str}: {e}")
            md = {}
        md.pop("current_run_id", None)
        md.pop("assigned_at", None)

        try:
            result = (
                await client.table(self.TASKS_TABLE)
                .update(
                    {
                        "phase": "queued",
                        "status": LIFECYCLE_TO_STATUS["queued"],
                        "started_at": None,
                        "metadata": md,
                    }
                )
                .eq("task_kind", TASK_KIND_AGENT)
                .eq("dbos_workflow_id", task_id_str)
                .in_("phase", ["assigned", "in_progress"])
                .execute()
            )
            return bool(result.data)
        except Exception as e:
            logger.exception(f"Failed to requeue task {task_id_str}: {e}")
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
            logger.error(f"Failed to log state transition for {agent_id}: {e}")
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
            logger.error(f"Failed to list state history {agent_id}: {e}")
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
            logger.error(f"Failed to enqueue outbox from {sender_agent_id}: {e}")
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
            logger.error(f"Failed to mark outbox {message_id} delivered: {e}")
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
            logger.error(f"Failed to list undelivered outbox: {e}")
            return []


def get_agent_workforce_repository() -> (
    Union["AgentWorkforceRepository", "AgentWorkforceRepositoryOrm"]
):
    """Return the right AgentWorkforceRepository implementation per env.

    ORM (5-table workforce bounded context) when ``USE_ORM_WORKFORCE`` is set
    AND the SQLAlchemy engine is configured; otherwise the legacy supabase-py
    REST path. A flag-on but engine-missing deploy logs once and falls back to
    REST (never crashes).
    """
    from app.core.config import settings

    if settings.USE_ORM_WORKFORCE:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.agent_workforce_repository_orm import (
                AgentWorkforceRepositoryOrm,
            )

            return AgentWorkforceRepositoryOrm()
        logger.warning(
            "USE_ORM_WORKFORCE=true but SUPAVISOR_DATABASE_URL is empty "
            "— falling back to supabase-py path"
        )
    return AgentWorkforceRepository()
