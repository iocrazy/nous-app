"""SQLAlchemy 2.0 ORM data access for the M2 Persistent Workforce: workers /
inbox / tasks / state_history / outbox.

Five tables form one bounded context (the workforce lifecycle), so they
share a single repository. Sections below mirror the migration:

    1. workers             — runtime state per agent
    2. inbox               — incoming messages (user + system + cross-agent)
    3. tasks               — per-agent queue with lifecycle state machine
    4. state_history       — audit trail for state transitions
    5. outbox              — outgoing messages (Realtime delivery + audit)

Post-rollout the ORM path is the only path — the ``USE_ORM_WORKFORCE`` flag and
the legacy supabase-py REST bodies have been retired (prod ran 100% ORM). All
writes commit via ``write_scope()`` (RLS-bypassing admin session — workforce
dispatch is service-role logic); reads use ``read_scope()``.
``get_agent_workforce_repository()`` unconditionally returns
``AgentWorkforceRepository``.

The state-machine logic lives ONE LEVEL UP in
``app.services.workforce.state_machine`` — this repo only exposes the raw
DB primitives. Advisory locks are held by the state machine, not here.

★★★ THE CRASHER HOT SPOT — ~17 bare ``UUID(row[...])`` consumers ★★★
=====================================================================
The dicts this repo returns are consumed by ``UUID(...)`` calls ALL OVER
``app/services/workforce/*``. ``UUID(native_uuid_obj)`` raises ``TypeError``
(``UUID()`` wants str/bytes/int, NOT a uuid.UUID). So if the repo returned a
native ``uuid.UUID`` for any consumed column, the consumer would CRASH.

Defense: the generic ``_parity`` sweep stringifies ANY ``uuid.UUID`` and
ISO-formats ANY ``datetime`` / ``date`` over EVERY returned dict (same
structural guarantee as ``issue_repository._parity``). This makes every
uuid column a ``str`` so every ``UUID(returned)`` consumer gets a str and works.

Per-table PK / uuid / bigint map (audited against the models + migrations):
  agent_workers:        PK agent_id (UUID). uuid: agent_id, current_task_id.
                        int: worker_pid (Integer). NO bigint id.
  agent_inbox:          PK id (UUID, server gen_random_uuid). uuid: id,
                        recipient_agent_id, sender_user_id, sender_agent_id,
                        task_id, reply_to_message_id. int: priority (SmallInt).
  task_tracking:        PK dbos_workflow_id (TEXT — NOT a uuid, NOT bigint).
                        uuid: user_id, agent_id, inbox_message_id, group_id,
                        flow_id. TEXT: dbos_workflow_id / parent_task_id /
                        root_task_id / resource_id / media_id. bigint: issue_id
                        / speed / total_bytes (NOT exposed by the shape mapper).
                        int: progress / cost_cents.
  agent_state_history:  PK id (UUID). uuid: id, agent_id, task_id.
  agent_outbox:         PK id (UUID). uuid: id, sender_agent_id,
                        recipient_user_id, recipient_agent_id, task_id.

==/!= AUTHZ COMPARES (silent killers): NONE found in the workforce consumers.
The workforce layer routes uuids through ``UUID(...)`` (crash, not silent) — no
``row["x"] == some_str`` authz gate like the issues M-batch had. ``==`` compares
that DO exist are uuid-vs-uuid (e.g. delegate_tool ``target_agent_id ==
self.caller_agent_id`` AFTER both are ``UUID(...)``-wrapped) — unaffected by the
returned dict's str shape. The dedup-collision path compares ``"duplicate" in
str(e).lower()`` on the exception text — preserved verbatim below.

NON-uuid type-sensitive columns
--------------------------------
  dbos_workflow_id / parent_task_id / root_task_id / resource_id / media_id
    (TEXT) → native str (unchanged; these were str under REST too).
  task_tracking.issue_id / speed / total_bytes (BIGINT) → would stay native int
    (5.3 trap) — but the task-shape mapper does NOT expose them, so they never
    surface. NO str() applied to bigints anywhere (the _parity sweep only
    touches uuid/datetime/date — int passes through untouched).
  priority / progress / cost_cents (SmallInt/Int) → native int (untouched).
  ALL timestamptz → ``.isoformat()`` (heartbeat_at, created_at, started_at,
    completed_at, processed_at, expires_at, reading_claimed_at, delivered_at,
    changed_at, state_changed_at, updated_at). The shape mapper / list dicts feed
    these to pydantic / JSON, so ISO str is the parity shape. There are NO date
    (date-only) columns in the 5 tables.
  payload / metadata / metadata_json / subscribers (JSONB) → native dict/list.

Enum / model-quirk scan
-----------------------
  NO SQLAlchemy ``Enum`` columns on any of the 5 models — worker.state /
  inbox.status / inbox.message_type / inbox.sender_kind / task.phase /
  task.status / outbox.recipient_kind are all plain ``Text`` / ``String`` with
  DB CHECK constraints. So NO ``_plain`` unwrap is needed — reads return bare
  strings already. We still route reads through ``_orm_obj_to_dict`` (which
  applies ``_plain`` harmlessly) for the ONE renamed column:
  ``task_tracking.metadata`` is mapped to the Python attr ``metadata_``
  (SQLAlchemy reserves ``metadata`` on declarative classes), so the value MUST
  be read via the mapped attr. ``_name_to_attr`` handles this and re-keys the
  dict back to the DB name ``"metadata"`` — exactly the key the shape mapper
  (``tt_row_to_task_shape``) reads.

task_tracking WRITE-COLUMN DISCIPLINE (CLAUDE.md)
=================================================
For ``task_kind='workflow'`` rows, phase/status/progress/started_at/
completed_at/error_msg are owned by the ``mirror_dbos_lifecycle_to_tracking``
trigger and business code must NOT write them. BUT this repo ONLY ever touches
``task_kind='agent_task'`` rows (EVERY query carries the ``task_kind`` filter),
which the trigger does NOT mirror — application code IS the source of truth for
their phase/status. So writing phase/status/started_at/completed_at/error_msg on
agent_task rows is CORRECT (NOT a discipline violation; workflow rows are never
touched). The write set is reproduced column-for-column from the legacy.

DATE/TIMESTAMPTZ FILTER BINDING (the v3 rule)
=============================================
ONE timestamptz range FILTER: ``list_stale_workers`` does
``WHERE heartbeat_at < stale_before``. ``stale_before`` arrives as a NATIVE
aware ``datetime`` → bound DIRECTLY (never an ISO string, never naive). All
timestamptz column WRITES bind native aware ``datetime`` objects (built with
``datetime.now(timezone.utc)``, not ``.isoformat()``) so the asyncpg bind gets a
real ``DateTime(True)`` value. ``expires_at`` arrives as a native datetime →
bound directly.

BULK / UPSERT
=============
upsert_worker is the only ON CONFLICT path → ``pg_insert(AgentWorkers)
.on_conflict_do_update(index_elements=["agent_id"], set_=...)`` (the legacy PK
upsert). No batch inserts (state_history / inbox / outbox insert one row/call).

Every method swallows exceptions and returns the legacy fallback (None / False /
[] / {"items": [], "total": 0}) at the same log level — the workforce
sweeper/state-machine layers depend on these soft-fail contracts.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import Text, cast, func, literal, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import (
    AgentInbox,
    AgentOutbox,
    AgentStateHistory,
    AgentWorkers,
    TaskTracking,
)
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

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


def _jsonb_merge(column: Any, patch: Dict[str, Any]) -> Any:
    """``coalesce(col, '{}') || <patch>`` — an in-place JSONB key merge.

    The coalesce is load-bearing, not decoration: ``task_tracking.metadata`` is
    NULLABLE (only a server_default fills it), and ``NULL || x`` is NULL in
    Postgres. Without it a row that somehow carries a NULL metadata would
    swallow every stamp silently — a ``dispatched_at`` that never lands means
    the dispatch tick re-enqueues that row on every single tick, forever."""
    empty = cast(literal("{}"), JSONB)
    return func.coalesce(column, empty).op("||", return_type=JSONB)(
        cast(literal(json.dumps(patch)), JSONB)
    )


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


# ─────────────────────────────────────────────────────────────────
# Strategy-C parity layer (DB-name-keyed dicts → REST-shaped dicts)
# ─────────────────────────────────────────────────────────────────
# DB-column-name → mapped-attribute-name maps (built once). The only column
# that differs (name != attr) is task_tracking.metadata → metadata_.
_WORKERS_N2A: Dict[str, str] = _name_to_attr(AgentWorkers)
_INBOX_N2A: Dict[str, str] = _name_to_attr(AgentInbox)
_TASK_N2A: Dict[str, str] = _name_to_attr(TaskTracking)
_HISTORY_N2A: Dict[str, str] = _name_to_attr(AgentStateHistory)
_OUTBOX_N2A: Dict[str, str] = _name_to_attr(AgentOutbox)


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped dict.

    Generic sweep (the crasher defense): uuid → str (so every
    ``UUID(returned)`` consumer gets a str, never a native uuid.UUID →
    TypeError), datetime → ISO str, date → ISO str. Bigint ids/FKs and other
    ints pass through NATIVE (the 5.3 trap — no str() on int). TEXT PKs
    (dbos_workflow_id, parent/root) are already str. JSONB stays native
    dict/list. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _worker_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _WORKERS_N2A))


def _inbox_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _INBOX_N2A))


def _history_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _HISTORY_N2A))


def _outbox_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _OUTBOX_N2A))


def _task_raw(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, parity'd dict for one task_tracking row (DB-name keyed,
    metadata via the metadata_ attr). Feed this to ``tt_row_to_task_shape`` so
    the shape mapper reads STR uuids (agent_id/user_id) and a str
    dbos_workflow_id."""
    return _parity(_orm_obj_to_dict(obj, _TASK_N2A))


