"""Repository for agent_commitments table (Sprint 4).

Persistence for cross-session followups. The pure value object lives in
``app.agent_framework.commitments.Commitment``; this repo translates to
and from the SQL row.

Uses the service-role (admin) client because access control is enforced
at the route layer via user-scoped clients (mirror agent_repository).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from app.agent_framework.commitments import (
    TERMINAL_STATUSES,
    Commitment,
    CommitmentStatus,
    TriggerType,
)
from app.db.supabase_client import get_async_supabase_admin


class CommitmentRepository:
    TABLE = "agent_commitments"

    async def _get_client(self):
        return await get_async_supabase_admin()

    # ------------------------------------------------------------------
    # Translators
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_commitment(row: Dict[str, Any]) -> Commitment:
        """SQL row → value object. Parses timestamps to UTC-aware."""
        return Commitment(
            id=row.get("id"),
            agent_id=str(row["agent_id"]),
            user_id=str(row["user_id"]) if row.get("user_id") else None,
            session_id=str(row["session_id"]) if row.get("session_id") else None,
            description=row["description"],
            payload_json=row.get("payload_json") or {},
            trigger_type=row["trigger_type"],
            trigger_at=_parse_ts(row.get("trigger_at")),
            trigger_event=row.get("trigger_event"),
            expires_at=_parse_ts(row.get("expires_at")),
            status=row["status"],
            created_at=_parse_ts(row.get("created_at")),
            fulfilled_at=_parse_ts(row.get("fulfilled_at")),
            fulfillment_run_id=(
                str(row["fulfillment_run_id"])
                if row.get("fulfillment_run_id")
                else None
            ),
            fulfillment_notes=row.get("fulfillment_notes"),
        )

    @staticmethod
    def _commitment_to_insert(c: Commitment) -> Dict[str, Any]:
        """Value object → dict for Supabase insert. Excludes server-managed
        columns (id, created_at)."""
        out: Dict[str, Any] = {
            "agent_id": c.agent_id,
            "description": c.description,
            "trigger_type": c.trigger_type.value,
            "status": c.status.value,
            "payload_json": c.payload_json or {},
        }
        if c.user_id:
            out["user_id"] = c.user_id
        if c.session_id:
            out["session_id"] = c.session_id
        if c.trigger_at:
            out["trigger_at"] = c.trigger_at.isoformat()
        if c.trigger_event:
            out["trigger_event"] = c.trigger_event
        if c.expires_at:
            out["expires_at"] = c.expires_at.isoformat()
        return out

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create(self, commitment: Commitment) -> Commitment:
        try:
            client = await self._get_client()
            payload = self._commitment_to_insert(commitment)
            result = await client.table(self.TABLE).insert(payload).execute()
            if not result.data:
                raise RuntimeError("commitment insert returned no row")
            return self._row_to_commitment(result.data[0])
        except Exception as exc:
            logger.error("Failed to create commitment: %s", exc)
            raise

    async def mark_fulfilled(
        self,
        commitment_id: int,
        *,
        fulfillment_run_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Optional[Commitment]:
        return await self._set_terminal_status(
            commitment_id,
            CommitmentStatus.FULFILLED,
            fulfillment_run_id=fulfillment_run_id,
            notes=notes,
            set_fulfilled_at=True,
        )

    async def mark_cancelled(
        self, commitment_id: int, *, notes: Optional[str] = None
    ) -> Optional[Commitment]:
        return await self._set_terminal_status(
            commitment_id, CommitmentStatus.CANCELLED, notes=notes
        )

    async def mark_failed(
        self, commitment_id: int, *, notes: Optional[str] = None
    ) -> Optional[Commitment]:
        return await self._set_terminal_status(
            commitment_id, CommitmentStatus.FAILED, notes=notes
        )

    async def mark_expired(self, commitment_id: int) -> Optional[Commitment]:
        return await self._set_terminal_status(commitment_id, CommitmentStatus.EXPIRED)

    async def _set_terminal_status(
        self,
        commitment_id: int,
        new_status: CommitmentStatus,
        *,
        fulfillment_run_id: Optional[str] = None,
        notes: Optional[str] = None,
        set_fulfilled_at: bool = False,
    ) -> Optional[Commitment]:
        if new_status not in TERMINAL_STATUSES:
            raise ValueError(
                f"_set_terminal_status called with non-terminal status {new_status}"
            )
        try:
            client = await self._get_client()
            update: Dict[str, Any] = {"status": new_status.value}
            if set_fulfilled_at:
                update["fulfilled_at"] = datetime.now(timezone.utc).isoformat()
            if fulfillment_run_id:
                update["fulfillment_run_id"] = fulfillment_run_id
            if notes:
                update["fulfillment_notes"] = notes
            # Only flip rows still pending — terminal is sticky.
            result = (
                await client.table(self.TABLE)
                .update(update)
                .eq("id", commitment_id)
                .eq("status", CommitmentStatus.PENDING.value)
                .execute()
            )
            if not result.data:
                return None
            return self._row_to_commitment(result.data[0])
        except Exception as exc:
            logger.error(
                "Failed to mark commitment %s as %s: %s",
                commitment_id,
                new_status.value,
                exc,
            )
            raise

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_by_id(self, commitment_id: int) -> Optional[Commitment]:
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", commitment_id)
                .maybe_single()
                .execute()
            )
            if not (result and result.data):
                return None
            return self._row_to_commitment(result.data)
        except Exception as exc:
            logger.error("Failed to get commitment %s: %s", commitment_id, exc)
            return None

    async def list_due_time(
        self, *, now: Optional[datetime] = None, limit: int = 100
    ) -> List[Commitment]:
        """Pending TIME triggers whose ``trigger_at <= now``. Sweeper input."""
        cutoff = (now or datetime.now(timezone.utc)).isoformat()
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("status", CommitmentStatus.PENDING.value)
                .eq("trigger_type", TriggerType.TIME.value)
                .lte("trigger_at", cutoff)
                .order("trigger_at", desc=False)
                .limit(limit)
                .execute()
            )
            return [self._row_to_commitment(r) for r in (result.data or [])]
        except Exception as exc:
            logger.error("Failed to list due time commitments: %s", exc)
            raise

    async def list_pending_event(
        self, event: str, *, limit: int = 100
    ) -> List[Commitment]:
        """Pending EVENT triggers matching ``event``. Event publisher input."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("status", CommitmentStatus.PENDING.value)
                .eq("trigger_type", TriggerType.EVENT.value)
                .eq("trigger_event", event)
                .limit(limit)
                .execute()
            )
            return [self._row_to_commitment(r) for r in (result.data or [])]
        except Exception as exc:
            logger.error("Failed to list event commitments for %s: %s", event, exc)
            raise

    async def list_next_session(
        self, *, agent_id: str, user_id: str, limit: int = 50
    ) -> List[Commitment]:
        """Pending NEXT_SESSION triggers for (agent, user). Session-open hook."""
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("status", CommitmentStatus.PENDING.value)
                .eq("trigger_type", TriggerType.NEXT_SESSION.value)
                .eq("agent_id", agent_id)
                .eq("user_id", user_id)
                .order("created_at", desc=False)
                .limit(limit)
                .execute()
            )
            return [self._row_to_commitment(r) for r in (result.data or [])]
        except Exception as exc:
            logger.error(
                "Failed to list next-session commitments for agent=%s user=%s: %s",
                agent_id,
                user_id,
                exc,
            )
            raise

    async def list_for_user(
        self,
        user_id: str,
        *,
        status: Optional[CommitmentStatus] = None,
        limit: int = 100,
    ) -> List[Commitment]:
        """User-facing 'my followups' list. Optional status filter."""
        try:
            client = await self._get_client()
            q = (
                client.table(self.TABLE)
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .limit(limit)
            )
            if status is not None:
                q = q.eq("status", status.value)
            result = await q.execute()
            return [self._row_to_commitment(r) for r in (result.data or [])]
        except Exception as exc:
            logger.error("Failed to list commitments for user %s: %s", user_id, exc)
            raise

    async def list_expired_pending(
        self, *, now: Optional[datetime] = None, limit: int = 100
    ) -> List[Commitment]:
        """Pending rows whose ``expires_at`` has passed. Sweeper marks them
        EXPIRED before they go stale."""
        cutoff = (now or datetime.now(timezone.utc)).isoformat()
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("status", CommitmentStatus.PENDING.value)
                .lte("expires_at", cutoff)
                .not_.is_("expires_at", "null")
                .limit(limit)
                .execute()
            )
            return [self._row_to_commitment(r) for r in (result.data or [])]
        except Exception as exc:
            logger.error("Failed to list expired commitments: %s", exc)
            raise


def _parse_ts(value: Any) -> Optional[datetime]:
    """Supabase returns timestamps as ISO strings or datetime — normalize
    to UTC-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        # PostgREST returns "2026-05-02T12:00:00+00:00" or with "Z"
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Could not parse timestamp: %r", value)
            return None
    return None


__all__ = ["CommitmentRepository"]
