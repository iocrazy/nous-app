"""SQLAlchemy 2.0 ORM implementation of AgentRunsRepository (Task 5.3).

The successor to ``agent_runs_repository_asyncpg.py``. Same Strangler-Fig
multiple-inheritance pattern as ``MediaRepositoryOrm`` / ``ResourcesRepositoryOrm``
(overrides the 6 public data-access methods; inherits ``_bigint`` etc. from
``AsyncpgRepository``), but the internals run on the ORM session scopes from
``app.db.session`` instead of ``db_engine.fetch_one`` / ``fetch_all`` on a bare
``connect()``.

FRAMING NOTE: although this file lands as the asyncpg file's successor, the
``USE_ASYNCPG_AGENT_RUNS`` path was never live in prod — the effective baseline
the /usage consumer was built against is the REST (supabase-py/PostgREST) base.
So the on-flip comparison that matters for VALUE-TYPE parity is REST→ORM, not
asyncpg→ORM: PostgREST renders uuid/numeric/bigint as JSON STRINGS, and the
``monthly_usage_by_agent`` consumer relies on that (see that method + the
``_usage_row_to_dict`` coercion below).

THE P0 FIX
==========
The asyncpg path ran the two write methods (``request_cancel`` /
``mark_heartbeat_lost``) via ``self.fetch_one`` / ``self.fetch_all`` on a
NON-committing ``engine.connect()``. On connection close the UPDATE **silently
rolled back** — the returned RETURNING row looked written but the next read saw
the OLD value. This implementation routes both writes through ``write_scope()``
(which does ``session.begin()`` and COMMITS), so the cancel flag flip and the
heartbeat-lost sweep actually persist. The ``*_commits_*`` integration tests in
``tests/integration/test_agent_runs_repository_orm.py`` pin this with a fresh
read-back connection.

Fidelity contract (the swap must be invisible to all call sites):
  - dict at the boundary — never leak ORM ``AgentRuns`` objects. Same exact
    dict shapes as the REST/legacy impl (SELECT * for list_*/get_by_id, the
    9-column projection for monthly_usage_by_agent).
  - ``_bigint()`` coercion on str-snowflake ids (``id`` / ``parent_run_id`` are
    BIGINT — migration 232) before binding (asyncpg int8 codec is strict).
  - VALUE-TYPE parity for ``monthly_usage_by_agent`` ONLY: its uuid / bigint /
    numeric columns are coerced to str (``_usage_row_to_dict``) to match the
    REST baseline, because that row dict has a Python-level type-sensitive
    consumer (ai_library_router.get_usage). The other reads (list_*/get_by_id)
    are NOT stringified — they leave the HTTP boundary via FastAPI
    ``jsonable_encoder`` (UUID→str, datetime→ISO), so native uuid.UUID /
    datetime in the dict produces byte-identical HTTP responses; blanket
    stringifying them would be wrong.
  - datetimes bound as tz-aware ``datetime`` objects, never isoformat strings.
  - return shapes: dict for list_by_agent, dict|None for get_by_id, list[dict]
    for list_children/monthly_usage_by_agent, bool for request_cancel, int for
    mark_heartbeat_lost.

Note on enum/renamed columns: ``agent_runs`` has NEITHER. ``status`` /
``liveness_state`` are plain ``Text`` columns (DB-side CHECK constraints, not a
PG enum / SQLAlchemy ``Enum`` type), so reads come back as bare ``str`` already
— no ``_plain`` unwrap is load-bearing here. There is no ``metadata`` column
mapped to ``metadata_`` (the JSONB column is ``metadata_json``, name == attr).
We still build row dicts via ``_name_to_attr`` + ``_orm_obj_to_dict`` to stay
mechanically identical to 5.1/5.2 and future-proof against a schema rename.

Idempotency (these run under the sweeper cron / cancel endpoint, possibly
retried):
  - ``request_cancel``        — SET cancel_requested=true WHERE status='running';
                                re-run is a no-op once flipped (idempotent).
  - ``mark_heartbeat_lost``   — SET status='heartbeat_lost' WHERE status='running'
                                AND heartbeat_at < cutoff; a SET-based bulk update,
                                naturally idempotent (re-run matches 0 rows).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import func, select, update

from app.db.repository_base import AsyncpgRepository
from app.db.session import read_scope, write_scope
from app.models import AgentRuns
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.agent_runs_repository import AgentRunsRepository

# agent_runs DB-column-name → mapped-attribute-name. Built once from the mapper.
# For agent_runs every name == key (no reserved-name remap), but we resolve via
# this map anyway for parity with 5.1/5.2 and to stay correct if a column is ever
# renamed.
_AGENT_RUNS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(AgentRuns)

# The 9-column projection monthly_usage_by_agent returns. Pinned here so the ORM
# select returns EXACTLY the columns the REST/legacy impl did.
_USAGE_COLS = (
    "agent_id",
    "user_id",
    "team_id",
    "project_id",
    "status",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost_cents",
)

# Columns of the usage projection whose VALUE TYPE must match what the live REST
# (supabase-py/PostgREST) baseline returned, because the /usage consumer
# (ai_library_router.get_usage) does Python-level type-sensitive ops on them
# (``r["user_id"] == str(uuid)``, ``UUID(r["agent_id"])``, ``float(cost_cents)``).
# PostgREST renders these PG types as JSON STRINGS:
#   - agent_id / user_id  : uuid    → str
#   - team_id / project_id : bigint  → str (or null)
#   - cost_cents          : numeric → str (or null)
# The ORM returns native ``uuid.UUID`` / ``Decimal`` / ``int``; coerce the four
# str-rendered columns so the swap stays invisible to the consumer. The token
# counts (int4) and ``status`` (text) already match REST (number / str) — left
# as-is. NULLs pass through unchanged (PostgREST emits JSON null for them too).
_USAGE_STR_COLS = ("agent_id", "user_id", "team_id", "project_id", "cost_cents")


def _usage_row_to_dict(row: Any) -> Dict[str, Any]:
    """One monthly_usage_by_agent row → dict with REST value-type parity.

    Coerces the uuid / bigint / numeric columns to str (matching PostgREST's
    JSON rendering) so the /usage consumer's type-sensitive ops keep working.
    NULLs stay None."""
    out = dict(row)
    for col in _USAGE_STR_COLS:
        val = out.get(col)
        if val is not None:
            out[col] = str(val)
    return out


def _agent_run_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for an ``agent_runs`` ORM row."""
    return _orm_obj_to_dict(obj, _AGENT_RUNS_NAME_TO_ATTR)


