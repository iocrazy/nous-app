# app/repositories/approval_requests_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of ApprovalRequestsRepository (Phase 2, H).

REST → ORM successor for the ``agent_approval_requests`` table (the approval-gate
state machine that pauses agent runs awaiting a human decision). Same Strangler-
Fig single-inheritance pattern: ``ApprovalRequestsRepositoryOrm`` subclasses
``ApprovalRequestsRepository`` and overrides every DB method. Call sites route
through ``get_approval_requests_repository()`` (bottom of
``approval_requests_repository.py``).

★★★ THE FROZEN-DATACLASS PARITY APPROACH (H-batch central difference) ★★★
=========================================================================
This repo returns the frozen ``ApprovalRequest`` dataclass, built by the legacy
``ApprovalRequest.from_row(row_dict)`` classmethod. That builder takes a
REST-shaped row dict (uuid → str, bigint → int, timestamptz → ISO str) and
constructs the dataclass with SPECIFIC Python field types — wrapping uuids back
into ``uuid.UUID`` via ``UUID(str(...))``, casting bigint ids to str, and parsing
ISO timestamps to UTC-aware ``datetime`` via the nested ``_dt`` helper.

STRATEGY (builder reuse — structurally guarantees parity): the ORM read methods
fetch native-typed rows (uuid.UUID, int, aware datetime) from SQLAlchemy, convert
each row to a REST-SHAPED dict via ``_rest_row`` (uuid → str, datetime → ISO str;
bigint stays native int), then feed that dict to the UNCHANGED inherited
``ApprovalRequest.from_row(dict)``. The exact same proven builder constructs the
dataclass from the exact same REST dict shape REST produced, so every dataclass
field is byte-identical in type and value to the legacy path. We do NOT
reimplement the dataclass construction.

PER-FIELD TYPE DECISION (what the legacy builder produces, and how _rest_row
feeds it):
  - id          (uuid)      → builder: ``UUID(str(row["id"]))`` →
                              ``ApprovalRequest.id: UUID``. _rest_row uuid → str
                              → builder wraps back to UUID.
  - user_id     (uuid)      → builder: ``UUID(str(row["user_id"]))`` →
                              ``user_id: UUID``. **AUTHZ CONSUMER**: ai_library_
                              router approve/reject do ``existing.user_id !=
                              user_uuid`` where ``user_uuid =
                              _coerce_user_uuid(auth.user_id)`` is a native UUID.
                              UUID == UUID → correct ownership check. _rest_row
                              uuid → str → builder UUID() → native UUID. CORRECT
                              (the dataclass field is UUID by design, NOT str).
  - agent_id    (uuid)      → builder: ``UUID(str(row["agent_id"]))`` →
                              ``agent_id: UUID``. (router renders ``str(r.agent_id)
                              )`` for the JSON response — str() of a UUID works.)
  - session_id  (BIGINT|NULL)→ builder: ``str(row["session_id"]) if ... else None``
                              → ``session_id: Optional[str]`` (mig 231/232: these
                              are bigint snowflakes rendered as numeric STRINGS).
                              _rest_row keeps the bigint NATIVE int → builder
                              str()s → str. CORRECT.
  - run_id      (BIGINT|NULL)→ builder: ``str(row["run_id"]) if ... else None`` →
                              Optional[str]. _rest_row native int → builder
                              str()s → str.
  - hook_name   (text)      → builder: ``row["hook_name"]`` → native str.
  - reason      (text)      → builder: ``row["reason"]`` → native str.
  - payload     (JSONB)     → builder: ``row.get("payload") or {}`` → native dict.
  - status      (text)      → builder: ``row["status"]`` → native str. The COLUMN
                              is plain Text (NOT a SQLAlchemy Enum) → ORM reads a
                              bare str → no _plain unwrap. **CONSUMER**: router
                              compares ``existing.status != "pending"`` (str==str)
                              → correct.
  - decided_at  (tstz|NULL) → builder ``_dt(...)`` → aware datetime | None.
  - decided_by  (uuid|NULL) → builder: ``UUID(str(...)) if ... else None`` →
                              Optional[UUID]. _rest_row uuid → str → builder
                              UUID() → native UUID.
  - decision_note (text|NULL)→ builder: ``row.get(...)`` → Optional[str].
  - created_at  (tstz)      → builder ``_dt(row["created_at"])`` → aware datetime.
  - expires_at  (tstz)      → builder ``_dt(row["expires_at"])`` → aware datetime.

uuid AUDIT (every uuid field): id / user_id / agent_id / decided_by are wrapped
back to native ``uuid.UUID`` by the builder — that IS the legacy dataclass shape
(fields typed ``UUID``), and the user_id authz consumer compares UUID == UUID, so
str()ing them would BREAK parity. _rest_row str()s them only as an intermediate
REST shape; the builder restores the UUID type. WRITE binds (create / get_by_id /
decide / mark_expired) pass str(uuid) to the Uuid columns exactly as the legacy
did — asyncpg's Uuid codec accepts the str.

