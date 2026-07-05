"""Repository for agent_runs table (SQLAlchemy 2.0 ORM) — read paths + the
cancel trigger + the sweeper helpers (writes to agent_runs are otherwise owned
by RunRecorder).

Post-rollout the repository IS the SQLAlchemy 2.0 implementation — the legacy
supabase-py REST path and its ``USE_ORM_AGENT_RUNS`` flag were retired once prod
ran 100% ORM. Reads go through ``read_scope()``; the two writes
(``request_cancel`` / ``mark_heartbeat_lost``) go through ``write_scope()``
(which ``session.begin()``s and COMMITS — this is the P0 fix over the old
non-committing ``engine.connect()`` path, which silently rolled the UPDATE back
on close). Call sites route through ``get_agent_runs_repository()`` (bottom of
this module) so the backing store stays invisible to them.

VALUE-TYPE PARITY (REST-origin domain, Task 5.3). The effective prod baseline
the ``monthly_usage_by_agent`` consumer was built against is the REST
(supabase-py/PostgREST) base — PostgREST renders uuid/numeric/bigint as JSON
strings, so the usage projection coerces to match:
  - ``agent_id`` / ``user_id`` (uuid) → str (the consumer does
    ``UUID(str(agent_id))`` and ``str(r["user_id"]) == str(uuid)``).
  - ``cost_cents`` (numeric) → str (the consumer does ``float(cost_cents)``;
    PostgREST renders numeric as a JSON str).
  - ``team_id`` / ``project_id`` (bigint) stay NATIVE int — the backend REST
    base returned them as int and the team/project-scope filter is a bare int
    compare (``r.get("team_id") == team_id``); stringifying them silently zeroes
    those scopes. See ``_usage_row_to_dict`` / ``_USAGE_STR_COLS``.
The other reads (list_*/get_by_id) are NOT stringified: they leave the HTTP
boundary via FastAPI ``jsonable_encoder`` (UUID→str, datetime→ISO), so native
uuid.UUID / datetime in the dict produces byte-identical HTTP responses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import case, func, select, update

from app.db.repository_base import AsyncpgRepository
from app.db.session import read_scope, write_scope
from app.models import AgentRuns
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

# agent_runs DB-column-name → mapped-attribute-name. Built once from the mapper.
# For agent_runs every name == key (no reserved-name remap), but we resolve via
# this map anyway for parity with the sibling repos and to stay correct if a
# column is ever renamed.
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
# (supabase-py/PostgREST) backend baseline returned, because the /usage consumer
# (ai_library_router.get_usage) does Python-level type-sensitive ops on them.
# Coerce EXACTLY these three (the ORM returns native uuid.UUID / Decimal):
#   - agent_id : uuid    → str   (consumer does UUID(str(agent_id)) + dict key)
#   - user_id  : uuid    → str   (consumer does str(r["user_id"]) == str(uuid))
#   - cost_cents : numeric → str (REST renders numeric as a JSON str; consumer
#                                 does float(cost_cents) — str works)
# DO NOT coerce team_id / project_id: they are bigint, and the *backend*
# supabase-py base returned bigint as a native Python int (JSON number → int —
# the bigint→str precision concern is a FRONTEND/JS issue via bigIntSafeFetch,
# not backend). The consumer filters with a BARE int compare
# (``r.get("team_id") == team_id`` where team_id is an ``int`` query param), so
# stringifying them makes ``"123" == 123`` False → team/project usage returns
# ZERO. Tokens (int4) and status (text) already match REST — left as-is. NULLs
# pass through unchanged.
_USAGE_STR_COLS = ("agent_id", "user_id", "cost_cents")


def _usage_row_to_dict(row: Any) -> Dict[str, Any]:
    """One monthly_usage_by_agent row → dict with REST value-type parity.

    Coerces the uuid (agent_id / user_id) and numeric (cost_cents) columns to
    str (matching PostgREST's JSON rendering) so the /usage consumer's
    type-sensitive ops keep working. team_id / project_id (bigint) stay native
    int to match the backend REST base + the consumer's bare-int filter. NULLs
    stay None."""
    out = dict(row)
    for col in _USAGE_STR_COLS:
        val = out.get(col)
        if val is not None:
            out[col] = str(val)
    return out


def _agent_run_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for an ``agent_runs`` ORM row."""
    return _orm_obj_to_dict(obj, _AGENT_RUNS_NAME_TO_ATTR)


class AgentRunsRepository(AsyncpgRepository):
    """Data access for agent_runs. Writes are exclusively owned by RunRecorder;
    this repo exposes the read paths, the cancel trigger, and sweeper helpers.

    RLS on the table already restricts rows to the calling user's scope when
    a user-scoped client is used. The router layer enforces user-scope via
    explicit ``user_id`` filters in the query; the cancel path verifies
    ownership before flipping the flag.

    ``_bigint`` is inherited from ``AsyncpgRepository`` (``agent_runs.id`` /
    ``parent_run_id`` are BIGINT — migration 232 — and snowflake ids travel as
    strings through FastAPI path params, which the PG int8 codec rejects).
    """

    TABLE = "agent_runs"

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

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
        """Single run detail; None if not found OR not owned by user.

        The second clause doubles as authz: a stray run_id from another user
        reads as 404, not 403, to avoid leaking existence."""
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
        """Phase 4 of #199: direct children of one parent run, newest first.

        Direct only — caller asks recursively if they want a full tree (avoids
        surprise N+1 explosions). The user_id filter doubles as authz: a stray
        parent_run_id from another user reads as empty rather than leaking
        existence. Ordered by started_at DESC to match the Runs UI convention."""
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

    # ------------------------------------------------------------------
    # Admin cross-user listing (AdminAuthDep gates the router; NO user scope)
    # ------------------------------------------------------------------

    _ADMIN_SORTABLE = frozenset(
        {
            "started_at",
            "cost_cents",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
        }
    )

    # Projection for the admin listing — deliberately EXCLUDES the heavy text
    # columns (input_summary / output_summary / metadata_json /
    # skill_slugs_used): a 20-row page must not drag whole prompts around.
    _ADMIN_LIST_COLS = (
        "id",
        "user_id",
        "agent_id",
        "model",
        "provider",
        "status",
        "trigger",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost_cents",
        "started_at",
        "ended_at",
        "error_code",
    )

    async def list_runs_admin(
        self,
        *,
        user_id: Optional[UUID] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        started_after: Optional[datetime] = None,
        started_before: Optional[datetime] = None,
        sort_by: str = "started_at",
        sort_desc: bool = True,
        offset: int = 0,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Admin-only cross-user paginated ``agent_runs`` listing with filters.

        Deliberately has NO user-scope filter — admin sees every user's runs
        (the router gates access via ``AdminAuthDep``). Returns
        ``{"items": [...], "total": N}``. Datetimes bound as ``datetime``
        objects (never isoformat strings). Unknown ``sort_by`` falls back to
        ``started_at`` (allow-list — the value reaches ``getattr``).

        Selects the ``_ADMIN_LIST_COLS`` projection only — the heavy text
        columns (prompt/output summaries, metadata) stay in the DB."""
        if sort_by not in self._ADMIN_SORTABLE:
            sort_by = "started_at"
        try:
            base = select(*[getattr(AgentRuns, c) for c in self._ADMIN_LIST_COLS])
            if user_id is not None:
                base = base.where(AgentRuns.user_id == user_id)
            if model:
                base = base.where(AgentRuns.model == model)
            if provider:
                base = base.where(AgentRuns.provider == provider)
            if status:
                base = base.where(AgentRuns.status == status)
            if started_after is not None:
                base = base.where(AgentRuns.started_at >= started_after)
            if started_before is not None:
                base = base.where(AgentRuns.started_at <= started_before)

            sort_col = getattr(AgentRuns, sort_by)
            ordering = sort_col.desc() if sort_desc else sort_col.asc()

            async with read_scope() as session:
                total = await session.scalar(
                    select(func.count()).select_from(base.subquery())
                )
                result = await session.execute(
                    base.order_by(ordering).offset(offset).limit(limit)
                )
                items = [dict(r) for r in result.mappings().all()]
            return {"items": items, "total": int(total or 0)}
        except Exception as e:
            logger.error(f"Failed to list admin runs: {e}")
            return {"items": [], "total": 0}

    async def distinct_models(self) -> List[str]:
        """Distinct non-null model names present in ``agent_runs`` — populates
        the admin AI-usage model filter dropdown. Ordered alphabetically."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentRuns.model)
                    .where(AgentRuns.model.is_not(None))
                    .distinct()
                    .order_by(AgentRuns.model.asc())
                )
                return [m for (m,) in result.all() if m]
        except Exception as e:
            logger.error(f"Failed to list distinct models: {e}")
            return []

    async def daily_usage_by_model(
        self, *, started_after: datetime, user_id: Optional[UUID] = None
    ) -> List[Dict[str, Any]]:
        """Per-day × per-model rollup for the AI-usage charts.

        Returns rows ``{date, model, provider, requests, total_tokens,
        cost_cents}`` ordered by date. NULL model groups as-is (router renders
        'unknown'). Datetime bound as ``datetime``. ``user_id`` narrows to one
        user's runs (the user-facing usage page); None = all users (admin)."""
        try:
            day = func.date(AgentRuns.started_at).label("date")
            stmt = (
                select(
                    day,
                    AgentRuns.model,
                    AgentRuns.provider,
                    func.count().label("requests"),
                    func.coalesce(func.sum(AgentRuns.total_tokens), 0).label(
                        "total_tokens"
                    ),
                    func.coalesce(func.sum(AgentRuns.cost_cents), 0).label(
                        "cost_cents"
                    ),
                )
                .where(AgentRuns.started_at >= started_after)
                .group_by(day, AgentRuns.model, AgentRuns.provider)
                .order_by(day.asc())
            )
            if user_id is not None:
                stmt = stmt.where(AgentRuns.user_id == user_id)
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [dict(r) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to compute daily usage: {e}")
            return []

    async def daily_usage(
        self,
        *,
        started_after: datetime,
        user_id: Optional[UUID] = None,
        group_by: str = "model",
    ) -> List[Dict[str, Any]]:
        """Per-day rollup grouped by ``model`` or ``agent`` — powers the usage
        page's single hero chart with a dimension switcher.

        Returns rows ``{date, key, requests, failed_requests, prompt_tokens,
        completion_tokens, total_tokens, cost_cents}`` ordered by date. ``key``
        is the model name or the agent_id (uuid → str; caller enriches to a
        display label). ``failed_requests`` counts failed + heartbeat_lost so
        the page can surface a success rate. Unknown ``group_by`` falls back
        to model."""
        key_col = AgentRuns.agent_id if group_by == "agent" else AgentRuns.model
        try:
            day = func.date(AgentRuns.started_at).label("date")
            failed = func.sum(
                case(
                    (AgentRuns.status.in_(("failed", "heartbeat_lost")), 1),
                    else_=0,
                )
            ).label("failed_requests")
            stmt = (
                select(
                    day,
                    key_col.label("key"),
                    func.count().label("requests"),
                    failed,
                    func.coalesce(func.sum(AgentRuns.prompt_tokens), 0).label(
                        "prompt_tokens"
                    ),
                    func.coalesce(func.sum(AgentRuns.completion_tokens), 0).label(
                        "completion_tokens"
                    ),
                    func.coalesce(func.sum(AgentRuns.total_tokens), 0).label(
                        "total_tokens"
                    ),
                    func.coalesce(func.sum(AgentRuns.cost_cents), 0).label(
                        "cost_cents"
                    ),
                )
                .where(AgentRuns.started_at >= started_after)
                .group_by(day, key_col)
                .order_by(day.asc())
            )
            if user_id is not None:
                stmt = stmt.where(AgentRuns.user_id == user_id)
            async with read_scope() as session:
                result = await session.execute(stmt)
                return [dict(r) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to compute grouped daily usage: {e}")
            return []

    # ------------------------------------------------------------------
    # Cancel (flip flag; runner observes via RunRecorder.check_cancelled)
    # ------------------------------------------------------------------

    async def request_cancel(self, run_id: str, *, user_id: UUID) -> bool:
        """Set cancel_requested=true. Idempotent. Only acts on running, owned
        rows. Returns True iff a row was actually updated. Committed via
        write_scope (the old asyncpg path silently rolled this back)."""
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

    # ------------------------------------------------------------------
    # Sweeper helpers
    # ------------------------------------------------------------------

    async def mark_heartbeat_lost(self, *, stale_before: datetime) -> int:
        """Bulk-flip stuck running rows (heartbeat_at < stale_before) →
        heartbeat_lost. Returns the row count for telemetry. A SET-based UPDATE
        (naturally idempotent). Committed via write_scope. Datetimes bound as
        ``datetime`` objects, never isoformat strings."""
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

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    async def monthly_usage_by_agent(
        self,
        *,
        month_start: datetime,
        month_end: datetime,
    ) -> List[Dict[str, Any]]:
        """Raw rows (9-column projection) for [month_start, month_end). Caller
        (ai_library_router.get_usage) groups in Python — matches the REST
        baseline. Datetimes bound as ``datetime``.

        VALUE-TYPE PARITY: agent_id / user_id (uuid) + cost_cents (numeric) are
        coerced to str (see ``_usage_row_to_dict``) to match the live REST
        baseline — the consumer does ``str(r["user_id"]) == str(uuid)`` /
        ``UUID(str(r["agent_id"]))`` / ``float(cost_cents)`` and breaks on a
        native ``uuid.UUID`` / ``Decimal``. team_id / project_id (bigint) stay
        native int (REST backend returned int; the scope filter is a bare int
        compare — stringifying them silently zeroes team/project scopes)."""
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


# ─── Factory ──────────────────────────────────────────────────────────


def get_agent_runs_repository() -> AgentRunsRepository:
    """Return the AgentRunsRepository.

    Post-rollout there is no flag branch and no engine-missing fallback: the
    repository IS the SQLAlchemy 2.0 ORM implementation. Call sites just do
    ``repo = get_agent_runs_repository()`` and use it directly."""
    return AgentRunsRepository()
