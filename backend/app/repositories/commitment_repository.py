"""Repository for agent_commitments table (Sprint 4).

Persistence for cross-session followups. The pure value object lives in
``app.agent_framework.commitments.Commitment``; this repo translates to
and from the SQL row.

ORM-only (post-rollout cleanup): the legacy supabase-py REST path has been
removed — prod runs 100% SQLAlchemy 2.0. Access control is enforced at the
route layer via user-scoped clients (mirror agent_repository); this repo runs
on the admin engine.

★★★ THE FROZEN-DATACLASS PARITY APPROACH ★★★
Unlike the dict-returning repos, this repo returns the ``Commitment`` value
object, built by ``_row_to_commitment(row_dict)``. That builder takes a
REST-shaped row dict (uuid → str, bigint → int, timestamptz → ISO str) and
constructs the dataclass with SPECIFIC Python field types. The ORM read methods
fetch native-typed rows, convert each to a REST-shaped dict via ``_rest_row``
(uuid → str, datetime → ISO str; bigint id / fulfillment_run_id stay NATIVE
int), then feed that dict to the UNCHANGED inherited ``_row_to_commitment`` — so
every dataclass field is byte-identical in type and value to what REST produced.

UUID AUDIT: agent_id / user_id / session_id are all str()'d by the builder. The
type-sensitive consumer is the router fulfill/cancel authz check
``existing.user_id != str(auth.user_id)`` (str==str) — a native UUID would 404
the owner. trigger_type / status are plain Text columns (NOT Enum) → bare str →
``Commitment.__post_init__`` coerces to TriggerType / CommitmentStatus enums.
fulfillment_run_id is BIGINT on the column but STR on the dataclass — the builder
str()s it (kept native int by _rest_row). id (bigint) stays native int.

v3 temporal: list_due_time / list_expired_pending bind the NATIVE aware datetime
cutoff (never an ISO string) in the trigger_at / expires_at range filter; create
/ _set_terminal_status _coerce_temporal the inherited ISO-string timestamps →
aware datetime for the asyncpg bind. Writes commit via write_scope(); reads use
read_scope(). Error handling: create raises on empty row; _set_terminal_status
returns None on no-pending-row; get_by_id swallows → None; list_* raise.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import insert as sa_insert
from sqlalchemy import select
from sqlalchemy import update as sa_update

from app.agent_framework.commitments import (
    TERMINAL_STATUSES,
    Commitment,
    CommitmentStatus,
    TriggerType,
)
from app.db.session import read_scope, write_scope
from app.models import AgentCommitments
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_COMMITMENT_N2A: Dict[str, str] = _name_to_attr(AgentCommitments)

# timestamptz columns. The _commitment_to_insert / _set_terminal_status
# translators hand us ISO-STRINGS for these (REST/PostgREST accepted strings);
# asyncpg binds to a real DateTime(True) column and REQUIRES a native aware
# datetime, so we coerce ISO-str → datetime at the write boundary (v3 rule).
_COMMITMENT_TS_COLS = frozenset(
    {"trigger_at", "expires_at", "created_at", "fulfilled_at"}
)


def _coerce_temporal(key: str, value: Any) -> Any:
    """ISO-string timestamptz patch value → native aware datetime for the
    asyncpg bind. Leaves native datetimes / None / non-ts keys untouched."""
    if key in _COMMITMENT_TS_COLS and isinstance(value, str):
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    return value


def _rest_row(out: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a SELECT *-shaped ORM dict into the REST-shaped row dict the
    ``_row_to_commitment`` builder expects: uuid → str, datetime → ISO str.
    Bigint id / fulfillment_run_id stay NATIVE int (the 5.3 trap — the builder
    str()s fulfillment_run_id itself; id stays an int field). NULLs pass
    through. The builder then reconstructs the frozen dataclass identically to
    the REST path."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


class CommitmentRepository:
    """ORM-backed repository for agent_commitments. Reuses the
    ``_row_to_commitment`` / ``_commitment_to_insert`` builders so the frozen
    ``Commitment`` dataclass is constructed byte-identically to the old REST
    path."""

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
        """Value object → dict for insert. Excludes server-managed columns
        (id, created_at)."""
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

    def _to_obj(self, obj: Any) -> Commitment:
        """Native ORM row → REST-shaped dict → inherited builder → dataclass."""
        return self._row_to_commitment(
            _rest_row(_orm_obj_to_dict(obj, _COMMITMENT_N2A))
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create(self, commitment: Commitment) -> Commitment:
        try:
            # Reuse the value-object → insert-dict translator, then coerce its
            # ISO-string timestamps to native datetimes for asyncpg.
            payload = self._commitment_to_insert(commitment)
            values = {k: _coerce_temporal(k, v) for k, v in payload.items()}
            stmt = (
                sa_insert(AgentCommitments).values(**values).returning(AgentCommitments)
            )
            async with write_scope() as session:
                result = await session.execute(stmt)
                row = result.scalars().first()
                if not row:
                    raise RuntimeError("commitment insert returned no row")
                return self._to_obj(row)
        except Exception as exc:
            logger.error(f"Failed to create commitment: {exc}")
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
            update: Dict[str, Any] = {"status": new_status.value}
            if set_fulfilled_at:
                update["fulfilled_at"] = datetime.now(timezone.utc)
            if fulfillment_run_id:
                # fulfillment_run_id is a BIGINT column; callers pass a numeric
                # STR (REST cast str→bigint). asyncpg's int8 codec is strict and
                # rejects a str → int()-coerce for the bind (the bigint analog of
                # _coerce_temporal).
                update["fulfillment_run_id"] = int(fulfillment_run_id)
            if notes:
                update["fulfillment_notes"] = notes
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(AgentCommitments)
                    # Only flip rows still pending — terminal is sticky.
                    .where(AgentCommitments.id == int(commitment_id))
                    .where(AgentCommitments.status == CommitmentStatus.PENDING.value)
                    .values(**update)
                    .returning(AgentCommitments)
                )
                row = result.scalars().first()
                if not row:
                    return None
                return self._to_obj(row)
        except Exception as exc:
            logger.error(
                "Failed to mark commitment {} as {}: {}",
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
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentCommitments)
                    .where(AgentCommitments.id == int(commitment_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return self._to_obj(row) if row else None
        except Exception as exc:
            logger.error(f"Failed to get commitment {commitment_id}: {exc}")
            return None

    async def list_due_time(
        self, *, now: Optional[datetime] = None, limit: int = 100
    ) -> List[Commitment]:
        """Pending TIME triggers whose ``trigger_at <= now``. Sweeper input."""
        # v3: bind the NATIVE aware datetime, never an ISO string.
        cutoff = now or datetime.now(timezone.utc)
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentCommitments)
                    .where(AgentCommitments.status == CommitmentStatus.PENDING.value)
                    .where(AgentCommitments.trigger_type == TriggerType.TIME.value)
                    .where(AgentCommitments.trigger_at <= cutoff)
                    .order_by(AgentCommitments.trigger_at.asc())
                    .limit(limit)
                )
                return [self._to_obj(r) for r in result.scalars().all()]
        except Exception as exc:
            logger.error(f"Failed to list due time commitments: {exc}")
            raise

    async def list_pending_event(
        self, event: str, *, limit: int = 100
    ) -> List[Commitment]:
        """Pending EVENT triggers matching ``event``. Event publisher input."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentCommitments)
                    .where(AgentCommitments.status == CommitmentStatus.PENDING.value)
                    .where(AgentCommitments.trigger_type == TriggerType.EVENT.value)
                    .where(AgentCommitments.trigger_event == event)
                    .limit(limit)
                )
                return [self._to_obj(r) for r in result.scalars().all()]
        except Exception as exc:
            logger.error(f"Failed to list event commitments for {event}: {exc}")
            raise

    async def list_next_session(
        self, *, agent_id: str, user_id: str, limit: int = 50
    ) -> List[Commitment]:
        """Pending NEXT_SESSION triggers for (agent, user). Session-open hook."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentCommitments)
                    .where(AgentCommitments.status == CommitmentStatus.PENDING.value)
                    .where(
                        AgentCommitments.trigger_type == TriggerType.NEXT_SESSION.value
                    )
                    # agent_id / user_id are str; asyncpg's Uuid codec binds the
                    # str to the Uuid column (matches the REST .eq).
                    .where(AgentCommitments.agent_id == agent_id)
                    .where(AgentCommitments.user_id == user_id)
                    .order_by(AgentCommitments.created_at.asc())
                    .limit(limit)
                )
                return [self._to_obj(r) for r in result.scalars().all()]
        except Exception as exc:
            logger.error(
                "Failed to list next-session commitments for agent={} user={}: {}",
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
            async with read_scope() as session:
                stmt = (
                    select(AgentCommitments)
                    .where(AgentCommitments.user_id == user_id)
                    .order_by(AgentCommitments.created_at.desc())
                    .limit(limit)
                )
                if status is not None:
                    stmt = stmt.where(AgentCommitments.status == status.value)
                result = await session.execute(stmt)
                return [self._to_obj(r) for r in result.scalars().all()]
        except Exception as exc:
            logger.error(f"Failed to list commitments for user {user_id}: {exc}")
            raise

    async def list_expired_pending(
        self, *, now: Optional[datetime] = None, limit: int = 100
    ) -> List[Commitment]:
        """Pending rows whose ``expires_at`` has passed. Sweeper marks them
        EXPIRED before they go stale."""
        # v3: bind the NATIVE aware datetime, never an ISO string.
        cutoff = now or datetime.now(timezone.utc)
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentCommitments)
                    .where(AgentCommitments.status == CommitmentStatus.PENDING.value)
                    .where(AgentCommitments.expires_at <= cutoff)
                    .where(AgentCommitments.expires_at.is_not(None))
                    .limit(limit)
                )
                return [self._to_obj(r) for r in result.scalars().all()]
        except Exception as exc:
            logger.error(f"Failed to list expired commitments: {exc}")
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
            logger.warning(f"Could not parse timestamp: {value}")
            return None
    return None


def get_commitment_repository() -> "CommitmentRepository":
    """Return the CommitmentRepository (ORM-backed, post-rollout).

    The per-domain USE_ORM_COMMITMENT rollout flag has been retired now that
    prod runs 100% ORM — the factory unconditionally returns the collapsed
    class.
    """
    return CommitmentRepository()


__all__ = ["CommitmentRepository", "get_commitment_repository"]
