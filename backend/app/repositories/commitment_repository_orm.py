# app/repositories/commitment_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of CommitmentRepository (Phase 2, H batch).

REST → ORM successor for the ``agent_commitments`` table. Same Strangler-Fig
single-inheritance pattern as the validated migrations: ``CommitmentRepositoryOrm``
subclasses ``CommitmentRepository`` and overrides every DB method. Call sites
route through ``get_commitment_repository()`` (bottom of
``commitment_repository.py``).

★★★ THE FROZEN-DATACLASS PARITY APPROACH (H-batch central difference) ★★★
=========================================================================
Unlike the dict-returning repos, this repo returns the ``Commitment`` value
object (``app.agent_framework.commitments.Commitment``), built by the legacy
``CommitmentRepository._row_to_commitment(row_dict)``. That static builder takes
a REST-shaped row dict (uuid → str, bigint → int, timestamptz → ISO str) and
constructs the dataclass with SPECIFIC Python field types — casting ids to str,
parsing ISO timestamps to UTC-aware ``datetime`` via the module ``_parse_ts``,
and letting ``Commitment.__post_init__`` coerce ``trigger_type`` / ``status``
strings to their Enum members.

STRATEGY (builder reuse — structurally guarantees parity): the ORM read methods
fetch native-typed rows (uuid.UUID, int, aware datetime) from SQLAlchemy, convert
each row to a REST-SHAPED dict via ``_rest_row`` (a generic value-type sweep:
uuid → str, datetime → ISO str; bigint id / FK stay native int), then feed that
dict to the UNCHANGED inherited ``self._row_to_commitment(dict)``. Because the
exact same proven builder constructs the dataclass from the exact same REST dict
shape REST produced, every dataclass field is byte-identical in type and value to
the legacy path. We do NOT reimplement the dataclass construction.

PER-FIELD TYPE DECISION (what the legacy builder produces, and how _rest_row
feeds it):
  - id            (BIGINT)   → builder: ``row.get("id")`` (passed through). REST
                               returned a JSON number → ``Commitment.id: int``.
                               _rest_row keeps bigint NATIVE int (the 5.3 trap).
  - agent_id      (uuid)     → builder: ``str(row["agent_id"])`` →
                               ``Commitment.agent_id: str``. _rest_row uuid → str.
  - user_id       (uuid|NULL)→ builder: ``str(row["user_id"]) if ... else None``
                               → ``Commitment.user_id: Optional[str]``. **AUTHZ
                               CONSUMER**: ai_library_router fulfill/cancel do
                               ``existing.user_id != str(auth.user_id)`` (str ==
                               str). A native UUID here would ALWAYS be != the
                               str → 404 the owner. _rest_row uuid → str → builder
                               str()s again → str. CORRECT.
  - session_id    (uuid|NULL)→ builder: ``str(...) if ... else None`` →
                               Optional[str]. _rest_row uuid → str.
  - description   (text)     → builder: ``row["description"]`` → native str.
  - payload_json  (JSONB)    → builder: ``row.get("payload_json") or {}`` →
                               native dict.
  - trigger_type  (text)     → builder passes the bare string to the dataclass;
                               ``__post_init__`` coerces str → ``TriggerType``
                               Enum. The COLUMN is plain Text (NOT a SQLAlchemy
                               Enum) → ORM reads a bare str → no _plain unwrap
                               needed. _rest_row leaves text as-is.
  - trigger_at    (tstz|NULL)→ builder: ``_parse_ts(...)`` → aware datetime |
                               None. _rest_row datetime → ISO str → _parse_ts
                               re-parses to the SAME aware datetime.
  - trigger_event (text|NULL)→ builder: ``row.get("trigger_event")`` →
                               Optional[str].
  - expires_at    (tstz|NULL)→ builder ``_parse_ts`` → aware datetime | None.
  - status        (text)     → builder passes bare str; ``__post_init__`` →
                               ``CommitmentStatus`` Enum. Plain Text column → no
                               _plain unwrap.
  - created_at    (tstz)     → builder ``_parse_ts`` → aware datetime.
  - fulfilled_at  (tstz|NULL)→ builder ``_parse_ts`` → aware datetime | None.
  - fulfillment_run_id (BIGINT|NULL) → builder: ``str(row["fulfillment_run_id"])
                               if ... else None`` → ``Optional[str]``. The COLUMN
                               is BIGINT (native int from ORM), but the dataclass
                               field is STR and the builder str()s it. _rest_row
                               keeps the bigint NATIVE int → builder str()s →
                               str. CORRECT (matching REST, where the value also
                               arrived as a number and the builder str()d it).
  - fulfillment_notes (text|NULL) → builder ``row.get(...)`` → Optional[str].