class AgentRunsRepositoryOrm(AsyncpgRepository, AgentRunsRepository):
    """ORM-backed AgentRunsRepository.

    Overrides the 6 public data-access methods on the ``agent_runs`` table;
    ``_bigint`` is inherited from AsyncpgRepository. See
    ``agent_runs_repository.py`` for the method-level contracts (kept terse
    here to avoid drift)."""

    TABLE = "agent_runs"

    # ── Reads ───────────────────────────────────────────────────────

    async def list_by_agent(
        self,
        *,
        agent_id: UUID,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Paginated runs for one agent, scoped to the caller. Returns
        {"items": [...], "total": N}. Two queries — count + page — matching
        the prior shape rather than collapsing into a window function."""
        try:
            async with read_scope() as session:
                total = await session.scalar(
                    select(func.count())
                    .select_from(AgentRuns)
                    .where(AgentRuns.agent_id == agent_id)
                    .where(AgentRuns.user_id == user_id)
                )
                result = await session.execute(
                    select(AgentRuns)
                    .where(AgentRuns.agent_id == agent_id)
                    .where(AgentRuns.user_id == user_id)
                    .order_by(AgentRuns.started_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
                items = [_agent_run_to_dict(r) for r in result.scalars().all()]
            return {"items": items, "total": int(total or 0)}
        except Exception as e:
            logger.error(f"Failed to list runs (agent={agent_id}, user={user_id}): {e}")
            return {"items": [], "total": 0}

    async def get_by_id(
        self, run_id: str, *, user_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """Single run; None if not found OR not owned (no existence leak)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentRuns)
                    # agent_runs.id is BIGINT (mig 232); coerce str → int8.
                    .where(AgentRuns.id == self._bigint(run_id))
                    .where(AgentRuns.user_id == user_id)
                    .limit(1)
                )
                row = result.scalars().first()
                return _agent_run_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get run {run_id}: {e}")
            return None

    async def list_children(
        self,
        parent_run_id: str,
        *,
        user_id: UUID,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Phase 4 of #199 — direct children of one parent run, newest first."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentRuns)
                    # agent_runs.parent_run_id is BIGINT (mig 232); coerce.
                    .where(AgentRuns.parent_run_id == self._bigint(parent_run_id))
                    .where(AgentRuns.user_id == user_id)
                    .order_by(AgentRuns.started_at.desc())
                    .limit(limit)
                )
                return [_agent_run_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list children for parent={parent_run_id}: {e}")
            return []

    # ── Writes (COMMITTING — the P0 fix) ────────────────────────────

    async def request_cancel(self, run_id: str, *, user_id: UUID) -> bool:
        """Set cancel_requested=true. Idempotent. Only acts on running, owned
        rows. Returns True iff a row was actually updated. Committed via
        write_scope (the asyncpg path silently rolled this back)."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(AgentRuns)
                    # agent_runs.id is BIGINT (mig 232); coerce.
                    .where(AgentRuns.id == self._bigint(run_id))
                    .where(AgentRuns.user_id == user_id)
                    .where(AgentRuns.status == "running")
                    .values(cancel_requested=True)
                )
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.error(f"Failed to request cancel for run {run_id}: {e}")
            return False

    # ── Sweeper ─────────────────────────────────────────────────────

    async def mark_heartbeat_lost(self, *, stale_before: datetime) -> int:
        """Bulk-flip stuck running rows → heartbeat_lost. Returns row count for
        telemetry. A SET-based UPDATE (naturally idempotent). Committed via
        write_scope. Datetimes bound as ``datetime`` objects, never isoformat."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(AgentRuns)
                    .where(AgentRuns.status == "running")
                    .where(AgentRuns.heartbeat_at < stale_before)
                    .values(
                        status="heartbeat_lost",
                        ended_at=datetime.now(timezone.utc),
                        error_code="heartbeat_lost",
                        error_message="No heartbeat for >2 minutes",
                    )
                )
                return result.rowcount or 0
        except Exception as e:
            logger.error(f"Failed to mark heartbeat_lost: {e}")
            return 0

    # ── Aggregate ───────────────────────────────────────────────────

    async def monthly_usage_by_agent(
        self,
        *,
        month_start: datetime,
        month_end: datetime,
    ) -> List[Dict[str, Any]]:
        """Raw rows (9-column projection) for [month_start, month_end). Caller
        (ai_library_router.get_usage) groups in Python — matches the REST
        baseline. Datetimes bound as ``datetime``.

        VALUE-TYPE PARITY: the uuid / bigint / numeric columns are coerced to
        str (see ``_usage_row_to_dict``) so they match what the live REST
        baseline (PostgREST JSON) returned — the consumer does
        ``r["user_id"] == str(uuid)`` / ``UUID(r["agent_id"])`` /
        ``float(cost_cents)`` and breaks on native ``uuid.UUID`` / ``Decimal``."""
        try:
            cols = [getattr(AgentRuns, name) for name in _USAGE_COLS]
            async with read_scope() as session:
                result = await session.execute(
                    select(*cols)
                    .where(AgentRuns.started_at >= month_start)
                    .where(AgentRuns.started_at < month_end)
                )
                return [_usage_row_to_dict(r) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to load monthly usage: {e}")
            return []


__all__ = ["AgentRunsRepositoryOrm"]
