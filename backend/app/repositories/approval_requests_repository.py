"""G1 — Repository for agent_approval_requests (mig 198).

CRUD on the approval-gate state machine that pauses agent runs awaiting
human decision. Used by:
  - AgentRunner (writes when hook returns await_approval)
  - approval router (lists pending / records decision)
  - sweeper (marks expired rows after expires_at)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


@dataclass(frozen=True)
class ApprovalRequest:
    id: UUID
    user_id: UUID
    agent_id: UUID
    session_id: Optional[UUID]
    run_id: Optional[UUID]
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
        def _dt(v):
            if not v:
                return None
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))

        return cls(
            id=UUID(str(row["id"])),
            user_id=UUID(str(row["user_id"])),
            agent_id=UUID(str(row["agent_id"])),
            session_id=UUID(str(row["session_id"])) if row.get("session_id") else None,
            run_id=UUID(str(row["run_id"])) if row.get("run_id") else None,
            hook_name=row["hook_name"],
            reason=row["reason"],
            payload=row.get("payload") or {},
            status=row["status"],
            decided_at=_dt(row.get("decided_at")),
            decided_by=UUID(str(row["decided_by"])) if row.get("decided_by") else None,
            decision_note=row.get("decision_note"),
            created_at=_dt(row["created_at"]),
            expires_at=_dt(row["expires_at"]),
        )


class ApprovalRequestsRepository:
    TABLE = "agent_approval_requests"

    async def _client(self):
        return await get_async_supabase_admin()

    async def create(
        self,
        *,
        user_id: UUID,
        agent_id: UUID,
        hook_name: str,
        reason: str,
        payload: Optional[dict] = None,
        session_id: Optional[UUID] = None,
        run_id: Optional[UUID] = None,
        ttl_hours: int = 24,
    ) -> Optional[ApprovalRequest]:
        try:
            client = await self._client()
            expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
            result = (
                await client.table(self.TABLE)
                .insert(
                    {
                        "user_id": str(user_id),
                        "agent_id": str(agent_id),
                        "session_id": str(session_id) if session_id else None,
                        "run_id": str(run_id) if run_id else None,
                        "hook_name": hook_name,
                        "reason": reason,
                        "payload": payload or {},
                        "expires_at": expires.isoformat(),
                    }
                )
                .execute()
            )
            if not result.data:
                return None
            return ApprovalRequest.from_row(result.data[0])
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
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("user_id", str(user_id))
                .eq("status", "pending")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return [ApprovalRequest.from_row(r) for r in (result.data or [])]
        except Exception as exc:
            logger.warning(f"[ApprovalRequestsRepo] list_pending failed: {exc}")
            return []

    async def get_by_id(self, request_id: UUID) -> Optional[ApprovalRequest]:
        try:
            client = await self._client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", str(request_id))
                .maybe_single()
                .execute()
            )
            if not result or not result.data:
                return None
            return ApprovalRequest.from_row(result.data)
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
        """Record an approval decision. Defensive owner_user_id filter
        in SQL (M3 pattern) prevents cross-user mutation even if the
        endpoint forgets the ownership check."""
        try:
            client = await self._client()
            await (
                client.table(self.TABLE)
                .update(
                    {
                        "status": "approved" if approve else "rejected",
                        "decided_at": datetime.now(timezone.utc).isoformat(),
                        "decided_by": str(owner_user_id),
                        "decision_note": note,
                    }
                )
                .eq("id", str(request_id))
                .eq("user_id", str(owner_user_id))  # defensive
                .eq("status", "pending")  # only pending → terminal
                .execute()
            )
            return True
        except Exception as exc:
            logger.warning(f"[ApprovalRequestsRepo] decide failed: {exc}")
            return False

    async def mark_expired(self, *, now: Optional[datetime] = None) -> int:
        """Sweep pending rows past their expires_at. Returns updated count."""
        try:
            client = await self._client()
            cutoff = (now or datetime.now(timezone.utc)).isoformat()
            result = (
                await client.table(self.TABLE)
                .update(
                    {
                        "status": "expired",
                        "decided_at": cutoff,
                    }
                )
                .eq("status", "pending")
                .lt("expires_at", cutoff)
                .execute()
            )
            return len(result.data or [])
        except Exception as exc:
            logger.warning(f"[ApprovalRequestsRepo] mark_expired failed: {exc}")
            return 0


__all__ = ["ApprovalRequest", "ApprovalRequestsRepository"]