PHANTOM-COLUMN PRE-FLIGHT (write paths)
=======================================
  create() : inserts user_id / agent_id / session_id / run_id / hook_name /
    reason / payload / expires_at — ALL mapped columns on AgentApprovalRequests.
    No phantom. id / created_at / status default server-side. expires_at is
    computed as a native aware datetime (``now + ttl``) — bound natively (no ISO
    string hazard on this path). status defaults to 'pending' server-side
    (matches REST, which omitted it).
    ★ BIGINT BIND HAZARD: session_id / run_id are BIGINT columns but the API
    accepts them as numeric STRINGS (mig 231/232 snowflakes). The legacy bound
    ``str(...)`` and PostgREST cast str→bigint. asyncpg's int8 codec is STRICT
    and rejects a str → we ``int()``-coerce them at the bind (the bigint analog
    of the v3 temporal coercion). NULL passes through. This is the ONLY
    ORM-specific write coercion in this repo.
  decide() : updates status / decided_at / decided_by / decision_note — ALL
    mapped. decided_at is an ISO string (``datetime.now(...).isoformat()``) →
    _coerce_temporal. No phantom.
  mark_expired() : updates status / decided_at (ISO str → _coerce_temporal). No
    phantom.

★ INERT DISCIPLINE — decide() returns True even on 0 rows matched ★
==================================================================
The legacy ``decide`` issues the UPDATE and returns ``True`` UNCONDITIONALLY on a
non-exception path — it does NOT check whether any row was actually updated (a
mismatched owner_user_id / already-terminal status silently no-ops but still
returns True; the router's PRIOR get_by_id ownership+status guard is what really
gates it). This is arguably a latent quirk, but it is the EXISTING behavior. Per
inert discipline we REPLICATE it EXACTLY — we do NOT add a rowcount check that
would start returning False on no-op (that would change observable behavior on
flip). decide() returns True on any clean execute, False only on exception.
(Flagged as a CONCERN in the report — not repaired here.)

DATE/TIMESTAMPTZ FILTER BINDING (v3)
====================================
  mark_expired : ``expires_at < cutoff`` (+ ``status == 'pending'``). The legacy
    computes ``cutoff = (now or datetime.now(utc)).isoformat()`` — an ISO STRING.
    asyncpg binding an ISO string to a DateTime(True) comparison is the v3
    hazard. We bind the NATIVE aware ``datetime`` (``now or datetime.now(utc)``)
    for the WHERE comparison; the same cutoff is bound (ISO-coerced via
    _coerce_temporal) for the decided_at UPDATE value. list_pending_for_user /
    get_by_id use only equality filters (no temporal range), no hazard.

WRITE COMMIT: create / decide / mark_expired use ``write_scope()`` (commits).
Reads use ``read_scope()``. Error handling mirrors the legacy EXACTLY: create /
get_by_id / list swallow + return None / [] on failure; create RAISES on
exception (the legacy ``raise`` after the warning log); decide returns False on
exception; mark_expired returns 0 on exception.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import AgentApprovalRequests
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.approval_requests_repository import (
    ApprovalRequest,
    ApprovalRequestsRepository,
)

_APPROVAL_N2A: Dict[str, str] = _name_to_attr(AgentApprovalRequests)

# timestamptz columns written as ISO strings by the legacy decide / mark_expired
# (REST/PostgREST accepted strings); asyncpg binds to a real DateTime(True)
# column and REQUIRES a native aware datetime → coerce at the write boundary
# (v3 temporal-binding rule).
_APPROVAL_TS_COLS = frozenset({"decided_at", "created_at", "expires_at"})


def _coerce_temporal(key: str, value: Any) -> Any:
    """ISO-string timestamptz patch value → native aware datetime for the
    asyncpg bind. Leaves native datetimes / None / non-ts keys untouched."""
    if key in _APPROVAL_TS_COLS and isinstance(value, str):
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    return value


