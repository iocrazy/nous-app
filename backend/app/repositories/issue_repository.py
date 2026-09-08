"""Issue repository — data access for the issues table (PR-D1).

CRUD + atomic identifier allocation via the ``issue_create_atomic`` stored
procedure. SQLAlchemy 2.0 ORM implementation (the REST path was retired when the
issue domain was collapsed to ORM-only).

Atomic-create contract:
    - The counter UPDATE and the INSERT must happen in the SAME PG transaction
      (otherwise concurrent callers can take the same identifier and the second
      INSERT will fail the unique index).
    - ``atomic_create`` calls the ``issue_create_atomic(payload jsonb)`` SECURITY
      DEFINER stored procedure (migration 173) inside a ``write_scope()``
      transaction. The procedure performs the counter UPDATE + INSERT in one PG
      txn — if the INSERT fails (RLS / CHECK / FK) the counter rolls back too, so
      there is no MH-N gap. ``atomic_create`` is called from a DBOS workflow
      (scheduled_master), so this commit semantics must stay idempotent-safe.

★★★ UUID CONSUMER AUDIT — THE M-BATCH HOT SPOT (silent authz killers) ★★★
=========================================================================
``issues`` has FOUR uuid columns. Three app-layer call sites compare the
returned ``created_by_user_id`` / ``assignee_user_id`` against a STRING user_id
to make an authz/visibility decision. The ORM returns native ``uuid.UUID`` — and
``UUID(...) == "uuid-string"`` is ALWAYS False with NO error and NO log. That
would 404 the legitimate owner of every issue (read/list/messages/ws). These
columns therefore MUST be str()'d:

  created_by_user_id / assignee_user_id (uuid) → str()'d — REQUIRED (authz ==):
      1. issues_router._assert_visibility — guards GET /{id}, GET /by-identifier,
         PATCH, /transition, /dispatch, DELETE.
      2. issue_messages_router._assert_issue_visible — guards GET /{id}/messages.
      3. ws_router._resolve_issue_ws_user — guards the issue-chat WebSocket.
  created_by_agent_id / assignee_agent_id (uuid) → str()'d for SHAPE parity (no
      ==/!= consumer; keeps the SELECT *-shaped dict byte-identical to REST).

All four are swept to str by ``_parity`` (any uuid → str). NON-uuid
type-sensitive columns: id / issue_number / team_id / project_id / parent_id /
goal_id / ai_session_id (BIGINT) stay NATIVE int (the 5.3 trap). status /
priority / origin_kind / identifier / dbos_workflow_id / title / description are
plain Text (CHECK-constrained, NOT SQLAlchemy Enum) → native str, no unwrap.
timestamptz columns → ``.isoformat()`` (Issue response fields are datetime).
execution_state (JSONB) → native dict.

There are NO date/timestamptz RANGE *filters* in this repo, BUT there IS a
temporal WRITE-binding hazard: the inherited soft_delete / transition_status
(and the router's hidden_at PATCH) hand ``update()`` ISO-STRINGS for started_at /
completed_at / cancelled_at / hidden_at (REST/PostgREST accepted ISO strings;
asyncpg binding to a real DateTime(True) column does NOT). ``update()`` runs
every patch value through ``_coerce_temporal`` (ISO-str → aware datetime) at the
write boundary — the v3 temporal-binding rule.

Writes commit via ``write_scope()``. Reads use ``read_scope()``.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger
from sqlalchemy import and_, or_, select, text
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import Issues
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict


def needs_input_predicate():
    """Spec-4 (needs_input first-class): the row shape that means "the agent
    is stalled waiting on a human answer" — extracted for testability, same
    spirit as ``visibility_predicate``.

    ``execution_state`` is JSONB; ``[...]`` indexes it and ``.astext`` casts
    the JSON scalar to text for the ``=`` compare (Postgres jsonb ->> ).
    """
    return and_(
        Issues.status == "needs_followup",
        Issues.execution_state["agent_outcome"].astext == "needs_input",
    )


def visibility_predicate(user_id: str):
    """The D6.1 issue-visibility WHERE clause, extracted for testability.

    visible = created by me OR assigned to me OR issue.team_id ∈ my teams.
    Team membership is a correlated IN-subquery against team_members — the
    single source of the "team 是铁边界" contract: same-team members see each
    other's issues, other teams never leak.
    """
    from app.models.teams import TeamMembers

    member_teams = select(TeamMembers.team_id).where(TeamMembers.user_id == user_id)
    return or_(
        Issues.created_by_user_id == user_id,
        Issues.assignee_user_id == user_id,
        Issues.team_id.in_(member_teams),
    )


_ISSUE_N2A: Dict[str, str] = _name_to_attr(Issues)
_ISSUE_ATTRS = {p.key for p in Issues.__mapper__.column_attrs}

# timestamptz columns on issues. The soft_delete / transition_status setters
# (and the router's hidden_at patch) hand us ISO-STRINGS for these (REST/
# PostgREST accepted strings); asyncpg binds to a real DateTime(True) column and
# REQUIRES a native aware datetime, so we coerce ISO-str → datetime at the write
# boundary (v3 temporal-binding rule). Any non-temporal value (e.g. None) passes
# through untouched.
_ISSUE_TS_COLS = frozenset(
    {
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "cancelled_at",
        "hidden_at",
        "execution_locked_at",
        "paused_at",
    }
)


# Terminal statuses that also end a target-level pause (phase 2a §2: "cancel
# while paused clears paused_at"). Mirrors issue_lifecycle.PREEMPT_STATUSES.
PAUSE_CLEARING_STATUSES = frozenset({"cancelled", "done", "closed"})


def _coerce_temporal(key: str, value: Any) -> Any:
    """Coerce an ISO-string timestamptz patch value to a native aware datetime
    for the asyncpg bind. Leaves native datetimes / None / non-ts keys as-is."""
    if key in _ISSUE_TS_COLS and isinstance(value, str):
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    return value


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped issues dict.

    uuid → str (REST shape — REQUIRED for the created_by_user_id /
    assignee_user_id authz compares; shape-only for the agent ids); datetime →
    ISO str. Bigint id / FKs (id / team_id / project_id / parent_id / goal_id /
    ai_session_id / issue_number) stay NATIVE int (the 5.3 trap). JSONB
    execution_state stays a native dict. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one full Issues row."""
    return _parity(_orm_obj_to_dict(obj, _ISSUE_N2A))