def _task_shape(obj: Optional[Any]) -> Optional[Dict[str, Any]]:
    """Full task_tracking ORM row → agent_tasks-style shape (parity'd)."""
    if obj is None:
        return None
    return tt_row_to_task_shape(_task_raw(obj))


class AgentWorkforceRepository:
    """Data access layer for the persistent workforce (M2, ORM 2.0)."""

    # Physical table name still read by an out-of-repo consumer:
    # app.services.workforce.agent_worker._lookup_inbox_message reaches through
    # ``workforce.INBOX_TABLE`` to do a best-effort supabase-py inbox read.
    # Kept for that caller (the other four REST-only table constants were
    # dropped with the legacy bodies).
    INBOX_TABLE = "agent_inbox"

    # ═════════════════════════════════════════════════════════════
    # 1. Workers
    # ═════════════════════════════════════════════════════════════

    async def get_worker(self, agent_id: UUID) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentWorkers)
                    .where(AgentWorkers.agent_id == str(agent_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _worker_row(row) if row else None
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
        now = datetime.now(timezone.utc)
        values = {
            "agent_id": str(agent_id),
            "state": state,
            "worker_pid": worker_pid,
            "worker_hostname": worker_hostname,
            "heartbeat_at": now,
            "state_changed_at": now,
        }
        try:
            stmt = (
                pg_insert(AgentWorkers)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["agent_id"],
                    set_={
                        "state": values["state"],
                        "worker_pid": values["worker_pid"],
                        "worker_hostname": values["worker_hostname"],
                        "heartbeat_at": values["heartbeat_at"],
                        "state_changed_at": values["state_changed_at"],
                    },
                )
                .returning(AgentWorkers)
            )
            async with write_scope() as session:
                result = await session.execute(stmt)
                row = result.scalars().first()
                return _worker_row(row) if row else None
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
        now = datetime.now(timezone.utc)
        values: Dict[str, Any] = {
            "state": state,
            "state_changed_at": now,
            "current_task_id": str(current_task_id) if current_task_id else None,
        }
        if bump_heartbeat:
            values["heartbeat_at"] = now
        try:
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(AgentWorkers)
                    .where(AgentWorkers.agent_id == str(agent_id))
                    .values(**values)
                    .returning(AgentWorkers.agent_id)
                )
                return result.scalars().first() is not None
        except Exception as e:
            logger.error(f"Failed to update worker state {agent_id}: {e}")
            return False

    async def heartbeat(self, agent_id: UUID) -> bool:
        """Lightweight heartbeat refresh — no state change."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(AgentWorkers)
                    .where(AgentWorkers.agent_id == str(agent_id))
                    .values(heartbeat_at=datetime.now(timezone.utc))
                    .returning(AgentWorkers.agent_id)
                )
                return result.scalars().first() is not None
        except Exception as e:
            logger.error(f"Failed heartbeat {agent_id}: {e}")
            return False

    async def list_stale_workers(
        self, *, stale_before: datetime
    ) -> List[Dict[str, Any]]:
        """Workers whose heartbeat is older than `stale_before` and still in an
        active state. ``stale_before`` is a NATIVE aware datetime → bound
        directly (v3 temporal-filter rule)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentWorkers)
                    .where(
                        AgentWorkers.state.in_(["idle", "working", "waiting_for_other"])
                    )
                    .where(AgentWorkers.heartbeat_at < stale_before)
                )
                return [_worker_row(r) for r in result.scalars().all()]
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
        values: Dict[str, Any] = {
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
            # Bind a NATIVE aware datetime (asyncpg → DateTime(True)); the legacy
            # passed expires_at.isoformat() to PostgREST.
            "expires_at": expires_at,
        }
        try:
            async with write_scope() as session:
                result = await session.execute(
                    pg_insert(AgentInbox).values(**values).returning(AgentInbox)
                )
                row = result.scalars().first()
                return _inbox_row(row) if row else None
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
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentInbox)
                    .where(AgentInbox.recipient_agent_id == str(recipient_agent_id))
                    .where(AgentInbox.dedup_key == dedup_key)
                    .where(AgentInbox.status.in_(["unread", "reading"]))
                    .limit(1)
                )
                row = result.scalars().first()
                return _inbox_row(row) if row else None
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
        status to 'reading' in one round-trip (CAS guard against double-claim).
        The state machine holds the per-agent advisory lock around this call."""
        try:
            async with write_scope() as session:
                # Highest priority, oldest first.
                picked = await session.execute(
                    select(AgentInbox.id)
                    .where(AgentInbox.recipient_agent_id == str(recipient_agent_id))
                    .where(AgentInbox.status == "unread")
                    .order_by(AgentInbox.priority.desc(), AgentInbox.created_at.asc())
                    .limit(1)
                )
                message_id = picked.scalars().first()
                if message_id is None:
                    return None

                updated = await session.execute(
                    sa_update(AgentInbox)
                    .where(AgentInbox.id == message_id)
                    .where(AgentInbox.status == "unread")  # CAS guard
                    .values(
                        status="reading",
                        reading_claimed_at=datetime.now(timezone.utc),
                        reading_claimed_by=claimed_by,
                    )
                    .returning(AgentInbox)
                )
                row = updated.scalars().first()
                return _inbox_row(row) if row else None
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
        values: Dict[str, Any] = {
            "status": status,
            "processed_at": datetime.now(timezone.utc),
        }
        if task_id is not None:
            values["task_id"] = str(task_id)
        try:
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(AgentInbox)
                    .where(AgentInbox.id == str(message_id))
                    .values(**values)
                    .returning(AgentInbox.id)
                )
                return result.scalars().first() is not None
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
            async with read_scope() as session:
                base = select(AgentInbox).where(
                    AgentInbox.recipient_agent_id == str(recipient_agent_id)
                )
                if status:
                    base = base.where(AgentInbox.status == status)

                count_stmt = select(func.count()).select_from(base.subquery())
                total = await session.scalar(count_stmt) or 0

                page_stmt = (
                    base.order_by(AgentInbox.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
                result = await session.execute(page_stmt)
                items = [_inbox_row(r) for r in result.scalars().all()]
            return {"items": items, "total": total}
        except Exception as e:
            logger.error(f"Failed to list inbox {recipient_agent_id}: {e}")
            return {"items": [], "total": 0}

    # ═════════════════════════════════════════════════════════════
    # 3. Tasks  (physical table task_tracking WHERE task_kind='agent_task')
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
        values: Dict[str, Any] = {
            "dbos_workflow_id": new_id,
            "task_kind": TASK_KIND_AGENT,
            "task_type": TASK_TYPE_AGENT,
            "agent_id": str(agent_id),
            "user_id": str(user_id),
            "title": (title or "Agent task").strip() or "Agent task",
            "status": "pending",
            "phase": "queued",
            "progress": 0,
            # JSONB metadata — mapped Python attr is metadata_ (DB name metadata).
            "metadata_": {"agent_payload": payload},
            "parent_task_id": str(parent_task_id) if parent_task_id else None,
            "root_task_id": root_id_str,
            "inbox_message_id": str(inbox_message_id) if inbox_message_id else None,
        }
        try:
            async with write_scope() as session:
                result = await session.execute(
                    pg_insert(TaskTracking).values(**values).returning(TaskTracking)
                )
                row = result.scalars().first()
                return _task_shape(row)
        except Exception as e:
            logger.exception(f"Failed to create task (agent={agent_id}): {e}")
            return None

    async def claim_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """CAS claim by id: queued → assigned, or None if someone got there first.

        Replaces the by-agent ``claim_next_queued`` (zero production callers —
        the dedup that actually ran was ``run_one_task``'s read-then-check,
        which is not atomic). The dispatcher already picked WHICH task; the only
        question left is whether this worker owns it.

        One UPDATE ... RETURNING, no preceding SELECT: the read-then-write it
        replaces left a window in which two workers both saw ``queued``."""
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            async with write_scope() as session:
                updated = await session.execute(
                    sa_update(TaskTracking)
                    .where(TaskTracking.dbos_workflow_id == str(task_id))
                    .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                    .where(TaskTracking.phase == "queued")  # the CAS
                    .values(
                        phase="assigned",
                        status=LIFECYCLE_TO_STATUS["assigned"],
                        metadata_=_jsonb_merge(
                            TaskTracking.metadata_, {"assigned_at": now_iso}
                        ),
                    )
                    .returning(TaskTracking)
                )
                return _task_shape(updated.scalars().first())
        except Exception as e:
            logger.exception(f"Failed to claim task {task_id}: {e}")
            return None

    async def list_undispatched_queued_tasks(
        self, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Queued agent_tasks nobody has enqueued yet — the dispatch tick's work
        list. Not just this tick's new rows: a task created outside the inbox
        path (a background sub-agent, phase 2b-2 §2.2) is picked up here too."""
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(TaskTracking)
                        .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                        .where(TaskTracking.phase == "queued")
                        .where(
                            TaskTracking.metadata_.op("->>", return_type=Text)(
                                cast(literal("dispatched_at"), Text)
                            ).is_(None)
                        )
                        .order_by(TaskTracking.created_at.asc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return [s for s in (_task_shape(r) for r in rows) if s]

    async def mark_dispatched(self, task_id: str) -> None:
        """Stamp ``metadata.dispatched_at`` so the next tick skips this row.

        Best effort: DBOS pins the workflow id to ``workforce-<task_id>``, so a
        lost stamp costs one extra enqueue that DBOS dedups — never a double
        run. Merged with ``||`` rather than read-modify-write so a concurrent
        metadata write (current_run_id, agent_result) is not clobbered."""
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_update(TaskTracking)
                    .where(TaskTracking.dbos_workflow_id == str(task_id))
                    .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                    .values(
                        metadata_=_jsonb_merge(
                            TaskTracking.metadata_, {"dispatched_at": now_iso}
                        )
                    )
                )
        except Exception as e:
            logger.exception(f"Failed to mark task {task_id} dispatched: {e}")

    async def count_inflight_agent_tasks(self) -> int:
        """Live agent_tasks, derived from the table rather than counted in this
        process. The estimate this replaces only ever incremented (nothing
        decremented on a terminal state), so it was a monotonically rising
        number wearing a gauge's name.

        ⚠️ DELIBERATELY does NOT follow this file's swallow-and-return-a-
        fallback convention. Its consumer is a health probe, and ``0`` would
        read as "the queue is empty" — the most reassuring possible answer to
        "I could not reach the database". Let it raise; the probe reports the
        gauge as unavailable and stays honest about not knowing."""
        async with read_scope() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(TaskTracking)
                .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                .where(TaskTracking.phase.in_(("queued", "in_progress")))
            )
        return int(total or 0)

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
        """Generic status update for an agent_task row. Read-modify-write the
        JSONB metadata (so unrelated keys survive), then persist phase/status
        (+ conditional started_at/completed_at/error_msg) — reproducing the
        legacy write set exactly."""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        task_id_str = str(task_id)

        try:
            async with write_scope() as session:
                existing = await session.execute(
                    select(TaskTracking.metadata_)
                    .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                    .where(TaskTracking.dbos_workflow_id == task_id_str)
                    .limit(1)
                )
                md_row = existing.first()
                md: Dict[str, Any] = dict((md_row[0] if md_row else None) or {})

                if current_run_id is not None:
                    md["current_run_id"] = str(current_run_id)
                if result is not None:
                    md["agent_result"] = result
                if error_code is not None:
                    md["error_code"] = error_code
                if lifecycle_status == "assigned":
                    md["assigned_at"] = now_iso

                values: Dict[str, Any] = {
                    "phase": lifecycle_status,
                    "status": LIFECYCLE_TO_STATUS.get(lifecycle_status, "pending"),
                    "metadata_": md,
                }
                if error_message is not None:
                    values["error_msg"] = error_message
                if lifecycle_status == "in_progress":
                    values["started_at"] = now
                if lifecycle_status in ("done", "failed", "cancelled"):
                    values["completed_at"] = now

                upd = await session.execute(
                    sa_update(TaskTracking)
                    .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                    .where(TaskTracking.dbos_workflow_id == task_id_str)
                    .values(**values)
                    .returning(TaskTracking.dbos_workflow_id)
                )
                return upd.scalars().first() is not None
        except Exception as e:
            logger.exception(
                f"Failed to update task {task_id_str} → {lifecycle_status}: {e}"
            )
            return False

    async def get_task(self, task_id: UUID) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(TaskTracking)
                    .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                    .where(TaskTracking.dbos_workflow_id == str(task_id))
                    .limit(1)
                )
                return _task_shape(result.scalars().first())
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
            async with read_scope() as session:
                base = select(TaskTracking).where(
                    TaskTracking.task_kind == TASK_KIND_AGENT
                )
                if agent_id:
                    base = base.where(TaskTracking.agent_id == str(agent_id))
                if user_id:
                    base = base.where(TaskTracking.user_id == str(user_id))
                if statuses:
                    # Filter on phase (preserves 8-state lifecycle precision).
                    base = base.where(TaskTracking.phase.in_(statuses))

                count_stmt = select(func.count()).select_from(base.subquery())
                total = await session.scalar(count_stmt) or 0

                page_stmt = (
                    base.order_by(TaskTracking.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
                result = await session.execute(page_stmt)
                items = [_task_shape(r) for r in result.scalars().all()]
            return {"items": items, "total": total}
        except Exception as e:
            logger.exception(f"Failed to list tasks: {e}")
            return {"items": [], "total": 0}

    async def requeue_task(self, task_id: UUID) -> bool:
        """Sweeper helper: send a reaped task back to the queue. Wipes
        assigned/started timestamps so it looks fresh."""
        task_id_str = str(task_id)
        try:
            async with write_scope() as session:
                existing = await session.execute(
                    select(TaskTracking.metadata_)
                    .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                    .where(TaskTracking.dbos_workflow_id == task_id_str)
                    .limit(1)
                )
                md_row = existing.first()
                md: Dict[str, Any] = dict((md_row[0] if md_row else None) or {})
                md.pop("current_run_id", None)
                md.pop("assigned_at", None)
                # The dispatch tick skips rows that carry ``dispatched_at``.
                # Leaving the stamp on a requeued task makes it queued forever
                # and enqueued never — a stall with no error on any surface.
                md.pop("dispatched_at", None)

                upd = await session.execute(
                    sa_update(TaskTracking)
                    .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                    .where(TaskTracking.dbos_workflow_id == task_id_str)
                    # ⚠️ 这两个词属于 **agent_task 的 8 状态 lifecycle**
                    # (``LIFECYCLE_TO_STATUS`` 的键)，不是 DBOS workflow 那套
                    # phase 词汇 —— 上面 ``task_kind == TASK_KIND_AGENT`` 已经
                    # 把行限定死了，这类行的 phase 全由本文件写，trigger 不参与
                    # (mig 200 注释)。所以这里的 ``in_progress`` 是真值，**不能**
                    # 换成 ACTIVE_PHASES；换了会把 agent 的 ``waiting_for_other``
                    # / ``blocked`` 一并 requeue，而漏掉 ``assigned``。
                    .where(TaskTracking.phase.in_(["assigned", "in_progress"]))
                    .values(
                        phase="queued",
                        status=LIFECYCLE_TO_STATUS["queued"],
                        started_at=None,
                        metadata_=md,
                    )
                    .returning(TaskTracking.dbos_workflow_id)
                )
                return upd.scalars().first() is not None
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
        values = {
            "agent_id": str(agent_id),
            "from_state": from_state,
            "to_state": to_state,
            "trigger": trigger,
            "task_id": str(task_id) if task_id else None,
            "metadata_json": metadata or {},
        }
        try:
            async with write_scope() as session:
                result = await session.execute(
                    pg_insert(AgentStateHistory)
                    .values(**values)
                    .returning(AgentStateHistory.id)
                )
                return result.scalars().first() is not None
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
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentStateHistory)
                    .where(AgentStateHistory.agent_id == str(agent_id))
                    .order_by(AgentStateHistory.changed_at.desc())
                    .limit(limit)
                )
                return [_history_row(r) for r in result.scalars().all()]
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
        values = {
            "sender_agent_id": str(sender_agent_id),
            "recipient_kind": recipient_kind,
            "recipient_user_id": (
                str(recipient_user_id) if recipient_user_id else None
            ),
            "recipient_agent_id": (
                str(recipient_agent_id) if recipient_agent_id else None
            ),
            "message_type": message_type,
            "payload": payload,
            "task_id": str(task_id) if task_id else None,
        }
        try:
            async with write_scope() as session:
                result = await session.execute(
                    pg_insert(AgentOutbox).values(**values).returning(AgentOutbox)
                )
                row = result.scalars().first()
                return _outbox_row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to enqueue outbox from {sender_agent_id}: {e}")
            return None

    async def mark_outbox_delivered(self, message_id: UUID) -> bool:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(AgentOutbox)
                    .where(AgentOutbox.id == str(message_id))
                    .values(
                        delivered=True,
                        delivered_at=datetime.now(timezone.utc),
                    )
                    .returning(AgentOutbox.id)
                )
                return result.scalars().first() is not None
        except Exception as e:
            logger.error(f"Failed to mark outbox {message_id} delivered: {e}")
            return False

    async def list_undelivered_outbox(
        self, *, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Outbox dispatcher reads this every tick to push messages over
        Realtime to recipient_user_id channels and into recipient agent inboxes."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentOutbox)
                    .where(AgentOutbox.delivered.is_(False))
                    .order_by(AgentOutbox.created_at.asc())
                    .limit(limit)
                )
                return [_outbox_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list undelivered outbox: {e}")
            return []


def get_agent_workforce_repository() -> AgentWorkforceRepository:
    """Return the AgentWorkforceRepository (ORM-only, 5-table workforce bounded
    context). The ``USE_ORM_WORKFORCE`` flag and the legacy supabase-py REST path
    have been retired post-rollout — prod runs 100% ORM."""
    return AgentWorkforceRepository()
