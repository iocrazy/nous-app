# app/repositories/agent_workforce_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of AgentWorkforceRepository (Phase 2, H batch).

REST → ORM successor for the M2 Persistent Workforce bounded context: FIVE
tables in one repo — ``agent_workers`` / ``agent_inbox`` / ``task_tracking``
(agent_task rows only) / ``agent_state_history`` / ``agent_outbox``. Same
Strangler-Fig single-inheritance pattern as the validated issue / projects /
logs migrations: ``AgentWorkforceRepositoryOrm`` subclasses
``AgentWorkforceRepository`` and overrides every DB method. Call sites use the
``get_agent_workforce_repository()`` factory (bottom of the legacy file), which
returns the ORM impl when ``USE_ORM_WORKFORCE`` is set + the engine configured.

★★★ THE H-BATCH CRASHER HOT SPOT — ~17 bare ``UUID(row[...])`` consumers ★★★
=========================================================================
The dicts this repo returns are consumed by ``UUID(...)`` calls ALL OVER
``app/services/workforce/*``. ``UUID(native_uuid_obj)`` raises ``TypeError``
(``UUID()`` wants str/bytes/int, NOT a uuid.UUID). So if the ORM returned a
native ``uuid.UUID`` for any consumed column, the consumer CRASHES on flip.

Defense: the generic ``_parity`` sweep stringifies ANY ``uuid.UUID`` and
ISO-formats ANY ``datetime`` / ``date`` over EVERY returned dict (same
structural guarantee as ``issue_repository_orm._parity``). This makes every
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

The ~17 ``UUID(...)`` crasher consumers — every one gets a STR after the sweep:
  agent_worker.py:85-86  UUID(task["agent_id"]) / UUID(task["user_id"])
      task[...] comes from the task-shape mapper, whose agent_id/user_id read
      task_tracking.agent_id / .user_id (UUID cols) → swept to str. ✔
      (task["id"] = dbos_workflow_id is TEXT → already str. ✔)
  inbox_processor.py:128,138,152,153,164,172,173,202,225
      UUID(message["id"])  → agent_inbox.id (UUID) → str. ✔
      UUID(task["id"])     → dbos_workflow_id (TEXT) → already str. ✔
  outbox_dispatcher.py:85,111  UUID(row["id"]) → agent_outbox.id (UUID) → str. ✔
      also UUID(row["recipient_agent_id"]) / UUID(row["sender_agent_id"]) (UUID)
      → str. ✔  f-string dedup_key=f"outbox:{row['id']}" → str either way. ✔
  delegate_tool.py:161,297  UUID(target["id"]) → from agent_repo, not this repo;
      UUID(inbox_row["id"]) → agent_inbox.id (UUID, from enqueue_inbox) → str. ✔
  workforce_router.py:253,266  UUID(agent["id"]) → ``agent`` is from
      ``_resolve_persistent_agent`` (agent_repository), NOT this repo. Out of
      scope here; covered by agent_repository's own parity. (Listed in the H
      brief as a sanity backstop — confirmed not sourced from workforce repo.)

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
    changed_at, state_changed_at, updated_at). The legacy returned PostgREST ISO
    strings; the shape mapper / list dicts feed these to pydantic / JSON, so ISO
    str is the parity shape. There are NO date (date-only) columns in the 5
    tables.
  payload / metadata / metadata_json / subscribers (JSONB) → native dict/list
    (REST returned parsed objects; ORM returns native — parity).

Enum / model-quirk scan
-----------------------
  NO SQLAlchemy ``Enum`` columns on any of the 5 models — worker.state /
  inbox.status / inbox.message_type / inbox.sender_kind / task.phase /
  task.status / outbox.recipient_kind are all plain ``Text`` / ``String`` with
  DB CHECK constraints (verified in the models). So NO ``_plain`` unwrap is
  needed — reads return bare strings already. We still route reads through
  ``_orm_obj_to_dict`` (which applies ``_plain`` harmlessly) for the ONE renamed
  column: ``task_tracking.metadata`` is mapped to the Python attr ``metadata_``
  (SQLAlchemy reserves ``metadata`` on declarative classes), so the value MUST
  be read via the mapped attr, not ``getattr(obj, "metadata")``. ``_name_to_attr``
  handles this and re-keys the dict back to the DB name ``"metadata"`` — which is
  exactly the key the shape mapper (``tt_row_to_task_shape``) reads.

task_tracking WRITE-COLUMN REPRODUCTION (CLAUDE.md task_tracking discipline)
============================================================================
CLAUDE.md: for ``task_kind='workflow'`` rows, phase/status/progress/started_at/
completed_at/error_msg are owned by the ``mirror_dbos_lifecycle_to_tracking``
trigger and business code must NOT write them. BUT this repo ONLY ever touches
``task_kind='agent_task'`` rows (EVERY query carries ``.eq(task_kind, ...)`` /
``WHERE task_kind = 'agent_task'``), and per the task_tracking model comment
agent_task rows are "应用层 (agent_workforce) 维护 lifecycle，不依赖
dbos.workflow_status" — i.e. the trigger does NOT mirror agent_task rows;
application code IS the source of truth for their phase/status. The legacy repo
therefore legitimately writes phase/status/started_at/completed_at/error_msg on
agent_task rows. We reproduce the legacy write set EXACTLY — column-for-column,
verbatim — adding NOTHING and dropping NOTHING:

  create_task   writes: dbos_workflow_id, task_kind, task_type, agent_id,
                user_id, title, status, phase, progress, metadata,
                parent_task_id, root_task_id, inbox_message_id.
  claim_next_queued writes: phase, status, metadata (with assigned_at spliced).
  update_task_status writes: phase, status, metadata; conditionally error_msg
                (only when error_message is not None), started_at (only on
                'in_progress'), completed_at (only on done/failed/cancelled).
  requeue_task  writes: phase, status, started_at(=None), metadata.

All four scope the UPDATE/INSERT to ``task_kind='agent_task'`` (+ the CAS guards
on phase). No trigger-owned WORKFLOW-row column is ever written. progress is
written only on the initial insert (=0), exactly as the legacy did.

PHANTOM-COLUMN PRE-FLIGHT (per write path) — ALL CLEAR
======================================================
  upsert_worker: agent_id/state/worker_pid/worker_hostname/heartbeat_at/
    state_changed_at — all mapped AgentWorkers columns. ✔
  update_worker_state: state/state_changed_at/current_task_id(+heartbeat_at) ✔
  heartbeat: heartbeat_at ✔
  enqueue_inbox: recipient_agent_id/sender_kind/sender_user_id/sender_agent_id/
    message_type/payload/priority/dedup_key/reply_to_message_id/expires_at —
    all mapped AgentInbox columns. ✔
  claim_next_unread update: status/reading_claimed_at/reading_claimed_by ✔
  mark_inbox_processed: status/processed_at(+task_id) ✔
  create_task / claim_next_queued / update_task_status / requeue_task: all
    mapped TaskTracking columns (see WRITE-COLUMN REPRODUCTION). ✔
  log_state_transition: agent_id/from_state/to_state/trigger/task_id/
    metadata_json — all mapped AgentStateHistory columns. ✔
  enqueue_outbox: sender_agent_id/recipient_kind/recipient_user_id/
    recipient_agent_id/message_type/payload/task_id — all mapped AgentOutbox
    columns. ✔
  mark_outbox_delivered: delivered/delivered_at ✔
No phantom columns anywhere → no HARD STOP. No broken/no-op endpoint repaired
(inert discipline): the legacy dedup-collision swallow and all the
exception-return-None paths are reproduced byte-for-byte.

DATE/TIMESTAMPTZ FILTER BINDING (v3 rule)
=========================================
ONE timestamptz range FILTER in this repo: ``list_stale_workers`` does
``WHERE heartbeat_at < stale_before``. ``stale_before`` arrives as a NATIVE
aware ``datetime`` (the sweeper builds it with ``datetime.now(tz) -
timedelta``), so we bind it DIRECTLY — never an ISO string, never naive. All
other time columns are written as ISO strings by the legacy; on the ORM write
boundary asyncpg needs a native aware datetime, so writes that set timestamptz
columns bind native ``datetime`` objects (we build them with
``datetime.now(timezone.utc)`` directly rather than ``.isoformat()`` so the
asyncpg bind gets a real DateTime(True) value). ``expires_at`` (enqueue_inbox)
arrives as a native datetime param → bound directly. No naive/ISO-string temporal
binds reach a WHERE or a column set.

BULK / UPSERT
=============
upsert_worker is the only ON CONFLICT path → reproduced with
``pg_insert(AgentWorkers).on_conflict_do_update(index_elements=["agent_id"],
set_=...)`` (the legacy ``on_conflict="agent_id"`` PK upsert). No batch inserts
in this repo (state_history / inbox / outbox each insert one row per call).

Reads use ``read_scope()``; ALL writes commit via ``write_scope()``. Error
handling mirrors the legacy EXACTLY: every method swallows exceptions and
returns the legacy fallback (None / False / [] / {"items": [], "total": 0}) and
logs at the same level — the workforce sweeper/state-machine layers depend on
these soft-fail contracts.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
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
from app.repositories.agent_workforce_repository import (
    LIFECYCLE_TO_STATUS,
    TASK_KIND_AGENT,
    TASK_TYPE_AGENT,
    AgentWorkforceRepository,
    MessageType,
    RecipientKind,
    SenderKind,
    TaskLifecycle,
    WorkerState,
    _new_task_id,
    tt_row_to_task_shape,
)

# DB-column-name → mapped-attribute-name maps (built once). The only column
# that differs (name != attr) is task_tracking.metadata → metadata_.
_WORKERS_N2A: Dict[str, str] = _name_to_attr(AgentWorkers)
_INBOX_N2A: Dict[str, str] = _name_to_attr(AgentInbox)
_TASK_N2A: Dict[str, str] = _name_to_attr(TaskTracking)
_HISTORY_N2A: Dict[str, str] = _name_to_attr(AgentStateHistory)
_OUTBOX_N2A: Dict[str, str] = _name_to_attr(AgentOutbox)


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped dict.

    Generic sweep (the H-batch crasher defense): uuid → str (so every
    ``UUID(returned)`` consumer gets a str, never a native uuid.UUID →
    TypeError), datetime → ISO str, date → ISO str. Bigint ids/FKs and other
    ints pass through NATIVE (the 5.3 trap — no str() on int). TEXT PKs
    (dbos_workflow_id, parent/root) are already str. JSONB stays native
    dict/list. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
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


class AgentWorkforceRepositoryOrm(AgentWorkforceRepository):
    """ORM-backed AgentWorkforceRepository (5-table workforce bounded context)."""

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

    async def claim_next_queued(self, *, agent_id: UUID) -> Optional[Dict[str, Any]]:
        """Atomic claim: pick the oldest queued task for this agent and flip to
        'assigned' (CAS guard on phase). Caller holds the advisory lock."""
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            async with write_scope() as session:
                picked = await session.execute(
                    select(TaskTracking.dbos_workflow_id, TaskTracking.metadata_)
                    .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                    .where(TaskTracking.agent_id == str(agent_id))
                    .where(TaskTracking.phase == "queued")
                    .order_by(TaskTracking.created_at.asc())
                    .limit(1)
                )
                first = picked.first()
                if first is None:
                    return None
                task_id = first[0]
                existing_md: Dict[str, Any] = dict(first[1] or {})
                existing_md["assigned_at"] = now_iso

                updated = await session.execute(
                    sa_update(TaskTracking)
                    .where(TaskTracking.dbos_workflow_id == task_id)
                    .where(TaskTracking.phase == "queued")  # CAS guard
                    .values(
                        phase="assigned",
                        status=LIFECYCLE_TO_STATUS["assigned"],
                        metadata_=existing_md,
                    )
                    .returning(TaskTracking)
                )
                return _task_shape(updated.scalars().first())
        except Exception as e:
            logger.exception(f"Failed to claim queued task (agent={agent_id}): {e}")
            return None

    async def update_task_status(
        self,
        *,
        task_id: UUID,
        lifecycle_status: TaskLifecycle,
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

                upd = await session.execute(
                    sa_update(TaskTracking)
                    .where(TaskTracking.task_kind == TASK_KIND_AGENT)
                    .where(TaskTracking.dbos_workflow_id == task_id_str)
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


__all__ = ["AgentWorkforceRepositoryOrm"]
