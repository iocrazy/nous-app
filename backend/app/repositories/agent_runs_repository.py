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
from sqlalchemy import Select, and_, case, func, or_, select, text, update

from app.db.repository_base import AsyncpgRepository
from app.db.session import read_scope, write_scope
from app.models import AgentRuns
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.services.ai.runner.turn_end import TurnEndReason

# agent_runs DB-column-name → mapped-attribute-name. Built once from the mapper.
# For agent_runs every name == key (no reserved-name remap), but we resolve via
# this map anyway for parity with the sibling repos and to stay correct if a
# column is ever renamed.
_AGENT_RUNS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(AgentRuns)

# The 9-column projection monthly_usage_by_agent returns. Pinned here so the ORM
# select returns EXACTLY the columns the REST/legacy impl did. The money column
# is the 9th and is built separately (_USAGE_COST_COL) — it reads own_cost_cents
# but keeps the cost_cents KEY, see below.
_USAGE_COLS = (
    "agent_id",
    "user_id",
    "team_id",
    "project_id",
    "status",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
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

# 钱那一列：读 ``own_cost_cents``（这一行自己烧的钱），但**键仍叫 cost_cents** —— 两个
# 消费方（``ai_library_router.get_usage`` 与 ``recompute_monthly_budgets_step``）按这
# 个键逐行累加，改的是读哪一列，不是投影的形状（3d 第 0 票）。
_USAGE_COST_COL = AgentRuns.own_cost_cents.label("cost_cents")

# Cap for the last-resort conversation title (first user message). The
# workbench row is one truncated line, so anything past this only costs
# payload bytes and accessible-name noise. See list_groups_by_agent.
_TITLE_MAX_LEN = 60


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


def _issue_scope_keys(
    issue_id: Optional[int], conversation_id: Optional[int]
) -> List[Any]:
    """「属于这个议题」的那组 OR 键 —— 两个钱读方共用，不许各写一份。

    一条 run 挂到议题上有两条路：直接戳 ``issue_id``，或者经它的 session
    ``conversation_id``。只认前一条会漏掉只走会话键的 run，于是同一个议题在预算门禁
    与驾驶舱 Budget 格上是两个数（``list_for_issue`` 也是拿两个键找行的）。
    """
    keys: List[Any] = []
    if issue_id is not None:
        keys.append(AgentRuns.issue_id == int(issue_id))
    if conversation_id is not None:
        keys.append(AgentRuns.conversation_id == int(conversation_id))
    return keys


def _own_cost_sum_stmt(*where: Any) -> Select[Any]:
    """``SUM(COALESCE(own_cost_cents, 0))`` over the matching rows —— 议题这一族
    「花了多少钱」的唯一表达式（3d 第 0 票）。

    两件事写死在这里，两个读方（``spent_cents_for_issue`` 与
    ``own_cost_cents_for_issue_runs``）共用，免得哪天只改一个：

    - **不按 root 过滤。** ``own_cost_cents`` 每行只记自身（own + media，不含
      后代），全行求和才是这个议题真花的钱。旧的 ``cost_cents`` 是「自身 + 已报到
      的后代」，求和时必须 root-only 才不双计 —— 而 Delegate 出去的子 run 带着
      ``issue_id`` 落库，正是被那道 root 过滤挡在预算之外，让议题预算对委派花费
      完全无感。这条 helper 就是那个缺陷的修法。
    - **每行读 ``COALESCE(own_cost_cents, 0)``。** NULL 只出现在「从没算过」的
      历史行上；不包一层的话，``SUM`` 本身照常跳过 NULL，但任何逐行运算（将来加
      ``FILTER`` / ``CASE``）会静默把整项变成 NULL。外层那个 coalesce 管的是另一
      件事：零行时 ``SUM`` 返回 NULL，调用方要的是 0。
    """
    return select(
        func.coalesce(func.sum(func.coalesce(AgentRuns.own_cost_cents, 0)), 0)
    ).where(*where)


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
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated runs for one agent, scoped to the caller. Returns
        {"items": [...], "total": N}. Two queries — count + page — matching
        the prior shape rather than collapsing into a window function.

        ``conversation_id`` narrows to one conversation's turns (the expand
        path of the grouped Runs view). Snowflake ids travel as strings
        through FastAPI query params — coerced to int here for the PG int8
        codec (same convention as ``_bigint``)."""
        try:
            async with read_scope() as session:
                base_filters = [
                    AgentRuns.agent_id == agent_id,
                    AgentRuns.user_id == user_id,
                ]
                if conversation_id is not None:
                    base_filters.append(
                        AgentRuns.conversation_id == int(conversation_id)
                    )
                total = await session.scalar(
                    select(func.count()).select_from(AgentRuns).where(*base_filters)
                )
                result = await session.execute(
                    select(AgentRuns)
                    .where(*base_filters)
                    .order_by(AgentRuns.started_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
                items = [_agent_run_to_dict(r) for r in result.scalars().all()]
            return {"items": items, "total": int(total or 0)}
        except Exception as e:
            logger.error(f"Failed to list runs (agent={agent_id}, user={user_id}): {e}")
            return {"items": [], "total": 0}

    # Group key: chat turns of one conversation share conversation_id (mig
    # 331); everything else (issue turns, workflow runs, vision batches …)
    # has conversation_id NULL and stays a group of one keyed by its own id.
    # ``'conv:' || NULL`` is NULL, so COALESCE falls through to the run id.
    @staticmethod
    def _group_key_sql(alias: str = "") -> str:
        p = f"{alias}." if alias else ""
        return f"COALESCE('conv:' || {p}conversation_id::text, 'run:' || {p}id::text)"

    async def list_groups_by_agent(
        self,
        *,
        agent_id: UUID,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Conversation-grouped runs page for the Runs tab. One item per
        conversation (chat) or per run (everything else), newest activity
        first, with per-group token/cost rollups and the latest run's display
        fields for the list card. Returns {"items": [...], "total": N} where
        total counts GROUPS, not runs.

        ``title`` is the group's DISPLAY NAME, projected here rather than
        derived on the client from ``latest_output_summary``. That summary is
        the tail of whatever the agent produced, so on this data it is prose
        only by luck — measured against prod: 44 of 99 runs have no summary at
        all and another 20 end in a JSON payload, i.e. two thirds of the list
        could only ever render the client's neutral fallback. Hence the chain
        below, ordered by how directly each source names the conversation:

          1. ``conversations.title`` — what the chat is called everywhere else
             in the app, and (for issue-born conversations) already a copy of
             the issue title. Covers every ``conv:`` group.
          2. ``issues.title`` via ``agent_runs.issue_id`` — for issue-born runs
             that never got a conversation row.
          3. First non-empty, non-JSON user message, capped at 60 chars — the
             ask, when a conversation somehow carries no title.

        NULL when none apply (pipeline runs: storyboard, captioning, vision —
        those have no conversation to name). The client owns the neutral
        fallback label so it stays translatable."""
        page_sql = text(
            f"""
            WITH g AS (
                SELECT
                    {self._group_key_sql()}                     AS group_key,
                    MAX(conversation_id)::text                  AS conversation_id,
                    COUNT(*)::int                               AS run_count,
                    COALESCE(SUM(prompt_tokens), 0)::bigint     AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0)::bigint AS completion_tokens,
                    -- 自身列：父行的 cost_cents 已折进后代的花费，而同一个分组里
                    -- 通常也有那些子 run 的行 —— 求和就把它们数两遍。
                    SUM(COALESCE(own_cost_cents, 0))            AS cost_cents,
                    MIN(started_at)                             AS first_started_at,
                    MAX(started_at)                             AS last_started_at,
                    BOOL_OR(status = 'running')                 AS any_running,
                    COUNT(*) FILTER (
                        WHERE status IN ('failed', 'heartbeat_lost')
                    )::int                                      AS error_count
                FROM agent_runs
                WHERE agent_id = :agent_id AND user_id = :user_id
                GROUP BY 1
            )
            SELECT g.*,
                   r.id::text        AS latest_run_id,
                   r.status          AS latest_status,
                   r.trigger         AS trigger,
                   r.model           AS model,
                   r.output_summary  AS latest_output_summary,
                   r.error_code      AS latest_error_code,
                   r.ended_at        AS latest_ended_at,
                   COALESCE(
                       NULLIF(btrim(c.title), ''),
                       NULLIF(btrim(i.title), ''),
                       m.first_user_text
                   )                 AS title
            FROM g
            JOIN LATERAL (
                SELECT id, status, trigger, model, output_summary,
                       error_code, ended_at, issue_id
                FROM agent_runs r2
                WHERE r2.agent_id = :agent_id AND r2.user_id = :user_id
                  AND {self._group_key_sql('r2')} = g.group_key
                ORDER BY r2.started_at DESC
                LIMIT 1
            ) r ON TRUE
            -- g.conversation_id is MAX(...)::text (kept as text so BIGINT
            -- snowflakes survive the JSON hop); cast back for the join.
            LEFT JOIN conversations c ON c.id = g.conversation_id::bigint
            LEFT JOIN issues i ON i.id = r.issue_id
            LEFT JOIN LATERAL (
                SELECT CASE
                           WHEN length(btrim(msg.body ->> 'text')) > {_TITLE_MAX_LEN}
                           THEN left(btrim(msg.body ->> 'text'), {_TITLE_MAX_LEN - 1}) || '…'
                           ELSE btrim(msg.body ->> 'text')
                       END AS first_user_text
                FROM messages msg
                WHERE msg.conversation_id = g.conversation_id::bigint
                  AND msg.sender_type = 'user'
                  AND msg.deleted_at IS NULL
                  AND btrim(COALESCE(msg.body ->> 'text', '')) <> ''
                  -- A pasted payload names the chat no better than a run id.
                  AND left(btrim(msg.body ->> 'text'), 1) NOT IN ('{{', '[')
                ORDER BY msg.seq ASC
                LIMIT 1
            ) m ON TRUE
            ORDER BY g.last_started_at DESC
            LIMIT :limit OFFSET :offset
            """
        )
        total_sql = text(
            f"""
            SELECT COUNT(DISTINCT {self._group_key_sql()})
            FROM agent_runs
            WHERE agent_id = :agent_id AND user_id = :user_id
            """
        )
        params = {"agent_id": agent_id, "user_id": user_id}
        try:
            async with read_scope() as session:
                total = await session.scalar(total_sql, params)
                result = await session.execute(
                    page_sql, {**params, "limit": limit, "offset": offset}
                )
                items = [dict(row) for row in result.mappings().all()]
            return {"items": items, "total": int(total or 0)}
        except Exception as e:
            logger.error(
                f"Failed to list run groups (agent={agent_id}, user={user_id}): {e}"
            )
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
        user's runs (the user-facing usage page); None = all users (admin).

        ``cost_cents`` 求的是 ``own_cost_cents``（键不变）：旧列是「自身 + 已报到的
        后代」，子 run 于是被数两遍，而且父行那笔折叠额还挂在**父那次调用的模型**
        上——按模型分组时连归属都是错的。"""
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
                    func.coalesce(
                        func.sum(func.coalesce(AgentRuns.own_cost_cents, 0)), 0
                    ).label("cost_cents"),
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
        started_before: Optional[datetime] = None,
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
        to model.

        ``cost_cents`` 求的是 ``own_cost_cents``（键不变）——同
        ``daily_usage_by_model``：旧列把子 run 的花费在父行里再数一遍。"""
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
                    func.coalesce(
                        func.sum(func.coalesce(AgentRuns.own_cost_cents, 0)), 0
                    ).label("cost_cents"),
                )
                .where(AgentRuns.started_at >= started_after)
                .group_by(day, key_col)
                .order_by(day.asc())
            )
            if started_before is not None:
                stmt = stmt.where(AgentRuns.started_at < started_before)
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

    async def request_pause(
        self, run_id: str, *, user_id: Optional[UUID] = None
    ) -> bool:
        """Set pause_requested=true on a RUNNING row (phase 2a target-level
        pause; ``PauseHook`` observes it at the next step boundary). Same
        shape as ``request_cancel``, but ``user_id`` is optional: a pause is
        authorised at the TARGET (issue visibility — a team member may pause
        a run the issue's owner started), so the row's own owner is not the
        gate. Pass it only when the caller IS gating on run ownership."""
        try:
            async with write_scope() as session:
                stmt = (
                    update(AgentRuns)
                    .where(AgentRuns.id == self._bigint(run_id))
                    .where(AgentRuns.status == "running")
                    .values(pause_requested=True)
                )
                if user_id is not None:
                    stmt = stmt.where(AgentRuns.user_id == user_id)
                result = await session.execute(stmt)
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.error(f"Failed to request pause for run {run_id}: {e}")
            return False

    async def clear_pause_request(self, run_id: str) -> bool:
        """Withdraw a pause the run has not observed yet (resume landed before
        the next step boundary): the run simply keeps going. Only a RUNNING
        row can still observe the flag, so only that is touched."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(AgentRuns)
                    .where(AgentRuns.id == self._bigint(run_id))
                    .where(AgentRuns.status == "running")
                    .values(pause_requested=False)
                )
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.error(f"Failed to clear pause request for run {run_id}: {e}")
            return False

    async def claim_undo(self, run_id: str, *, user_id: UUID) -> str:
        """Run 级撤销的一次性认领（mig 415）。单条 CAS：undone_at 从 NULL
        置 now() 即认领成功；先 claim 后执行是刻意的——两个并发 undo 把
        scene inverse 应用两次比「崩溃后无法重试」更糟（宁可少撤不可重撤）。
        返回 'claimed' | 'already_undone' | 'running' | 'not_found'。"""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(AgentRuns)
                    .where(AgentRuns.id == self._bigint(run_id))
                    .where(AgentRuns.user_id == user_id)
                    .where(AgentRuns.undone_at.is_(None))
                    .where(AgentRuns.status != "running")
                    .values(undone_at=func.now())
                )
                if (result.rowcount or 0) > 0:
                    return "claimed"
        except Exception as e:
            logger.error(f"Failed to claim undo for run {run_id}: {e}")
            return "not_found"
        row = await self.get_by_id(run_id, user_id=user_id)
        if not row:
            return "not_found"
        if row.get("undone_at"):
            return "already_undone"
        if row.get("status") == "running":
            return "running"
        return "not_found"  # pragma: no cover — 竞态兜底

    # ------------------------------------------------------------------
    # Issue-linked run housekeeping (A2, needs_input first-class design §5.1)
    # ------------------------------------------------------------------

    async def backfill_issue_id(self, run_id: str, issue_id: int) -> None:
        """Best-effort: stamp ``agent_runs.issue_id`` on a run that already
        finished, keyed by ``run_id``.

        RunRecorder has supported an ``issue_id`` constructor kwarg since
        mig-208 (see ``test_run_recorder_issue_id.py``), but no
        issue-dispatch/reply caller ever threads it through at construction
        time — every issue-linked agent_runs row starts NULL. Rather than
        widen ``RunRecorder``'s call site (shared by every other trigger),
        this backfills post-hoc using the ``run_id`` the turn's own result
        dict already carries. ``WHERE issue_id IS NULL`` makes it idempotent
        and never clobbers a value some other write already set. Never
        raises — this is decoration, not the primary status-routing op."""
        try:
            async with write_scope() as session:
                await session.execute(
                    update(AgentRuns)
                    .where(AgentRuns.id == self._bigint(run_id))
                    .where(AgentRuns.issue_id.is_(None))
                    .values(issue_id=int(issue_id))
                )
        except Exception as e:
            logger.error(
                f"[agent_runs] backfill_issue_id failed "
                f"(run={run_id} issue={issue_id}): {e}"
            )

    async def mark_empty_output(self, run_id: str, *, error_message: str) -> None:
        """Type a zero-content, no-outcome run as a typed EMPTY_OUTPUT
        failure (A2) instead of leaving it looking like an ordinary
        ``completed`` row.

        Deliberately does NOT touch ``liveness_state``. Every other writer of
        ``liveness_state='dead'`` — ``liveness_scanner._mark_dead`` and
        ``liveness/reconcile.reconcile_stranded_runs`` — pairs it atomically
        with ``status='failed'`` (migration 207's column comment documents
        this as the intended contract: dead means "the process actually
        died", a different ops playbook from "the model returned nothing").
        This run's ``status`` stays ``'completed'`` (RunRecorder already
        closed it that way, successfully, before this method ever runs), so
        writing ``liveness_state='dead'`` here would mint a never-before-seen
        ``status='completed' + liveness_state='dead'`` combo and pollute that
        invariant for ops triage. The EMPTY_OUTPUT typing is already fully
        carried by ``error_code``/``error_message`` — no liveness_state write
        needed. (The "liveness_state stays 'running' after completion" gap
        this docstring used to flag as needing its own terminal value is now
        closed: migration 406 added ``'finished'`` and ``RunRecorder._finish``
        writes it. This row is already ``'finished'`` by the time we get here
        — which is correct, the run DID complete; the EMPTY_OUTPUT typing
        lives on the error columns, not on liveness.) Never raises —
        best-effort annotation on an already-finished row."""
        try:
            async with write_scope() as session:
                await session.execute(
                    update(AgentRuns)
                    .where(AgentRuns.id == self._bigint(run_id))
                    .values(
                        error_code="EMPTY_OUTPUT",
                        error_message=error_message,
                    )
                )
        except Exception as e:
            logger.error(f"[agent_runs] mark_empty_output failed (run={run_id}): {e}")

    async def running_root_run_id(
        self, *, issue_id: Optional[int] = None, conversation_id: Optional[int] = None
    ) -> Optional[int]:
        """The id of a ROOT run (parent_run_id IS NULL) currently running on
        this issue or its session conversation, else None. ``issue_id`` is
        backfilled after the turn, so the conversation is the live key."""
        if issue_id is None and conversation_id is None:
            return None
        keys = []
        if issue_id is not None:
            keys.append(AgentRuns.issue_id == int(issue_id))
        if conversation_id is not None:
            keys.append(AgentRuns.conversation_id == int(conversation_id))
        try:
            async with read_scope() as session:
                row = (
                    await session.execute(
                        select(AgentRuns.id)
                        .where(AgentRuns.status == "running")
                        .where(AgentRuns.parent_run_id.is_(None))
                        .where(or_(*keys))
                        .order_by(AgentRuns.started_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            return int(row) if row is not None else None
        except Exception as e:
            # A failed read must not read as "nothing running": callers pause,
            # resume or divert on this answer. Raise; they type the failure.
            logger.error(f"[agent_runs] running_root_run_id failed: {e}")
            raise

    async def list_transcript_events(
        self,
        run_id: int,
        *,
        upto_seq: Optional[int] = None,
        event_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """``{seq, event_type, payload}`` rows of one run in seq order (mig
        397 ``agent_run_transcript_events``). ``upto_seq`` is INCLUSIVE;
        ``event_types`` narrows to those types. Phase 2b-1: the one read the
        fork service and the ``view-at`` endpoint share — do not write a
        third copy of this query."""
        from app.models import AgentRunTranscriptEvents as TE

        stmt = (
            select(TE.seq, TE.event_type, TE.payload)
            .where(TE.run_id == int(run_id))
            .order_by(TE.seq.asc())
        )
        if upto_seq is not None:
            stmt = stmt.where(TE.seq <= int(upto_seq))
        if event_types:
            stmt = stmt.where(TE.event_type.in_(list(event_types)))
        async with read_scope() as session:
            rows = (await session.execute(stmt)).mappings().all()
        return [
            {
                "seq": int(r["seq"]),
                "event_type": r["event_type"],
                "payload": r["payload"],
            }
            for r in rows
        ]

    async def last_transcript_seq(self, run_id: int) -> Optional[int]:
        """这条 run 的 transcript 水位（``MAX(seq)``），没有事件就是 None。

        前端按它丢重复与乱序的回合结束信号（3b §4）。「没有事件」与「seq 0」
        是两件事，所以空表回 None。"""
        from app.models import AgentRunTranscriptEvents as TE

        async with read_scope() as session:
            return (
                await session.execute(
                    select(func.max(TE.seq)).where(TE.run_id == int(run_id))
                )
            ).scalar_one_or_none()

    async def list_forks(self, run_id: int) -> List[Dict[str, Any]]:
        """Runs whose ``fork_of_run_id`` is this run (mig 453), oldest first.
        Phase 2b-1 ``GET /runs/{id}/forks``."""
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(
                            AgentRuns.id,
                            AgentRuns.fork_at_seq,
                            AgentRuns.created_at,
                            AgentRuns.status,
                        )
                        .where(AgentRuns.fork_of_run_id == int(run_id))
                        .order_by(AgentRuns.created_at.asc())
                    )
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    async def spent_cents_for_issue(
        self,
        *,
        issue_id: Optional[int] = None,
        conversation_id: Optional[int] = None,
        exclude_run_id: Optional[int] = None,
    ) -> float:
        """Sum of ``own_cost_cents`` over the issue's runs (by issue_id or its
        session conversation), optionally excluding the live run whose spend the
        caller tracks itself.

        **Every run, root and child alike** — ``own_cost_cents`` records only
        the row's own spend (own + media, no descendants), so the whole issue's
        真花的钱 is the sum over all of them. See ``_own_cost_sum_stmt``.
        """
        keys = _issue_scope_keys(issue_id, conversation_id)
        if not keys:
            return 0.0
        stmt = _own_cost_sum_stmt(or_(*keys))
        if exclude_run_id is not None:
            stmt = stmt.where(AgentRuns.id != int(exclude_run_id))
        try:
            async with read_scope() as session:
                return float((await session.execute(stmt)).scalar_one() or 0)
        except Exception as e:
            # A spend gate must not read a failed SUM as "nothing spent":
            # raise, and let the caller decide (budget hook: fail open once,
            # logged; Top up answer: typed 503).
            logger.error(f"[agent_runs] spent_cents_for_issue failed: {e}")
            raise

    async def own_cost_cents_for_issue_runs(
        self, issue_id: int, conversation_id: Optional[int] = None
    ) -> float:
        """这个议题真花了多少钱 —— ``issue_rollup`` 的 Budget 格读它。

        与 ``spent_cents_for_issue`` **完全同一条语句**（同一组 OR 键 +
        同一条 SUM 表达式），只是不带 ``exclude_run_id``：驾驶舱要的是此刻的全额，
        包括正在跑的那条（镜像语句每次都把 ``own_cost_cents`` 写成实时值，所以
        running 的行也算得准）。

        ``conversation_id`` 必须跟着传（``load_rollup`` 手里就有 ``ai_session_id``）：
        只按 ``issue_id`` 找会漏掉只走会话键的 run —— 那条 run 会出现在 run 列表和
        预算门禁的总额里，却不进 Budget 格，而三处的 docstring 都写着口径一致。

        读失败一律 raise：rollup 的调用方自己决定怎么降级，这里回 0 就等于把一个
        花了钱的议题显示成没花钱，而那正是预算格存在的意义。
        """
        keys = _issue_scope_keys(issue_id, conversation_id)
        if not keys:
            return 0.0
        stmt = _own_cost_sum_stmt(or_(*keys))
        try:
            async with read_scope() as session:
                return float((await session.execute(stmt)).scalar_one() or 0)
        except Exception as e:
            logger.error(f"[agent_runs] own_cost_cents_for_issue_runs failed: {e}")
            raise

    async def efficiency_for_issue(self, issue_id: int) -> Dict[str, Any]:
        """这个议题上所有 run 的工作量（3c §3.3）。

        **不**加 root 过滤：五个计数是每个 run 的自身量，父行不含子行，全体求和才是
        真数（``spent_cents_for_issue`` 自 3d 第 0 票起同理——它改读只记自身的
        ``own_cost_cents``，也不再 root-only）。一条 SQL 按
        ``turn_end_reason`` 分组，总量在 Python 侧加起来——分布与总量同源。

        ⚠️ 「全体」的准确口径是**带着这个 ``issue_id`` 的全部 run**，不是「这棵树的
        全部 run」——这里的 WHERE 只认这一列，树结构（``root_run_id``）根本没进查询。
        两者相等的前提是每个派发站点都在 INSERT 时戳上议题；3c 终审 I1 抓到
        ``workforce/agent_worker`` 漏了这一戳，于是委派出去的活整个不计，而
        ``compute_rollup`` 的 ``¢/output`` 分子（root 的树总额，含子的钱）照算不误，
        单价系统性偏高。**加新的派发站点时，issue_id 与 team_id 一样是必戳项。**

        ``avg_run_ms`` 的分母只数两端时间戳都有的 run；一个都没有 → None（不知道，
        不是 0 毫秒）。读失败返回 {}：驾驶舱少两个格子，不该把整个议题页拖垮。
        """
        try:
            timed = and_(
                AgentRuns.started_at.isnot(None), AgentRuns.ended_at.isnot(None)
            )
            stmt = (
                select(
                    AgentRuns.turn_end_reason.label("reason"),
                    func.count().label("runs"),
                    func.coalesce(func.sum(AgentRuns.steps), 0).label("steps"),
                    func.coalesce(func.sum(AgentRuns.tool_calls), 0).label(
                        "tool_calls"
                    ),
                    func.coalesce(func.sum(AgentRuns.tool_errors), 0).label(
                        "tool_errors"
                    ),
                    func.coalesce(func.sum(AgentRuns.deliverables), 0).label(
                        "deliverables"
                    ),
                    func.count().filter(timed).label("timed_runs"),
                    # FILTER 挂在 ``sum`` 上，不是挂在 ``extract`` 上——Postgres
                    # 的 FILTER 只对聚合函数合法，挂错位置 SQLAlchemy 在建语句时
                    # 就 AttributeError，而那一抛正好落进下面的 except，整个效率账
                    # 会永远静默返回 {}。``test_efficiency_sql_compiles_and_folds``
                    # 钉住这个形状。
                    func.coalesce(
                        func.sum(
                            func.extract(
                                "epoch", AgentRuns.ended_at - AgentRuns.started_at
                            )
                        ).filter(timed),
                        0,
                    ).label("total_seconds"),
                )
                .where(AgentRuns.issue_id == int(issue_id))
                .group_by(AgentRuns.turn_end_reason)
            )
            async with read_scope() as session:
                rows = (await session.execute(stmt)).mappings().all()
        except Exception as e:
            logger.error(f"[agent_runs] efficiency_for_issue({issue_id}) failed: {e}")
            return {}
        out: Dict[str, Any] = {
            "runs": 0,
            "steps": 0,
            "tool_calls": 0,
            "tool_errors": 0,
            "deliverables": 0,
            "turn_end_reasons": {},
        }
        timed_runs, total_seconds = 0, 0.0
        for row in rows:
            out["runs"] += int(row["runs"])
            for key in ("steps", "tool_calls", "tool_errors", "deliverables"):
                out[key] += int(row[key] or 0)
            timed_runs += int(row["timed_runs"] or 0)
            total_seconds += float(row["total_seconds"] or 0.0)
            if row["reason"]:
                out["turn_end_reasons"][str(row["reason"])] = int(row["runs"])
        out["avg_run_ms"] = (
            int(total_seconds * 1000 / timed_runs) if timed_runs else None
        )
        return out

    async def list_for_issue(
        self,
        *,
        issue_id: Optional[int] = None,
        conversation_id: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Root runs on an issue (by issue_id or its session conversation),
        newest first, with the folded ``metadata_json`` views. ``id`` /
        ``parent_run_id`` stay native int (issue.rollup stringifies)."""
        keys = []
        if issue_id is not None:
            keys.append(AgentRuns.issue_id == int(issue_id))
        if conversation_id is not None:
            keys.append(AgentRuns.conversation_id == int(conversation_id))
        if not keys:
            return []
        cols = (
            AgentRuns.id,
            AgentRuns.status,
            AgentRuns.trigger,
            AgentRuns.model,
            AgentRuns.started_at,
            AgentRuns.ended_at,
            AgentRuns.cost_cents,
            AgentRuns.error_code,
            AgentRuns.metadata_json,
            AgentRuns.agent_id,
        )
        try:
            async with read_scope() as session:
                rows = (
                    (
                        await session.execute(
                            select(*cols)
                            .where(or_(*keys))
                            .where(AgentRuns.parent_run_id.is_(None))
                            .order_by(AgentRuns.started_at.desc())
                            .limit(limit)
                        )
                    )
                    .mappings()
                    .all()
                )
            out = []
            for r in rows:
                d = dict(r)
                d["cost_cents"] = (
                    float(d["cost_cents"]) if d.get("cost_cents") is not None else None
                )
                d["agent_id"] = (
                    str(d["agent_id"]) if d.get("agent_id") is not None else None
                )
                out.append(d)
            return out
        except Exception as e:
            logger.error(f"[agent_runs] list_for_issue failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Efficiency read face (3c §3.3)
    # ------------------------------------------------------------------

    async def efficiency_groups(
        self,
        *,
        frm: datetime,
        to: datetime,
        user_id: Optional[UUID] = None,
        team_id: Optional[int] = None,
        project_id: Optional[int] = None,
        group_by: str = "model",
    ) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
        """按 model 或 agent 分组的效率原料 + 全窗口的 turn_end 分布。

        三个 scope 参数至少一个非空由调用方保证（user 锁调用者，team/project 过成员
        门）。这里**不兜底**——一个没有任何 where 的跨团队全表聚合，是这条链上最贵也
        最危险的查询。返回原始计数而不是比率：分母语义（0 次调用 vs 0 件产出）在路由
        层统一处理一次，两处各算一遍必然漂移。读失败一律 raise（路由转 503）——空结果
        会被读成「这段时间没花钱」。

        **两种粒度混在一张表里，每一列的口径写死在这里（同 ``issue_totals``）：**

        - ``cost_cents`` —— **整棵树的钱，记在 root 所在的那一组**。取数分两步：子查询
          按 ``COALESCE(root_run_id, id)`` 把 ``own_cost_cents`` 预聚合成每棵树一行，
          主查询左联回来，只在 root 行上求和（``FILTER (WHERE parent_run_id IS NULL)``）。
          直接对分组里的行求 ``own_cost_cents`` 会**改掉归属口径**（见下面的 I5 注），
          而读旧的 ``cost_cents`` 则会低报：那一列是「自身 + **已报到的**后代」，子 run
          没报回父行时它不含那笔钱 —— Delegate 出去的花费于是整个漏出这张表（3d 第 0
          票，与 ``usage_repository.issue_totals`` / ``spent_cents_for_issue`` 同批迁）。

          ⚠️ **子查询收行的条件是「属于某棵在 scope 内的树」，不是每行自己的 scope 列**
          （裁定 7）。``scope`` 与 root 谓词只作用在内层那个 ``in_scope_roots`` 选择上，
          子查询自己那层**不带任何**行级 scope 过滤。理由：root 才是被授权、被 scope 的
          那个对象，整棵树归属于它 —— 与 ``tree_cost_cents`` / ``tree_charge`` 同一套
          语义；而子 run 的 scope 列是 best-effort（``team_of_run`` 查不到就降级成
          ``None``，``DispatchScope`` 同理），子行的 ``team_id`` 完全可以是 NULL 而它的
          root 有值。按子行自己的列过滤的后果是**静默的**：root 在 scope 里、join 也落
          得下来，但那条子 run 的钱压根没进 ``tree_cost.cents``，树总额少掉委派那笔。

          这个选择定下的两个边界，方向相反，都是有意的：
          1. 子 run 在**窗口外**、它的 root 在窗口内 → **算**。跟旧的折叠列行为一致
             （root 收口时把后代的钱折进来，不问后代是什么时候跑的）。尾窗口尤其常见：
             root 在 ``to`` 附近起，孩子在它之后才结束。
          2. 子 run 在窗口内、它的 root 在**窗口外** → **不算**。那棵树的 root 行不在
             主查询里，join 无处落地。这是「钱按 root 归属」的必然代价。
        - ``run_count`` / ``failed_runs`` / ``tool_calls`` / ``tool_errors`` /
          ``deliverables`` —— **root + children 全算**。这些是每个 run **自身**的量，
          不上滚；按 root 过滤会把子 run 干的活整个丢掉。
          ⚠️ 所以这个 ``run_count`` 与 ``ai_usage_hourly.run_count`` **不是同一个口径**
          （小时表按计费事件累加），两处数字对不上是预期的，不是漂移。
        - ``avg_run_ms`` —— 样本里父子混在一起。一个 root run 的墙钟覆盖它孩子的墙钟，
          所以这是「一次运行平均多久」而不是「独立工作量平均多久」，两者在有子 run 的
          agent 上会显著不同。

        所以 root 谓词写成聚合上的 ``FILTER (WHERE ...)``，不写进 ``WHERE``。

        ⚠️ **两种粒度混在一张分组表里的后果**（3c 终审 I5）：分组键取自每个 run 自己
        的 model / agent，而钱只从 root 行来。一次委派里父用 A 模型、子用 B 模型时，
        **整棵树的钱进 A 组，子的产出进 B 组** —— B 组于是拿到 ``cost_cents = 0`` 且
        ``deliverables > 0``。这不是缺陷而是两种粒度的必然结果（钱按树滚、活按 run
        数），但它会让「单价」这一列说出谎话，所以路由层在 ``cost == 0 and
        delivered > 0`` 时回 null 而不是 0.0。顶栏 tile 逐组求和后不受影响，受影响
        的只有分组表本身。
        """
        key_col = AgentRuns.agent_id if group_by == "agent" else AgentRuns.model
        root_only = AgentRuns.parent_run_id.is_(None)
        scope = [AgentRuns.created_at >= frm, AgentRuns.created_at < to]
        if user_id is not None:
            scope.append(AgentRuns.user_id == user_id)
        if team_id is not None:
            scope.append(AgentRuns.team_id == int(team_id))
        if project_id is not None:
            scope.append(AgentRuns.project_id == int(project_id))
        # A run only has a duration once BOTH endpoints are stamped; the FILTER
        # keeps half-stamped rows out of the average's denominator instead of
        # letting them read as "instant".
        timed = and_(AgentRuns.started_at.isnot(None), AgentRuns.ended_at.isnot(None))
        # 钱：先把整棵树的 own_cost_cents 加成每棵树一行，再左联回 root 行所在的分组。
        #
        # 收行的条件是**「属于某棵在 scope 内的树」**，不是每行自己的 scope 列（裁定 7）。
        # 子 run 的 scope 列是 best-effort：``team_of_run`` 查不到就降级成 None，
        # ``DispatchScope`` 同理，所以子行的 team_id / project_id 可以是 NULL 而它的
        # root 有值。按子行自己的列过滤 = 把委派那笔钱静默丢掉，而那正是本票要捞回来的。
        tree_key = func.coalesce(AgentRuns.root_run_id, AgentRuns.id)
        in_scope_roots = select(AgentRuns.id).where(*scope, root_only)
        tree_cost = (
            select(
                tree_key.label("root"),
                func.sum(func.coalesce(AgentRuns.own_cost_cents, 0)).label("cents"),
            )
            .where(tree_key.in_(in_scope_roots))
            .group_by(tree_key)
            .subquery("tree_cost")
        )
        try:
            rows_stmt = (
                select(
                    key_col.label("key"),
                    func.count().label("run_count"),
                    func.coalesce(
                        func.sum(
                            case(
                                (AgentRuns.status.in_(("failed", "heartbeat_lost")), 1),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("failed_runs"),
                    func.count().filter(timed).label("timed_runs"),
                    func.coalesce(
                        func.sum(
                            func.extract(
                                "epoch", AgentRuns.ended_at - AgentRuns.started_at
                            )
                        ).filter(timed),
                        0,
                    ).label("total_seconds"),
                    func.coalesce(func.sum(AgentRuns.tool_calls), 0).label(
                        "tool_calls"
                    ),
                    func.coalesce(func.sum(AgentRuns.tool_errors), 0).label(
                        "tool_errors"
                    ),
                    func.coalesce(func.sum(AgentRuns.deliverables), 0).label(
                        "deliverables"
                    ),
                    # root 行至多联到一行（子查询已按树分好组），所以这个 join 不会
                    # 放大上面那几个回合粒度的计数。
                    func.coalesce(
                        func.sum(tree_cost.c.cents).filter(root_only), 0
                    ).label("cost_cents"),
                )
                .select_from(AgentRuns)
                .outerjoin(tree_cost, tree_cost.c.root == AgentRuns.id)
                .where(*scope)
                .group_by(key_col)
            )
            reasons_stmt = (
                select(AgentRuns.turn_end_reason, func.count())
                .where(*scope)
                .where(AgentRuns.turn_end_reason.isnot(None))
                .group_by(AgentRuns.turn_end_reason)
            )
            async with read_scope() as session:
                rows = (await session.execute(rows_stmt)).mappings().all()
                reasons = (await session.execute(reasons_stmt)).all()
        except Exception as e:
            logger.error(f"[agent_runs] efficiency_groups failed: {e}")
            raise
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["key"] = "" if d["key"] is None else str(d["key"])
            d["total_ms"] = int(float(d.pop("total_seconds") or 0.0) * 1000)
            out.append(d)
        return out, {str(k): int(v) for k, v in reasons}

    async def cost_rows_for_ids(self, ids: List[int]) -> List[Dict[str, Any]]:
        """这批 run 的归属列与行级展示列。可见性判定在路由层——仓库不认识调用者。

        ⚠️ 这里的 ``cost_cents`` 是**行上那一列**（自身 + 已报到的后代），只够单行展示。
        气泡与 ``done`` 帧回答的「这次回合花了多少」走 ``tree_cost_cents``（3d 第 0 票）
        —— 别把这一列当成树总额加起来。
        """
        if not ids:
            return []
        stmt = select(
            AgentRuns.id,
            AgentRuns.user_id,
            AgentRuns.issue_id,
            AgentRuns.cost_cents,
            AgentRuns.model,
            AgentRuns.status,
            AgentRuns.prompt_tokens,
            AgentRuns.completion_tokens,
        ).where(AgentRuns.id.in_([int(i) for i in ids]))
        try:
            async with read_scope() as session:
                return [dict(r) for r in (await session.execute(stmt)).mappings().all()]
        except Exception as e:
            logger.error(f"[agent_runs] cost_rows_for_ids failed: {e}")
            raise

    async def run_ids_in_trees(self, root_ids: List[int]) -> Dict[str, List[str]]:
        """每个 root run id → 以它为根的整棵树的全部 run id（含它自己），都是字符串。

        扣费是**逐 run** 发生的（``token_billing`` 对每条 run 各 ceil 一次，见那里的
        注释），所以「这次回合扣了多少」的答案分散在整棵树上。3c 终审 I2：两个消耗行
        宿主此前只问 root 自己那一条流水，真栈一次回合 6 条、余额 −6，界面显示 ◇ 1.00。

        ``root_run_id`` 在子 run 上指向根、在 root 行上是 NULL（``_attach_to_parent_run``
        是唯一写方），所以一条 ``root_run_id IN (:roots) OR id IN (:roots)`` 就取全。

        每个问到的 id **至少映射到它自己**，即使它的行读不回来 —— 一次读空不该把一个
        真扣过钱的 run 显示成免费。⚠️ 问一个**中间**节点只会拿回它自己：它的孙子
        ``root_run_id`` 指向真正的根而不是它。两个宿主问的都是 root，这是已知边界。

        读失败一律 raise：降级成「只有 root」等于把 I2 那个低报又悄悄装回去。三个消费方
        各自已经有 catch。
        """
        roots = [int(r) for r in root_ids if r is not None]
        if not roots:
            return {}
        out: Dict[str, set[str]] = {str(r): {str(r)} for r in roots}
        stmt = select(AgentRuns.id, AgentRuns.root_run_id).where(
            or_(AgentRuns.root_run_id.in_(roots), AgentRuns.id.in_(roots))
        )
        try:
            async with read_scope() as session:
                rows = (await session.execute(stmt)).all()
        except Exception as e:
            logger.error(f"[agent_runs] run_ids_in_trees failed: {e}")
            raise
        for run_id, root_run_id in rows:
            # root 行自己的 ``root_run_id`` 是 NULL；一个被问到的中间节点的
            # ``root_run_id`` 指向一个**没被问到**的根，那种行归到它自己名下。
            key = str(root_run_id) if str(root_run_id) in out else str(run_id)
            if key in out:
                out[key].add(str(run_id))
        return {k: sorted(v) for k, v in out.items()}

    async def tree_cost_cents(self, root_ids: List[int]) -> Dict[str, float]:
        """每个 root → 整棵树的真实花费（Σ ``own_cost_cents``），字符串键。

        树键 ``COALESCE(root_run_id, id)``：root 行自己的 ``root_run_id`` 恒为 NULL
        （``_attach_to_parent_run`` 是唯一写方），所以这一个表达式同时覆盖根与后代，
        与 ``run_ids_in_trees`` 答的是同一棵树。**全仓只许这一种拼法。**

        为什么不直接读 root 行的 ``cost_cents``：那一列是「自身 + **已报到的**后代」，
        由 ``run_recorder._finish`` 在父 run 收口时折叠出来（3d 第 0 票）。子 run 还没
        报回父行、或者根本没报（失败 / 被取消）时它低报，而它同时又不能被求和 ——
        子 run 自己还有一行。``own_cost_cents`` 每行只记自身，求和既不低报也不双计。

        每个问到的 root **至少映射到它自己**（0.0）—— 读空不等于免费，键整个缺席会让
        调用方 ``.get`` 拿到 None 再 ``?? 0``，把一次读空写成一个关于钱的断言。

        ⚠️ **只许拿 root 来问。** 一条子 run 的树键指向它的根而不是它自己，所以问一个
        非 root 的 id 会拿到 0.0 —— 那是「这个 id 不是任何一棵树的根」，不是「它没花
        钱」。同族的已知边界见 ``run_ids_in_trees``（那里问中间节点只拿回它自己）。两个
        宿主（``/runs/costs`` 与 ``done`` 帧）问的都是 root：气泡与状态帧都由 root 的
        recorder 发。加第三个消费方前先确认它手里那个 id 是根。

        与 ``tree_charge.bucket_tree`` 的 ``tree_total`` 是同一个数。读失败一律 raise：
        降级成 0 就是把一次故障说成「这次回合免费」，``/runs/costs`` 正靠这个异常答 503。
        """
        roots = [int(r) for r in root_ids if r is not None]
        if not roots:
            return {}
        tree_key = func.coalesce(AgentRuns.root_run_id, AgentRuns.id)
        stmt = (
            select(
                tree_key.label("root"),
                func.sum(func.coalesce(AgentRuns.own_cost_cents, 0)).label("cents"),
            )
            .where(tree_key.in_(roots))
            .group_by(tree_key)
        )
        out = {str(r): 0.0 for r in roots}
        try:
            async with read_scope() as session:
                for root, cents in (await session.execute(stmt)).all():
                    out[str(root)] = round(float(cents or 0), 4)
        except Exception as e:
            logger.error(f"[agent_runs] tree_cost_cents failed: {e}")
            raise
        return out

    # ------------------------------------------------------------------
    # Sweeper helpers
    # ------------------------------------------------------------------

    async def mark_heartbeat_lost(self, *, stale_before: datetime) -> int:
        """Bulk-flip stuck running rows (heartbeat_at < stale_before) →
        heartbeat_lost. Returns the row count for telemetry (see
        ``mark_heartbeat_lost_ids`` for the ids)."""
        return len(await self.mark_heartbeat_lost_ids(stale_before=stale_before))

    async def mark_heartbeat_lost_ids(self, *, stale_before: datetime) -> list[int]:
        """Same UPDATE, returning the flipped run ids so the sweeper can close
        each transcript with ``turn_end{reason:interrupted}`` (mig 453 spine:
        the event log is replay-complete only if a crashed run still gets its
        terminal event). SET-based, idempotent; committed via write_scope.
        Datetimes bound as ``datetime`` objects, never isoformat strings.

        ⚠️ 这条 UPDATE 同批写 ``turn_end_reason='heartbeat_lost'``（3c 终审 I4），
        而它**先于**调用方的 ``close_interrupted_runs``，后者的补写带
        ``turn_end_reason IS NULL`` 守卫。所以列上是 ``heartbeat_lost``，而
        transcript 事件仍是 ``turn_end{reason:interrupted, detail:heartbeat_lost}``。
        两者不冲突，是两个粒度：事件说「这一轮被腰斩」，列多说了一句「因为心跳
        没了」—— 分布条要的正是后者，否则崩溃类失败与普通中断混成一段。
        """
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
                        turn_end_reason=TurnEndReason.HEARTBEAT_LOST.value,
                    )
                    # 维度随行返回：小时表那一行要 team / project / agent /
                    # model / trigger，全在这张表上（3c 终审 I4）。
                    .returning(
                        AgentRuns.id,
                        AgentRuns.team_id,
                        AgentRuns.project_id,
                        AgentRuns.agent_id,
                        AgentRuns.model,
                        AgentRuns.trigger,
                        AgentRuns.attribution,
                    )
                )
                rows = result.fetchall()
        except Exception as e:
            logger.error(f"Failed to mark heartbeat_lost: {e}")
            return []

        # 被扫掉的 run 永远不会再经过 ``RunRecorder._finish``，所以检索投影与
        # ``ai_usage_hourly`` 的那一行都只能由这里跟上（3c Task 13 评审
        # Important 1 / 终审 I4）。在 ``write_scope()`` 之外：投影读回的必须是
        # 刚提交的那份 status，而这两件事失败都绝不该把已经扫成功的 id 吞掉——
        # 调用方拿这些 id 去补 ``turn_end`` 事件。
        from app.services.liveness.crash_rollup import record_crash_terminal_runs
        from app.services.search.projection import project_run_id_best_effort

        swept = [int(r.id) for r in rows]
        for run_id in swept:
            await project_run_id_best_effort(run_id)
        await record_crash_terminal_runs(rows)
        return swept

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
        compare — stringifying them silently zeroes team/project scopes).

        钱那一列读 ``own_cost_cents``（键仍是 ``cost_cents``，见
        ``_USAGE_COST_COL``）：按 agent 求和的是**这个 agent 自己**烧的钱；父子不同
        agent 时各记各的——这正是按 agent 限额想要的。此前读 ``cost_cents`` 把子 run
        算了两遍（父行的 ``cost_cents`` 已经折进了后代的花费，而子 run 自己那行也在
        同一批结果里）。"""
        try:
            cols = [
                *(getattr(AgentRuns, name) for name in _USAGE_COLS),
                _USAGE_COST_COL,
            ]
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

    # ------------------------------------------------------------------
    # AI Library gallery batch stats (spec 2026-08-02 §B1)
    #
    # Three GROUP BY queries covering EVERY agent the caller can see. They
    # exist so the gallery stops fanning out one /dashboard request per
    # agent (19 round-trips on the current preset roster). Each returns a
    # dict keyed by agent id as str — absent key means "no rows", which the
    # router renders as a zero, so callers never branch on None.
    # ------------------------------------------------------------------

    async def usage_by_agent_since(
        self, agent_ids: List[UUID], since: datetime
    ) -> Dict[str, Dict[str, int]]:
        """{agent_id: {runs, tokens, cost_cents}} for runs started at/after
        ``since``.

        ``cost_cents`` rides the same GROUP BY rather than a second query —
        the gallery and the agent workbench both want "what did this week
        cost", and a separate aggregate would double the round-trips this
        endpoint exists to collapse.

        那一列求的是 ``own_cost_cents``（键不变，router 的 ``cost_cents_7d`` 直接读
        它）：一棵树跨两个 agent 时，旧列让父 agent 的七日花费把子 agent 那份又算
        一遍。
        """
        if not agent_ids:
            return {}
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        AgentRuns.agent_id,
                        func.count().label("runs"),
                        func.coalesce(func.sum(AgentRuns.total_tokens), 0).label(
                            "tokens"
                        ),
                        func.coalesce(
                            func.sum(func.coalesce(AgentRuns.own_cost_cents, 0)), 0
                        ).label("cost_cents"),
                    )
                    .where(AgentRuns.agent_id.in_(agent_ids))
                    .where(AgentRuns.started_at >= since)
                    .group_by(AgentRuns.agent_id)
                )
                return {
                    str(r.agent_id): {
                        "runs": int(r.runs),
                        "tokens": int(r.tokens or 0),
                        "cost_cents": int(r.cost_cents or 0),
                    }
                    for r in result.all()
                }
        except Exception as e:
            logger.error(f"[agent_runs] usage_by_agent_since failed: {e}")
            return {}

    async def running_counts_by_agent(self, agent_ids: List[UUID]) -> Dict[str, int]:
        """{agent_id: live run count}. No time window — 'running' is now."""
        if not agent_ids:
            return {}
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentRuns.agent_id, func.count().label("n"))
                    .where(AgentRuns.agent_id.in_(agent_ids))
                    .where(AgentRuns.status == "running")
                    .group_by(AgentRuns.agent_id)
                )
                return {str(r.agent_id): int(r.n) for r in result.all()}
        except Exception as e:
            logger.error(f"[agent_runs] running_counts_by_agent failed: {e}")
            return {}

    async def latest_run_health_by_agent(
        self, agent_ids: List[UUID], since: datetime
    ) -> Dict[str, Dict[str, Optional[str]]]:
        """{agent_id: {liveness_state, error_code, error_message}} for agents
        whose MOST RECENT finished run ended badly.

        The window is anchored on the *latest* run rather than on "any dead
        run in the window" — that is the whole point of this query. Selecting
        dead rows directly (the pre-2026-08-19 shape) meant one bad run kept
        the gallery's fault badge lit for the entire ``since`` window no
        matter how many successful runs landed afterwards; the badge answered
        "did anything break this week?" when the user reads it as "is this
        agent broken right now". So: take the newest finished run per agent,
        then keep it only if IT is the unhealthy one. A later success (or a
        cancel) wins the DISTINCT ON and the agent drops out of the map.

        ``status != 'running'`` rather than an explicit terminal allowlist —
        an in-flight run must not mask the dead one behind it (``running_count``
        already tells the gallery about live runs), and a future terminal
        status stays covered without editing this filter.

        DISTINCT ON picks that latest row per agent in one pass; pulling every
        run just to take the first would scale with run volume.
        """
        if not agent_ids:
            return {}
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        AgentRuns.agent_id,
                        AgentRuns.liveness_state,
                        AgentRuns.error_code,
                        AgentRuns.error_message,
                    )
                    .where(AgentRuns.agent_id.in_(agent_ids))
                    .where(AgentRuns.status != "running")
                    .where(AgentRuns.started_at >= since)
                    .distinct(AgentRuns.agent_id)
                    .order_by(AgentRuns.agent_id, AgentRuns.started_at.desc())
                )
                # The unhealthy-or-not decision lives here, not in the WHERE,
                # so the DISTINCT ON winner is genuinely "the last run" and a
                # healthy winner can clear the badge.
                return {
                    str(r.agent_id): {
                        "liveness_state": r.liveness_state,
                        "error_code": r.error_code,
                        "error_message": r.error_message,
                    }
                    for r in result.all()
                    if r.liveness_state in ("stuck", "dead")
                }
        except Exception as e:
            logger.error(f"[agent_runs] latest_run_health_by_agent failed: {e}")
            return {}

    # ------------------------------------------------------------------
    # Autopilot quota (M4, mig 395)
    # ------------------------------------------------------------------

    async def count_auto_dispatches_today(self, project_id: str) -> int:
        """Count of this project's AUTO-dispatched agent runs since the start
        of the current UTC day (the autopilot daily-quota counter, design
        spec §2 step 4).

        "Auto" is identified by ``trigger == 'issue_dispatch_auto'`` — a
        distinct value from the manual confirm-gate path's
        ``'issue_dispatch'`` (see ``run_issue_agent``), so a manual "Run now"
        never counts against the quota even though it produces the exact
        same shape of agent_runs row otherwise. Scoped by ``started_at`` (the
        column every other time-window query on this table uses, e.g.
        ``idx_agent_runs_agent_started``) rather than ``created_at`` so a
        row's quota-day matches the moment the run actually started.

        UTC day boundary (never project/user local time — spec §4: "按 UTC
        日,简单一致"). Best-effort: any read failure degrades to 0 (fail
        OPEN on the count so a DB hiccup never permanently blocks autopilot
        into the "over quota" branch) — errors are logged, never raised.
        """
        from datetime import datetime, timezone

        try:
            day_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            async with read_scope() as session:
                total = await session.scalar(
                    select(func.count())
                    .select_from(AgentRuns)
                    .where(AgentRuns.project_id == int(str(project_id)))
                    .where(AgentRuns.trigger == "issue_dispatch_auto")
                    .where(AgentRuns.started_at >= day_start)
                )
            return int(total or 0)
        except Exception as e:
            logger.error(
                f"[agent_runs] count_auto_dispatches_today failed for project "
                f"{project_id}: {e}"
            )
            return 0


# ─── Factory ──────────────────────────────────────────────────────────


def get_agent_runs_repository() -> AgentRunsRepository:
    """Return the AgentRunsRepository.

    Post-rollout there is no flag branch and no engine-missing fallback: the
    repository IS the SQLAlchemy 2.0 ORM implementation. Call sites just do
    ``repo = get_agent_runs_repository()`` and use it directly."""
    return AgentRunsRepository()