def _rest_row(out: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a SELECT *-shaped ORM dict into the REST-shaped row dict the
    legacy ``ApprovalRequest.from_row`` expects: uuid → str, datetime → ISO str.
    Bigint session_id / run_id stay NATIVE int (the 5.3 trap — the builder str()s
    them itself). NULLs pass through. The builder then reconstructs the frozen
    dataclass (wrapping the uuid strings back to ``UUID``) identically to REST."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


class ApprovalRequestsRepositoryOrm(ApprovalRequestsRepository):
    """ORM-backed ApprovalRequestsRepository. Overrides every DB method; reuses
    the inherited ``ApprovalRequest.from_row`` builder so the frozen dataclass is
    constructed byte-identically to REST."""

    def _to_obj(self, obj: Any) -> ApprovalRequest:
        """Native ORM row → REST-shaped dict → inherited builder → dataclass."""
        return ApprovalRequest.from_row(_rest_row(_orm_obj_to_dict(obj, _APPROVAL_N2A)))

    async def create(
        self,
        *,
        user_id: UUID,
        agent_id: UUID,
        hook_name: str,
        reason: str,
        payload: Optional[dict] = None,
        # ai_sessions.id / agent_runs.id are BIGINT Snowflake (mig 231/232).
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        ttl_hours: int = 24,
    ) -> Optional[ApprovalRequest]:
        try:
            expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
            stmt = (
                sa_insert(AgentApprovalRequests)
                .values(
                    user_id=str(user_id),
                    agent_id=str(agent_id),
                    # session_id / run_id are BIGINT columns; the legacy bound
                    # str(...) (PostgREST cast str→bigint). asyncpg's int8 codec
                    # is strict and REJECTS a str → int()-coerce for the bind
                    # (the bigint analog of _coerce_temporal). NULL passes through.
                    session_id=int(session_id) if session_id else None,
                    run_id=int(run_id) if run_id else None,
                    hook_name=hook_name,
                    reason=reason,
                    payload=payload or {},
                    # native aware datetime — no ISO-string hazard on this path.
                    expires_at=expires,
                )
                .returning(AgentApprovalRequests)
            )
            async with write_scope() as session:
                result = await session.execute(stmt)
                row = result.scalars().first()
                if not row:
                    return None
                return self._to_obj(row)
        except Exception as exc:
            logger.warning(f"[ApprovalRequestsRepo] create failed: {exc}")
            raise

    async def list_pending_for_user(
        self,
        user_id: UUID,
        *,
        limit: int = 50,
    ) -> List[ApprovalRequest]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentApprovalRequests)
                    .where(AgentApprovalRequests.user_id == str(user_id))
                    .where(AgentApprovalRequests.status == "pending")
                    .order_by(AgentApprovalRequests.created_at.desc())
                    .limit(limit)
                )
                return [self._to_obj(r) for r in result.scalars().all()]
        except Exception as exc:
            logger.warning(f"[ApprovalRequestsRepo] list_pending failed: {exc}")
            return []

    async def get_by_id(self, request_id: UUID) -> Optional[ApprovalRequest]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentApprovalRequests)
                    .where(AgentApprovalRequests.id == str(request_id))
                    .limit(1)
                )
                row = result.scalars().first()
                if not row:
                    return None
                return self._to_obj(row)
        except Exception as exc:
            logger.warning(f"[ApprovalRequestsRepo] get_by_id failed: {exc}")
            return None

    async def decide(
        self,
        request_id: UUID,
        *,
        owner_user_id: UUID,
        approve: bool,
        note: Optional[str] = None,
    ) -> bool:
        """Record an approval decision. Defensive owner_user_id filter in SQL
        (M3 pattern) prevents cross-user mutation even if the endpoint forgets the
        ownership check.

        INERT DISCIPLINE: returns True on any clean execute — does NOT inspect the
        updated rowcount (matching the legacy, which also returns True
        unconditionally on the non-exception path). See module docstring CONCERN.
        """
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_update(AgentApprovalRequests)
                    .where(AgentApprovalRequests.id == str(request_id))
                    # defensive owner filter
                    .where(AgentApprovalRequests.user_id == str(owner_user_id))
                    # only pending → terminal
                    .where(AgentApprovalRequests.status == "pending")
                    .values(
                        status="approved" if approve else "rejected",
                        decided_at=datetime.now(timezone.utc),
                        decided_by=str(owner_user_id),
                        decision_note=note,
                    )
                )
            return True
        except Exception as exc:
            logger.warning(f"[ApprovalRequestsRepo] decide failed: {exc}")
            return False

    async def mark_expired(self, *, now: Optional[datetime] = None) -> int:
        """Sweep pending rows past their expires_at. Returns updated count."""
        try:
            # v3: bind the NATIVE aware datetime for the WHERE comparison.
            cutoff = now or datetime.now(timezone.utc)
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(AgentApprovalRequests)
                    .where(AgentApprovalRequests.status == "pending")
                    .where(AgentApprovalRequests.expires_at < cutoff)
                    .values(status="expired", decided_at=cutoff)
                    .returning(AgentApprovalRequests.id)
                )
                return len(result.scalars().all())
        except Exception as exc:
            logger.warning(f"[ApprovalRequestsRepo] mark_expired failed: {exc}")
            return 0


__all__ = ["ApprovalRequestsRepositoryOrm"]