uuid AUDIT (every uuid field): agent_id / user_id / session_id are all str()'d
by the builder. The ONLY type-sensitive consumer is the user_id authz compare
(``!= str(auth.user_id)``) — str==str, handled. agent_id is also used as a WRITE
bind in ``list_next_session`` (``Commitments.agent_id == agent_id``) — a str
bound to a Uuid column; asyncpg's Uuid codec accepts a str, matching the REST
.eq.

PHANTOM-COLUMN PRE-FLIGHT (write paths)
=======================================
  create() : ``_commitment_to_insert`` emits agent_id / description /
    trigger_type / status / payload_json / [user_id / session_id / trigger_at /
    trigger_event / expires_at] — ALL mapped columns on AgentCommitments. No
    phantom. id / created_at are server-managed (NOT in the insert payload) and
    come back via RETURNING. trigger_at / expires_at are written as ISO STRINGS
    by the legacy ``_commitment_to_insert`` (``.isoformat()``); asyncpg binds to
    a real DateTime(True) column and REQUIRES an aware datetime → coerced via
    ``_coerce_temporal`` at the write boundary (v3 temporal-binding rule).
  _set_terminal_status() : update payload is status / [fulfilled_at /
    fulfillment_run_id / fulfillment_notes] — ALL mapped. fulfilled_at is set to
    a NATIVE aware datetime (no ISO-string hazard on this path). ★ BIGINT BIND:
    fulfillment_run_id is a BIGINT column but callers (mark_fulfilled) pass a
    numeric STR (REST cast str→bigint); asyncpg's int8 codec is strict and
    rejects a str → we ``int()``-coerce it for the bind. No phantom.

DATE/TIMESTAMPTZ FILTER BINDING (v3)
====================================
Two read methods bind a temporal CUTOFF in a range filter:
  list_due_time       : ``trigger_at <= cutoff``
  list_expired_pending: ``expires_at <= cutoff`` (+ ``expires_at IS NOT NULL``)
The legacy computes ``cutoff = (now or datetime.now(utc)).isoformat()`` — an ISO
STRING. asyncpg binding an ISO string to a DateTime(True) comparison is the v3
hazard. We bind the NATIVE aware ``datetime`` (``now or datetime.now(utc)``)
directly — never the ISO string. Equality filters (status / trigger_type /
trigger_event / agent_id / user_id) bind bare strings, no temporal hazard.

WRITE COMMIT: create / _set_terminal_status use ``write_scope()`` (commits).
Reads use ``read_scope()``. Error handling mirrors the legacy EXACTLY: create
raises on empty row; _set_terminal_status returns None on no-matching-row, raises
on error; get_by_id swallows + returns None; the list_* methods raise on error.
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
from app.repositories.commitment_repository import CommitmentRepository

_COMMITMENT_N2A: Dict[str, str] = _name_to_attr(AgentCommitments)

# timestamptz columns. The legacy _commitment_to_insert / _set_terminal_status
# hand us ISO-STRINGS for these (REST/PostgREST accepted strings); asyncpg binds
# to a real DateTime(True) column and REQUIRES a native aware datetime, so we
# coerce ISO-str → datetime at the write boundary (v3 temporal-binding rule).
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
    legacy ``_row_to_commitment`` expects: uuid → str, datetime → ISO str. Bigint
    id / fulfillment_run_id stay NATIVE int (the 5.3 trap — the builder str()s
    fulfillment_run_id itself; id stays an int field). NULLs pass through. The
    builder then reconstructs the frozen dataclass identically to the REST
    path."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


class CommitmentRepositoryOrm(CommitmentRepository):
    """ORM-backed CommitmentRepository. Overrides every DB method; reuses the
    inherited ``_row_to_commitment`` / ``_commitment_to_insert`` builders so the
    frozen ``Commitment`` dataclass is constructed byte-identically to REST."""

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
            # Reuse the inherited value-object → insert-dict translator, then
            # coerce its ISO-string timestamps to native datetimes for asyncpg.
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


__all__ = ["CommitmentRepositoryOrm"]
