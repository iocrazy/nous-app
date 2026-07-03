"""G1 — Repository for agent_approval_requests (mig 198).

CRUD on the approval-gate state machine that pauses agent runs awaiting
human decision. Used by:
  - AgentRunner (writes when hook returns await_approval)
  - approval router (lists pending / records decision)
  - sweeper (marks expired rows after expires_at)

ORM-only (post-rollout cleanup): the legacy supabase-py REST path has been
removed — prod runs 100% SQLAlchemy 2.0. The FROZEN-DATACLASS parity approach
is preserved: the read methods fetch native-typed rows, convert each to a
REST-shaped dict via ``_rest_row`` (uuid → str, datetime → ISO str; bigint
session_id / run_id stay NATIVE int), then feed that dict to the UNCHANGED
``ApprovalRequest.from_row(dict)`` builder — so every frozen-dataclass field is
byte-identical in type and value to what REST produced. The uuid fields
(id / user_id / agent_id / decided_by) are wrapped BACK to native ``uuid.UUID``
by the builder (the dataclass fields ARE typed UUID) — the router approve/reject
authz check ``existing.user_id != user_uuid`` is UUID==UUID, so str()ing them
would break parity. session_id / run_id are BIGINT columns but STR dataclass
fields (mig 231/232 snowflakes) — int()-coerced at the create() bind (asyncpg's
int8 codec rejects a str). Writes commit via write_scope(); reads use
read_scope(). Error handling: create raises on exception; get_by_id / list
swallow → None / []; decide → False on exception; mark_expired → 0.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ApprovalRequest:
    id: UUID
    user_id: UUID
    agent_id: UUID
    # ai_sessions.id / agent_runs.id are BIGINT Snowflake (mig 231/232) →
    # numeric strings, not UUIDs.
    session_id: Optional[str]
    run_id: Optional[str]
    hook_name: str
    reason: str
    payload: dict
    status: str  # pending / approved / rejected / expired / cancelled
    decided_at: Optional[datetime]
    decided_by: Optional[UUID]
    decision_note: Optional[str]
    created_at: datetime
    expires_at: datetime

    @classmethod
    def from_row(cls, row: dict) -> "ApprovalRequest":
        def _dt_parse(v):
            if not v:
                return None
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))

        return cls(
            id=UUID(str(row["id"])),
            user_id=UUID(str(row["user_id"])),
            agent_id=UUID(str(row["agent_id"])),
            session_id=str(row["session_id"]) if row.get("session_id") else None,
            run_id=str(row["run_id"]) if row.get("run_id") else None,
            hook_name=row["hook_name"],
            reason=row["reason"],
            payload=row.get("payload") or {},
            status=row["status"],
            decided_at=_dt_parse(row.get("decided_at")),
            decided_by=UUID(str(row["decided_by"])) if row.get("decided_by") else None,
            decision_note=row.get("decision_note"),
            created_at=_dt_parse(row["created_at"]),
            expires_at=_dt_parse(row["expires_at"]),
        )


_APPROVAL_N2A: Dict[str, str] = _name_to_attr(AgentApprovalRequests)


def _rest_row(out: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a SELECT *-shaped ORM dict into the REST-shaped row dict the
    ``ApprovalRequest.from_row`` builder expects: uuid → str, datetime → ISO str.
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


class ApprovalRequestsRepository:
    """ORM-backed repository over ``agent_approval_requests``. Reuses the
    ``ApprovalRequest.from_row`` builder so the frozen dataclass is constructed
    byte-identically to the retired REST path."""

    TABLE = "agent_approval_requests"

    def _to_obj(self, obj: Any) -> ApprovalRequest:
        """Native ORM row → REST-shaped dict → builder → frozen dataclass."""
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
                    # is strict and REJECTS a str → int()-coerce for the bind.
                    # NULL passes through.
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
        updated rowcount (matching the retired REST path, which also returned True
        unconditionally on the non-exception path; a mismatched owner_user_id /
        already-terminal status silently no-ops but still returns True — the
        router's PRIOR get_by_id ownership+status guard is the real gate)."""
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


def get_approval_requests_repository() -> "ApprovalRequestsRepository":
    """Return the ApprovalRequestsRepository (ORM-backed, post-rollout).

    The per-domain USE_ORM_APPROVAL rollout flag has been retired now that prod
    runs 100% ORM — the factory unconditionally returns the collapsed class.
    """
    return ApprovalRequestsRepository()


__all__ = [
    "ApprovalRequest",
    "ApprovalRequestsRepository",
    "get_approval_requests_repository",
]