class IssueRepository:
    TABLE_NAME = "issues"

    async def atomic_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Allocate (issue_number, identifier) and INSERT atomically.

        payload should NOT contain `id`, `issue_number`, `identifier`,
        `created_at`, or `updated_at` — the DB sets those.

        Uses the ``issue_create_atomic(payload jsonb)`` SECURITY DEFINER stored
        procedure (migration 173) which performs the counter UPDATE + INSERT in a
        single PG transaction. If INSERT fails (RLS / CHECK / FK), the counter
        rolls back too — no MH-N gap. ``SELECT * FROM <proc>`` expands the
        composite return into columns; write_scope() owns the surrounding txn.
        """
        # UUIDs and dates (and other non-JSON-native types) need coercion before
        # the jsonb payload bind: UUID → str, date/datetime → ISO. The DATE
        # column (issues.due_date) then casts cleanly from the jsonb string.
        sanitized: dict[str, Any] = {}
        for k, v in payload.items():
            if isinstance(v, _uuid.UUID):
                sanitized[k] = str(v)
            elif isinstance(v, (_dt.datetime, _dt.date)):
                sanitized[k] = v.isoformat()
            else:
                sanitized[k] = v

        stmt = text("SELECT * FROM issue_create_atomic(CAST(:payload AS jsonb))")
        async with write_scope() as session:
            result = await session.execute(stmt, {"payload": _json.dumps(sanitized)})
            row = result.mappings().first()
            # Materialize INSIDE the scope (matches projects_repository_orm).
            if not row:
                raise RuntimeError("issue_create_atomic returned empty result")
            out = _parity(dict(row))
        logger.info(f"Created issue {out.get('identifier')} (id={out.get('id')})")
        return out

    async def get_by_id(self, issue_id: int) -> Optional[dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(Issues).where(Issues.id == int(issue_id)).limit(1)
            )
            row = result.scalars().first()
            return _row(row) if row else None

    async def get_by_identifier(self, identifier: str) -> Optional[dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(Issues).where(Issues.identifier == identifier).limit(1)
            )
            row = result.scalars().first()
            return _row(row) if row else None

    async def update(self, issue_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        if not patch:
            existing = await self.get_by_id(issue_id)
            if not existing:
                raise ValueError(f"issue id={issue_id} not found")
            return existing
        values = {
            k: _coerce_temporal(k, v) for k, v in patch.items() if k in _ISSUE_ATTRS
        }
        async with write_scope() as session:
            result = await session.execute(
                sa_update(Issues)
                .where(Issues.id == int(issue_id))
                .values(**values)
                .returning(Issues)
            )
            row = result.scalars().first()
            # Materialize INSIDE the scope (matches projects_repository_orm) so
            # the dict build never depends on expire_on_commit=False keeping the
            # entity readable after the session closes.
            if not row:
                raise ValueError(f"issue id={issue_id} not found or update no-op")
            return _row(row)

    async def set_paused_at(
        self, issue_id: int, value: Optional[_dt.datetime]
    ) -> dict[str, Any]:
        """Phase 2a target-level pause: ``paused_at`` is the ONLY truth of a
        paused issue (rollup ``derive_phase`` reads it first; status stays
        ``in_progress``). ``None`` resumes. Not on the mig-170 immutable list,
        so this is an ordinary app-role write — no service_role hop."""
        return await self.update(issue_id, {"paused_at": value})

    async def is_team_member(self, user_id: str, team_id: int) -> bool:
        """True when user_id belongs to team_id. Backs the D6.1 visibility
        fold on the detail-side asserts (list-side folds in SQL)."""
        from app.models.teams import TeamMembers

        _uuid.UUID(user_id)
        async with read_scope() as session:
            row = await session.execute(
                select(TeamMembers.user_id).where(
                    TeamMembers.user_id == user_id,
                    TeamMembers.team_id == int(team_id),
                )
            )
            return row.first() is not None

    async def list_for_user(
        self,
        user_id: str,
        *,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
        team_id: Optional[int] = None,
        include_hidden: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List issues visible to user_id. Returns (items, total).

        D6.1 team folding (用户立约 2026-07-19: team 是铁边界): visible =
        (created by me) OR (assigned to me) OR (the issue's team is one I
        belong to). Membership comes from a team_members subquery — the
        client's team_id param is only ever an AND-filter on top, never an
        authorization input, so passing another team's id yields nothing
        beyond rows already visible (own/assigned).
        """
        # Defensive: validate user_id is a real UUID before it reaches the WHERE
        # bind. A malformed value (or one from an untrusted source in the future)
        # would otherwise flow straight into the query.
        _uuid.UUID(user_id)

        async with read_scope() as session:
            base = select(Issues).where(visibility_predicate(user_id))
            if status:
                base = base.where(Issues.status == status)
            if project_id:
                base = base.where(Issues.project_id == project_id)
            if team_id:
                base = base.where(Issues.team_id == team_id)
            if not include_hidden:
                base = base.where(Issues.hidden_at.is_(None))

            # Total count (count="exact" parity) over the SAME predicate set.
            from sqlalchemy import func

            count_stmt = select(func.count()).select_from(base.subquery())
            total = await session.scalar(count_stmt) or 0

            page_stmt = (
                base.order_by(Issues.created_at.desc()).offset(offset).limit(limit)
            )
            result = await session.execute(page_stmt)
            items = [_row(r) for r in result.scalars().all()]
        return items, total

    async def list_needs_input(
        self, user_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Issues visible to user_id that are parked at ``needs_followup``
        with ``execution_state.agent_outcome == 'needs_input'`` — the agent is
        waiting on a human answer (Spec-4 needs_input first-class). Feeds the
        Task Center 'needs your answer' list (Task 3).

        Same D6.1 visibility fold as ``list_for_user`` (own OR assignee OR
        team member); ordered by ``updated_at`` DESC (most recently asked
        first), capped at ``limit``. Always excludes soft-deleted rows (no
        ``include_hidden`` toggle, unlike ``list_for_user``) — an issue the
        caller hid is not something they're waiting to answer.
        """
        _uuid.UUID(user_id)
        async with read_scope() as session:
            stmt = (
                select(Issues)
                .where(visibility_predicate(user_id))
                .where(needs_input_predicate())
                .where(Issues.hidden_at.is_(None))
                .order_by(Issues.updated_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [_row(r) for r in result.scalars().all()]

    async def count_needs_input_by_agent(
        self, user_id: str, agent_ids: list[str]
    ) -> dict[str, int]:
        """{agent_id: count} of needs_input issues assigned to each agent and
        visible to ``user_id`` (AI Library gallery "waiting for your reply"
        badge, spec 2026-08-02 §B1).

        One GROUP BY over ``assignee_agent_id`` — the partial index
        ``issues_assignee_agent_status_idx`` covers it. Same visibility fold
        and hidden_at exclusion as ``list_needs_input``: an agent parked on an
        issue the caller can't see must not inflate the caller's badge.

        Agents with no such issue are simply absent from the dict.
        """
        if not agent_ids:
            return {}
        _uuid.UUID(user_id)
        ids = [_uuid.UUID(str(a)) for a in agent_ids]
        try:
            from sqlalchemy import func

            async with read_scope() as session:
                rows = (
                    await session.execute(
                        select(
                            Issues.assignee_agent_id,
                            func.count().label("n"),
                        )
                        .where(Issues.assignee_agent_id.in_(ids))
                        .where(visibility_predicate(user_id))
                        .where(needs_input_predicate())
                        .where(Issues.hidden_at.is_(None))
                        .group_by(Issues.assignee_agent_id)
                    )
                ).all()
            return {str(r[0]): int(r[1]) for r in rows}
        except Exception as e:
            logger.error(f"[issues] count_needs_input_by_agent failed: {e}")
            return {}

    async def map_identifiers(self, issue_ids: list[int]) -> dict[str, str]:
        """{issue id (as str) → human identifier (MH-N)} for a set of ids.

        One query; missing ids are simply absent. Ids ride as strings out
        (bigIntSafeFetch discipline) so the caller can key file rows by the same
        stringified ``source_issue_id`` it holds."""
        ids = [int(i) for i in issue_ids if i is not None]
        if not ids:
            return {}
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(Issues.id, Issues.identifier).where(Issues.id.in_(ids))
                )
            ).all()
        return {str(r[0]): r[1] for r in rows}

    async def list_in_progress_without_live_run(self) -> list[dict[str, Any]]:
        """MH-1 reconciliation candidates: ``in_progress`` issues whose
        ``execution_state`` still carries a turn marker while no run is
        ``running`` on them (by issue_id or their session conversation) and
        nothing is awaiting input. The sweeper stamps ``agent_outcome`` on
        them so the decoration stops claiming a run that ended."""
        from app.models import AgentRuns

        live = (
            select(AgentRuns.id)
            .where(
                AgentRuns.status == "running",
                or_(
                    AgentRuns.issue_id == Issues.id,
                    AgentRuns.conversation_id == Issues.ai_session_id,
                ),
            )
            .exists()
        )
        stmt = select(Issues).where(
            Issues.status == "in_progress",
            Issues.hidden_at.is_(None),
            Issues.execution_state.has_key("turn"),
            ~Issues.execution_state.has_key("awaiting_input"),
            ~Issues.execution_state.has_key("agent_outcome"),
            ~live,
        )
        async with read_scope() as session:
            return [_row(r) for r in (await session.execute(stmt)).scalars().all()]

    async def list_children(
        self,
        parent_id: int,
        *,
        include_hidden: bool = False,
    ) -> list[dict[str, Any]]:
        """All sub-issues of ``parent_id`` (issues.parent_id == parent_id).

        Backs the sub-issue completion barrier (subissue_barrier.py): after a
        child reaches a terminal status we load its full sibling set to decide
        whether the barrier has closed. Hidden (soft-deleted) rows are excluded
        by default — a trashed child must not keep the barrier open, and it is
        not part of the barrier size the pinned wake-id is derived from.
        """
        async with read_scope() as session:
            stmt = select(Issues).where(Issues.parent_id == int(parent_id))
            if not include_hidden:
                stmt = stmt.where(Issues.hidden_at.is_(None))
            result = await session.execute(stmt)
            return [_row(r) for r in result.scalars().all()]

    async def list_by_origin(
        self,
        origin_kind: str,
        origin_id: str,
        *,
        include_hidden: bool = False,
    ) -> list[dict[str, Any]]:
        """All issues stamped with a given (origin_kind, origin_id) pair.

        Backs the content back-link reverse lookup (issues_origin_idx). Used by
        the project-stage auto-issue hook for idempotency (has this stage's
        issue already been created?) and for closing the previous stage's issue.
        Hidden (soft-deleted) rows are excluded by default.
        """
        async with read_scope() as session:
            stmt = select(Issues).where(
                Issues.origin_kind == origin_kind,
                Issues.origin_id == origin_id,
            )
            if not include_hidden:
                stmt = stmt.where(Issues.hidden_at.is_(None))
            result = await session.execute(stmt)
            return [_row(r) for r in result.scalars().all()]

    async def soft_delete(self, issue_id: int) -> dict[str, Any]:
        """User-facing delete — sets hidden_at, keeps row for audit / undo."""
        return await self.update(
            issue_id, {"hidden_at": datetime.now(timezone.utc).isoformat()}
        )

    async def transition_status(
        self,
        issue_id: int,
        new_status: str,
        *,
        dbos_workflow_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Status-transition setter. Includes lifecycle timestamp side-effects
        (started_at / completed_at / cancelled_at) per design doc Protocol 5."""
        # Capture the pre-transition status BEFORE the write — the sub-issue
        # barrier's first gate is a non-terminal→terminal edge check, so an
        # edit/repeat-save landing the same status must be told apart from a real
        # transition. Read once here; the update below is the write.
        prev = await self.get_by_id(issue_id)
        prev_status = prev.get("status") if prev else None

        patch: dict[str, Any] = {"status": new_status}
        now = datetime.now(timezone.utc).isoformat()

        if new_status == "in_progress":
            patch["started_at"] = now
        elif new_status == "done":
            patch["completed_at"] = now
        elif new_status == "cancelled":
            patch["cancelled_at"] = now

        if dbos_workflow_id is not None:
            patch["dbos_workflow_id"] = dbos_workflow_id
        if new_status in PAUSE_CLEARING_STATUSES and (prev or {}).get("paused_at"):
            # Phase 2a: a pause is a non-terminal state. Landing on a terminal
            # status ends it too — otherwise the rollup (paused_at wins) keeps
            # showing a cancelled/closed issue as paused.
            patch["paused_at"] = None

        result = await self.update(issue_id, patch)
        # Post-commit sub-issue barrier hook. Placed at the repository layer (not
        # each caller) so BOTH transition_status entry points — the router's
        # /transition endpoint and project_stage_issues' stage-close — are
        # covered by one call site, with no chance of a future caller forgetting
        # it. The agent-workflow terminal path uses a raw SQL setter that does
        # NOT go through this method, so issue_lifecycle.execute_issue fires the
        # same hook from its workflow body (two-site rationale in
        # subissue_barrier). Best-effort: never let the barrier break the
        # transition.
        await _fire_subissue_barrier(int(issue_id), prev_status, new_status)
        # Pipeline relay (W2b) fan-out rides the SAME seam as the barrier fan-in:
        # a pipeline-step child reaching terminal advances its run to the next
        # step. Separate best-effort call so a relay failure never affects the
        # barrier or the transition.
        await _fire_pipeline_relay(int(issue_id), prev_status, new_status)
        # Workflow node status回流 (M1 PR-B): a project_stage mirror issue's
        # status change projects onto its ``project_stage_nodes.status`` (issue
        # is the fact source, node status is the projection). Same repo seam,
        # same best-effort discipline as the two hooks above. ``result`` is the
        # freshly-written issue row, so its origin_kind/origin_id are current.
        await _fire_stage_node_sync(result, new_status)
        return result


async def _fire_pipeline_relay(
    issue_id: int, prev_status: Optional[str], new_status: str
) -> None:
    """Best-effort pipeline-relay advance after a status transition. Same repo→
    service inversion rationale as ``_fire_subissue_barrier``; the relay hook
    swallows its own errors, this outer guard is belt-and-braces."""
    try:
        from app.services.issues.pipeline_relay import on_pipeline_child_terminal

        await on_pipeline_child_terminal(issue_id, prev_status, new_status)
    except Exception as exc:  # noqa: BLE001 — the transition is the primary op
        logger.warning(
            f"[issue_repository] pipeline relay hook failed for issue "
            f"{issue_id}: {exc!r}"
        )


# issues.status → project_stage_nodes.status projection (spec §6.2). done/
# cancelled are the two terminals; a cancelled mirror parks its node back at
# pending (the group can be re-derived), never a node "cancelled" (not a node
# status). Any status outside this map leaves the node untouched.
_ISSUE_TO_NODE_STATUS: Dict[str, str] = {
    "todo": "pending",
    "in_progress": "in_progress",
    "in_review": "in_review",
    "done": "done",
    "cancelled": "pending",
}

# Public view of the statuses the projection actually maps, so a caller can
# skip fetching the issue row at all for a status that would be a no-op here
# (``blocked`` / ``needs_followup`` have no node counterpart).
STAGE_NODE_SYNC_STATUSES = frozenset(_ISSUE_TO_NODE_STATUS)


async def fire_stage_node_sync(
    issue: Optional[dict],
    new_status: str,
    *,
    enqueue_autopilot: bool = True,
) -> None:
    """Public seam onto the issue→node projection, for the callers that write
    ``issues.status`` WITHOUT going through ``transition_status``.

    ``issue_lifecycle.set_status`` is the one such caller: it updates
    ``public.issues`` with raw SQL (the execution columns are service_role-
    only), which skipped this projection entirely — a mirror node stayed on
    its old status after an agent self-completed to ``in_review``.

    ``enqueue_autopilot=False`` suppresses the ``done``-path tail tick. Pass
    it from anything running inside a ``@DBOS.step``: starting a workflow
    there raises a bare AssertionError (see ``issue_lifecycle.
    _maybe_fire_subissue_barrier``'s docstring). Same best-effort contract as
    the internal hook — never raises.
    """
    await _fire_stage_node_sync(issue, new_status, enqueue_autopilot=enqueue_autopilot)


async def _fire_stage_node_sync(
    issue: Optional[dict],
    new_status: str,
    *,
    enqueue_autopilot: bool = True,
) -> None:
    """Best-effort issue→node status projection after a status transition.

    Only fires for a ``project_stage`` mirror issue whose ``origin_id`` is the
    NEW three-segment ``project_stage:{project_id}:{node_id}`` shape — the old
    two-segment SOP origin (no project scope) is skipped, and a non-project_stage
    issue never reaches ``set_node_status``. If the node id resolves to a legacy
    SOP stage rather than a real ``project_stage_nodes`` row, ``set_node_status``
    is a harmless no-op. Swallows its own errors so the transition is never
    aborted by the projection.
    """
    try:
        if not issue or issue.get("origin_kind") != "project_stage":
            return
        origin_id = issue.get("origin_id")
        if not origin_id:
            return
        from app.services.library.project_stage_issues import parse_stage_origin_id

        project_id, node_id = parse_stage_origin_id(str(origin_id))
        if project_id is None:
            # Old two-segment origin — not a workflow node, skip回流.
            return
        mapped = _ISSUE_TO_NODE_STATUS.get(new_status)
        if mapped is None:
            return

        from app.repositories.project_stage_nodes_repository import (
            get_project_stage_nodes_repository,
        )

        await get_project_stage_nodes_repository().set_node_status(node_id, mapped)

        # M4 Autopilot (task O2, spec §2 trigger 1): a node reaching 'done'
        # may unblock other nodes' events.auto_start deps, or close out the
        # active group so a cascade advance can run — best-effort tail
        # enqueue, never allowed to affect the transition above.
        if mapped == "done" and enqueue_autopilot:
            await _enqueue_autopilot_tick_best_effort(project_id)
    except Exception as exc:  # noqa: BLE001 — the transition is the primary op
        logger.warning(
            f"[issue_repository] stage-node sync hook failed for issue "
            f"{(issue or {}).get('id')}: {exc!r}"
        )


async def _enqueue_autopilot_tick_best_effort(project_id: str) -> None:
    """Best-effort import + enqueue seam so a broken import can never abort
    the caller's own try/except (belt-and-braces, same idiom as the other
    hook wrappers in this module).

    Re-entrancy guard (review fix I2): ``execute_advance``'s forward branch —
    itself one of ``autopilot._cascade_pass``'s own loop steps — transitions
    each closing-group node's mirror issue to 'done', which is EXACTLY this
    hook's own trigger (the issue→node sync fires on every status write, not
    just user-driven closes). Without checking ``cascade_in_progress()`` here
    too, a cascade closing N nodes in one group would fan out N redundant
    nested tick enqueues per step — the SAME hole ``advance_service``'s own
    tail-enqueue call guards against, just reached through this OTHER
    call site instead. Never raises — an enqueue failure must never affect
    the status transition that triggered it.
    """
    try:
        from app.workflows.autopilot import cascade_in_progress, enqueue_autopilot_tick

        if cascade_in_progress():
            return
        await enqueue_autopilot_tick(project_id)
    except Exception as exc:  # noqa: BLE001 — never blocks the status transition
        logger.warning(
            f"[issue_repository] autopilot tick enqueue failed for project "
            f"{project_id}: {exc!r}"
        )


async def _fire_subissue_barrier(
    issue_id: int, prev_status: Optional[str], new_status: str
) -> None:
    """Best-effort sub-issue barrier evaluation after a status transition.

    Lazy-imports the service (repo → service inversion is deliberate here — the
    barrier is a domain reaction to a data write, and routing it through the one
    repository method that owns status transitions is what makes coverage
    exhaustive). ``on_child_issue_terminal`` already swallows its own errors; the
    outer guard is belt-and-braces so even an import failure can never bubble up
    and abort the child's own status flow.
    """
    try:
        from app.services.issues.subissue_barrier import on_child_issue_terminal

        await on_child_issue_terminal(issue_id, prev_status, new_status)
    except Exception as exc:  # noqa: BLE001 — the transition is the primary op
        logger.warning(
            f"[issue_repository] sub-issue barrier hook failed for issue "
            f"{issue_id}: {exc!r}"
        )


def get_issue_repository() -> "IssueRepository":
    """Return the IssueRepository (ORM-backed; the domain is ORM-only)."""
    return IssueRepository()


# Module-level singleton imported by the three consumers (issues_router /
# issue_messages_router / ws_router) and the scheduled_master workflow as
# ``from app.repositories.issue_repository import issue_repository``.
issue_repository = get_issue_repository()
