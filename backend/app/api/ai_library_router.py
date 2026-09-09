"""AI Library REST endpoints — agents, skills, and skill files (Phase 1).

Endpoints (prefix ``/ai-library``):
    GET    /agents                          — List accessible agents (+ skill_ids)
    GET    /agents/{slug}                   — Get single agent (+ skill_ids)
    PATCH  /agents/{slug}                   — Update agent (rejects system presets)
    GET    /skills                          — List accessible skills (+ files)
    GET    /skills/{slug}                   — Get single skill (+ files)
    PATCH  /skills/{slug}                   — Update skill (rejects system presets)
    GET    /skills/{slug}/files             — List skill's files
    PUT    /skills/{slug}/files/{path:path} — Upsert a skill file
    DELETE /skills/{slug}/files/{path:path} — Delete a skill file

Phase 1 policy: system-preset agents (is_system_preset=true) and system-preset
skills (is_public=true AND project_id IS NULL) are read-only — write attempts
return 403. User- and project-scoped resources are mutable by their owners.

Mirrors the flat ``app/api/*_router.py`` convention used elsewhere in this
backend (e.g. ``ai_agents_router.py``, ``skills_router.py``).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from loguru import logger
from pydantic import BaseModel, Field

from app.core.admin_deps import AdminAuthDep
from app.core.deps import AuthDep
from app.core.scope_dep import ScopedRequestDep
from app.core.scope_guards import verify_project_read_access
from app.repositories.agent_repository import (
    AGENT_OVERRIDE_FIELDS,
    AgentRepository,
    get_agent_repository,
)
from app.repositories.agent_runs_repository import (
    get_agent_runs_repository,
)
from app.repositories.agent_workforce_repository import (
    tt_row_to_task_shape,
)
from app.repositories.issue_repository import get_issue_repository
from app.repositories.skill_repository import (
    SkillRepository,
    get_skill_repository,
)
from app.schemas.agent_runs import (
    RunDetail,
    RunGroupListResponse,
    RunListItem,
    RunListResponse,
    UndoReport,
    UsageAggregate,
)
from app.schemas.ai_library import (
    AgentCreate,
    AgentOut,
    AgentStatsResponse,
    AgentUpdate,
    CapabilitiesOut,
    ChatPermissionsOut,
    PermissionAuditItem,
    PermissionAuditListOut,
    SkillCreate,
    SkillFileOut,
    SkillFileUpsert,
    SkillOut,
    SkillUpdate,
)
from app.schemas.ai_library_chat import (
    ChatRequest,
    ChatResponse,
    SessionCreate,
    SessionOut,
    SessionUpdate,
    SessionWithMessages,
)
from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
from app.services.ai.permissions.agent_chat_caps import agent_chat_caps
from app.services.ai.permissions.high_risk_caps import high_risk_caps
from app.services.ai.runner.seed_loader import SeedLoader
from app.services.modules.gate import require_module

router = APIRouter(
    prefix="/ai-library",
    tags=["AI Library"],
    dependencies=[Depends(require_module("ai-library"))],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repos() -> tuple[AgentRepository, SkillRepository]:
    """Return a fresh (AgentRepository, SkillRepository) pair per request."""
    return get_agent_repository(), get_skill_repository()


def _coerce_user_uuid(user_id: str) -> UUID:
    """Convert an auth user_id string to UUID; 401 on malformed token."""
    try:
        return UUID(user_id)
    except (TypeError, ValueError) as exc:
        logger.error(f"[ai-library] malformed user_id from auth: {user_id} ({exc})")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user identifier in auth context",
        )


def _is_system_skill(skill: Dict[str, Any]) -> bool:
    """Return True for system-preset skills (public + no project owner)."""
    return bool(skill.get("is_public")) and skill.get("project_id") is None


def _serialize_row(row: Any) -> Dict[str, Any]:
    """Coerce an ORM row mapping to the JSON-safe primitives the PostgREST
    path returned: enum → bare str, timestamptz → ISO-8601 str, uuid → str.
    BIGINT/int/Decimal pass through (FastAPI renders them as JSON numbers).
    jsonb columns come back as dicts/lists and pass through unchanged."""
    from uuid import UUID as _UUID

    from app.repositories._orm_helpers import _plain

    out: Dict[str, Any] = {}
    for key, value in row.items():
        value = _plain(value)
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif isinstance(value, _UUID):
            out[key] = str(value)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Scope membership helpers (Phase 2 PR 2.9)
# ---------------------------------------------------------------------------


async def _user_is_team_member(user_id: UUID, team_id: int) -> bool:
    """Return True iff the user has a ``team_members`` row for this team."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TeamMembers

    async with read_scope() as session:
        row = (
            await session.execute(
                select(TeamMembers.team_id)
                .where(TeamMembers.team_id == int(team_id))
                .where(TeamMembers.user_id == str(user_id))
                .limit(1)
            )
        ).first()
    return row is not None


async def _user_can_write_project(user_id: UUID, project_id: int) -> bool:
    """True iff user is owner of ``project_id`` OR a ``project_members`` row.

    Any project membership qualifies (including viewer) — we only need
    presence to let the user attach an agent to the project's scope. Finer
    role-based restrictions can layer on later if needed.
    """
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import ProjectMembers, Projects

    async with read_scope() as session:
        # Owner check
        owner = (
            await session.execute(
                select(Projects.owner_id).where(Projects.id == int(project_id)).limit(1)
            )
        ).first()
        if owner is not None and str(owner[0]) == str(user_id):
            return True
        # Explicit membership
        member = (
            await session.execute(
                select(ProjectMembers.project_id)
                .where(ProjectMembers.project_id == int(project_id))
                .where(ProjectMembers.user_id == str(user_id))
                .limit(1)
            )
        ).first()
    return member is not None


async def _fetch_user_team_ids(user_id: UUID) -> List[int]:
    """Return BIGINT team ids the user is a member of (empty on miss)."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TeamMembers

    async with read_scope() as session:
        rows = (
            (
                await session.execute(
                    select(TeamMembers.team_id).where(
                        TeamMembers.user_id == str(user_id)
                    )
                )
            )
            .scalars()
            .all()
        )
    return [int(t) for t in rows if t is not None]


def _scoped_team_id(request: Request, user_team_ids: List[int]) -> Optional[int]:
    """Resolve the ``X-Team-Id`` header to a team filter, if any.

    Returns the parsed BIGINT when:
      * the header is present and parseable
      * the user is a member of that team

    Returns ``None`` when the header is absent, unparseable, or the user is
    not a member (silently falling back to "no team scoping"). That matches
    MediaHub's existing X-Team-Id convention — the header is a hint, not an
    authorization boundary. The underlying resource RLS is still enforced
    via ``team_ids`` / ``project_ids``, so a bogus header can only *narrow*
    the visible set, never expand it.
    """
    raw = request.headers.get("X-Team-Id")
    if not raw:
        return None
    try:
        scoped = int(raw)
    except (TypeError, ValueError):
        return None
    if scoped not in user_team_ids:
        return None
    return scoped


async def _fetch_user_project_ids(user_id: UUID) -> List[int]:
    """Return BIGINT project ids the user owns or is a member of."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import ProjectMembers, Projects

    async with read_scope() as session:
        owned = (
            (
                await session.execute(
                    select(Projects.id).where(Projects.owner_id == str(user_id))
                )
            )
            .scalars()
            .all()
        )
        member = (
            (
                await session.execute(
                    select(ProjectMembers.project_id).where(
                        ProjectMembers.user_id == str(user_id)
                    )
                )
            )
            .scalars()
            .all()
        )
    ids: set[int] = set()
    for v in owned:
        if v is not None:
            ids.add(int(v))
    for v in member:
        if v is not None:
            ids.add(int(v))
    return sorted(ids)


async def _fetch_team_names(team_ids: List[int]) -> Dict[int, str]:
    """Return {team_id: name} for the given BIGINT ids ([] → {})."""
    if not team_ids:
        return {}
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Teams

    async with read_scope() as session:
        rows = (
            await session.execute(
                select(Teams.id, Teams.name).where(
                    Teams.id.in_([int(t) for t in team_ids])
                )
            )
        ).all()
    return {int(r[0]): r[1] for r in rows}


async def _fetch_project_names(project_ids: List[int]) -> Dict[int, str]:
    """Return {project_id: name} for the given BIGINT ids ([] → {})."""
    if not project_ids:
        return {}
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Projects

    async with read_scope() as session:
        rows = (
            await session.execute(
                select(Projects.id, Projects.name).where(
                    Projects.id.in_([int(p) for p in project_ids])
                )
            )
        ).all()
    return {int(r[0]): r[1] for r in rows}


async def _enrich_rows_with_scope_names(
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach ``team_name`` / ``project_name`` to each row.

    Shared between agents and skills — both surface a denormalized scope
    badge in the UI. Single batch query per scope, avoids N+1. Returns
    new dicts (never mutates the inputs).
    """
    team_ids = sorted({int(r["team_id"]) for r in rows if r.get("team_id") is not None})
    project_ids = sorted(
        {int(r["project_id"]) for r in rows if r.get("project_id") is not None}
    )
    team_names = await _fetch_team_names(team_ids)
    project_names = await _fetch_project_names(project_ids)

    enriched: List[Dict[str, Any]] = []
    for row in rows:
        t_id = row.get("team_id")
        p_id = row.get("project_id")
        enriched.append(
            {
                **row,
                "team_name": team_names.get(int(t_id)) if t_id is not None else None,
                "project_name": (
                    project_names.get(int(p_id)) if p_id is not None else None
                ),
            }
        )
    return enriched


# Kept as an alias so existing call sites remain readable at each callsite.
_enrich_agents_with_scope_names = _enrich_rows_with_scope_names
_enrich_skills_with_scope_names = _enrich_rows_with_scope_names


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


@router.get("/agents", response_model=List[AgentOut], summary="List accessible agents")
async def list_agents(request: Request, auth: AuthDep) -> List[Dict[str, Any]]:
    """Return all agents visible to the current user with their skill bindings.

    Visible set = union of:
      * system presets (``is_system_preset=true``)
      * user's own agents (``user_id = me``)
      * agents on the active team — narrowed by ``X-Team-Id`` header when
        present and the user is a member of that team; otherwise all teams
        the user is a member of
      * agents on any project the user owns or is a member of

    Each row is enriched with ``skill_ids`` (ordered, enabled only) plus the
    denormalized ``team_name`` / ``project_name`` for UI scope badges.
    """
    agent_repo, _ = _repos()
    user_uuid = _coerce_user_uuid(auth.user_id)
    user_team_ids = await _fetch_user_team_ids(user_uuid)
    project_ids = await _fetch_user_project_ids(user_uuid)
    # X-Team-Id narrows the visible team resources to one team. When absent
    # or the user isn't a member of the requested team, fall back to every
    # team the user belongs to (legacy behavior).
    scoped_team = _scoped_team_id(request, user_team_ids)
    team_ids = [scoped_team] if scoped_team is not None else user_team_ids
    rows = await agent_repo.list_accessible(
        user_id=user_uuid,
        team_ids=team_ids,
        project_ids=project_ids,
    )

    # Agent-overrides (mig 341): mark presets the caller (or their teams)
    # customized so the sidebar can badge them.
    override_scopes = await agent_repo.list_override_scopes(
        user_id=user_uuid, team_ids=team_ids
    )

    enriched_with_skills: List[Dict[str, Any]] = []
    for row in rows:
        try:
            agent_uuid = UUID(str(row["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(f"[ai-library] skipping agent with bad id: {exc}")
            continue
        skill_ids = await agent_repo.get_skill_ids(agent_uuid)
        enriched_with_skills.append(
            _with_resolved_permissions(
                {
                    **row,
                    "skill_ids": skill_ids,
                    "override_scopes": override_scopes.get(str(row["id"]), []),
                }
            )
        )
    return await _enrich_agents_with_scope_names(enriched_with_skills)


# Fault copy. Each line must name the remedy, not just the symptom — the
# gallery renders it verbatim under the badge (spec §B1: 故障徽章必须携带一行
# 可操作原因).
_FAULT_DETAIL = {
    "budget": "Monthly budget exceeded — raise the budget or resume the agent",
    "manual": "Paused by admin — resume from the agent's Profile tab",
}
_FAULT_DETAIL_MAX = 120

# error_code values that mean "our infrastructure stopped this run", not
# "this agent is broken". Written by app/services/liveness/reconcile.py, whose
# only caller is startup bootstrap — so it fires once per backend start, over
# runs that were in flight when the process went away. A deploy is a routine
# operation; letting it paint the gallery card red told users their agent had
# failed when nothing about the agent was wrong (2026-08-19 report). These
# surface as a neutral `interrupted_reason` instead.
#
# Deliberately NOT included: 'heartbeat_lost' / 'liveness_dead' from
# liveness_scanner._mark_dead. Those fire while the backend is up and running,
# so the process really did die under the agent — a genuine fault that must
# keep lighting the badge.
_INTERRUPTED_ERROR_CODES = {"stranded_on_restart": "restart"}


# MUST stay above /agents/{slug} — FastAPI matches in declaration order and
# the slug route would otherwise swallow "stats".
@router.get(
    "/agents/stats",
    response_model=AgentStatsResponse,
    summary="Batch 7d stats / live runs / waiting replies / fault per agent",
)
async def get_agents_stats(
    request: Request, auth: AuthDep, days: int = 7
) -> Dict[str, Any]:
    """One request covering every agent the caller can see.

    Replaces the gallery's per-agent ``/dashboard`` fan-out. Visibility
    reuses ``list_accessible`` so an agent the caller can't see never
    appears here either.

    Each aggregate is a single GROUP BY across all agent ids — never a
    per-agent query (see the N+1 guard in
    ``tests/test_ai_library_agent_stats.py``).
    """
    window_days = max(1, min(int(days), 90))
    agent_repo = get_agent_repository()
    runs_repo = get_agent_runs_repository()
    issue_repo = get_issue_repository()

    user_uuid = _coerce_user_uuid(auth.user_id)
    user_team_ids = await _fetch_user_team_ids(user_uuid)
    project_ids = await _fetch_user_project_ids(user_uuid)
    scoped_team = _scoped_team_id(request, user_team_ids)
    team_ids = [scoped_team] if scoped_team is not None else user_team_ids
    rows = await agent_repo.list_accessible(
        user_id=user_uuid, team_ids=team_ids, project_ids=project_ids
    )

    agent_ids: List[UUID] = []
    for row in rows:
        try:
            agent_ids.append(UUID(str(row["id"])))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(f"[ai-library] stats skipping agent with bad id: {exc}")

    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    usage = await runs_repo.usage_by_agent_since(agent_ids, since)
    running = await runs_repo.running_counts_by_agent(agent_ids)
    last_run_health = await runs_repo.latest_run_health_by_agent(agent_ids, since)
    needs_input = await issue_repo.count_needs_input_by_agent(
        str(user_uuid), [str(a) for a in agent_ids]
    )

    items: Dict[str, Any] = {}
    for row in rows:
        agent_id = str(row.get("id"))
        if agent_id == "None":
            continue
        paused = row.get("paused_reason")
        fault: Optional[Dict[str, Any]] = None
        interrupted_reason: Optional[str] = None
        health = last_run_health.get(agent_id)
        if paused in _FAULT_DETAIL:
            fault = {"kind": paused, "detail": _FAULT_DETAIL[paused]}
        elif health:
            code = health.get("error_code")
            message = health.get("error_message")
            if code in _INTERRUPTED_ERROR_CODES:
                # Not a fault: the run was cut short by a restart. The card
                # says so in neutral copy, worded frontend-side so it can be
                # translated (unlike _FAULT_DETAIL, which ships English).
                interrupted_reason = _INTERRUPTED_ERROR_CODES[code]
            elif message:
                # No message means the scanner flagged the run 'stuck' without
                # ever writing a reason; a badge with no remedy is what spec
                # §B1 set out to remove, so it stays silent.
                fault = {"kind": "dead_runs", "detail": message[:_FAULT_DETAIL_MAX]}
        agent_usage = usage.get(agent_id) or {}
        items[agent_id] = {
            "runs_7d": int(agent_usage.get("runs", 0)),
            "tokens_7d": int(agent_usage.get("tokens", 0)),
            "cost_cents_7d": int(agent_usage.get("cost_cents", 0)),
            "running_count": int(running.get(agent_id, 0)),
            "needs_input_count": int(needs_input.get(agent_id, 0)),
            "fault": fault,
            "interrupted_reason": interrupted_reason,
        }
    return {"items": items}


@router.get(
    "/agents/{slug}",
    response_model=AgentOut,
    summary="Get a single agent by slug",
)
async def get_agent(slug: str, auth: AuthDep) -> Dict[str, Any]:
    """Fetch a single agent by slug, 404 if missing.

    Response includes ``team_name`` / ``project_name`` for scope display.
    """
    agent_repo, _ = _repos()
    # Merged view: the editor shows the caller's EFFECTIVE agent (their
    # personal override applied), with override_scope/override_fields set so
    # the UI can show the customized badge + reset affordance.
    agent = await agent_repo.get_by_slug(
        slug, override_user_id=_coerce_user_uuid(auth.user_id)
    )
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        )
    agent_uuid = UUID(str(agent["id"]))
    agent = _with_resolved_permissions(
        {**agent, "skill_ids": await agent_repo.get_skill_ids(agent_uuid)}
    )
    enriched = await _enrich_agents_with_scope_names([agent])
    return enriched[0]


@router.post(
    "/agents",
    response_model=AgentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user-owned agent (optionally forked)",
)
async def create_agent(
    payload: AgentCreate,
    auth: AuthDep,
) -> Dict[str, Any]:
    """Create a non-preset agent owned by the current user.

    Scope (Phase 2 PR 2.9):
      * No ``team_id`` / ``project_id``: private per-user agent (default).
      * ``team_id`` set: visible to all team members. Caller must be a member.
      * ``project_id`` set: visible to project owner + members. Caller must be
        the owner or a member.
      * ``team_id`` + ``project_id`` both set → 400 (mutually exclusive).

    If ``fork_from`` is set, copies identity_md / soul_md / agent_md /
    model / temperature / max_tokens from that agent as a starting point.
    Explicit fields in the payload override forked values. Skill bindings
    are NOT copied — the user adds them separately via PATCH.
    """
    agent_repo, _ = _repos()
    user_uuid = _coerce_user_uuid(auth.user_id)

    # Scope validation — mutually exclusive + membership check.
    if payload.team_id is not None and payload.project_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="team_id and project_id are mutually exclusive",
        )
    if payload.team_id is not None:
        if not await _user_is_team_member(user_uuid, payload.team_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"user is not a member of team {payload.team_id}",
            )
    if payload.project_id is not None:
        if not await _user_can_write_project(user_uuid, payload.project_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"user is not the owner or a member of project {payload.project_id}"
                ),
            )

    # Slug must be unique
    if await agent_repo.get_by_slug(payload.slug):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"agent slug '{payload.slug}' already exists",
        )

    # Start from defaults
    fields: Dict[str, Any] = {
        "slug": payload.slug,
        "name": payload.name,
        "description": payload.description,
        "is_system_preset": False,
        "user_id": str(user_uuid),
        "team_id": payload.team_id,
        "project_id": payload.project_id,
        "model": "qwen-max",
        "temperature": 0.7,
        "max_tokens": 4096,
        "identity_md": None,
        "soul_md": None,
        "agent_md": None,
        "agent_group": None,
    }

    # If fork_from given, copy content fields from source (source may be
    # a system preset — we're only READING its fields).
    if payload.fork_from:
        source = await agent_repo.get_by_slug(payload.fork_from)
        if not source:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"fork source agent '{payload.fork_from}' not found",
            )
        for key in (
            "identity_md",
            "soul_md",
            "agent_md",
            "model",
            "temperature",
            "max_tokens",
            # A fork stays in its source's roster group unless the payload
            # says otherwise — a forked Portrait AI belongs with the artists.
            "agent_group",
        ):
            if source.get(key) is not None:
                fields[key] = source[key]

    # Explicit payload field overrides win over forked values.
    for key in (
        "model",
        "temperature",
        "max_tokens",
        "identity_md",
        "soul_md",
        "agent_md",
        "agent_group",
    ):
        val = getattr(payload, key)
        if val is not None:
            fields[key] = val

    created = await agent_repo.insert(fields)
    enriched = await _enrich_agents_with_scope_names([{**created, "skill_ids": []}])
    return enriched[0]


def _resolved_snapshot(profile: Any) -> Dict[str, Any]:
    """Resolved (fail-closed) chat + capabilities snapshot of a
    ``capability_profile`` value, for ``agent_permission_audits`` before/after
    columns (2026-08-10 spec §3). Deliberately the SAME parsers the gates
    enforce with, not an independent re-reading of the JSONB — so the audit
    trail reflects what actually took effect, not what the raw storage keys
    happen to say."""
    probe = {"capability_profile": profile}
    return {
        "chat": ChatPermissionsOut.from_caps(agent_chat_caps(probe)).model_dump(),
        "capabilities": CapabilitiesOut.from_caps(high_risk_caps(probe)).model_dump(),
    }


@router.patch(
    "/agents/{slug}",
    response_model=AgentOut,
    summary="Update agent fields and/or skill bindings",
)
async def update_agent(
    slug: str,
    payload: AgentUpdate,
    auth: AuthDep,
) -> Dict[str, Any]:
    """Patch an agent's mutable fields and optionally replace its skill bindings.

    Phase 1 carve-out (CHAT-PERM-15): system-preset agents remain read-only for
    content and skill edits, but chat_permissions ARE editable (governance, not
    content). Role-gate applies to chat_permissions edits (CHAT-PERM-19/review H1).
    """
    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        )

    agent_uuid = UUID(str(agent["id"]))
    # Single coercion shared by both the role gate and the write path below.
    user_uuid = _coerce_user_uuid(auth.user_id)
    # Content fields (everything except skill bindings and the two permission
    # subtrees, which merge into capability_profile further down).
    updates = payload.model_dump(
        exclude_none=True,
        exclude={
            "skill_ids",
            "chat_permissions",
            "capabilities",
            "override_scope",
            "override_team_id",
            "permission_change_reason",
        },
    )

    # System presets (mig 341): content edits land in the caller's OVERRIDE
    # layer instead of the (formerly 403'd) base row. Only the whitelisted
    # AGENT_OVERRIDE_FIELDS are customizable; catalog identity (name /
    # description / icon), budgets and skill bindings stay admin-owned.
    is_preset = bool(agent.get("is_system_preset"))
    override_updates: Dict[str, Any] = {}
    if is_preset:
        if payload.skill_ids is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="skill bindings of system presets are shared — not customizable",
            )
        non_overridable = sorted(k for k in updates if k not in AGENT_OVERRIDE_FIELDS)
        if non_overridable:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"system preset fields not customizable: {non_overridable}",
            )
        override_updates = updates
        updates = {}

    # Role gate for chat-permission edits (CHAT-PERM-19 / review H1). The legacy
    # endpoint had NO role check — any logged-in user could PATCH any agent. We
    # only gate the new chat_permissions write here (content edits keep their
    # existing preset-only policy). Grant if: caller owns the agent, OR is owner
    # of the agent's scope team, OR (for presets / platform-scope) is a platform
    # admin.
    if payload.chat_permissions is not None:
        if not await _can_edit_chat_permissions(agent_repo, agent, user_uuid):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="not allowed to change this agent's chat permissions",
            )

    # A8: the same gate governs HIGH-RISK capability grants (write/delete/media/
    # cross-episode/publish). Deliberately not a looser one — handing an agent
    # the ability to overwrite or delete a user's scenes is strictly more
    # privileged than editing its prompt, so a non-owner must never reach it.
    # For system presets this resolves to platform-admin-only (no user_id, no
    # team_id ⇒ only the admin branch can pass), which is what we want: preset
    # rows are shared, a grant there would apply to everyone.
    if payload.capabilities is not None:
        if not await _can_edit_chat_permissions(agent_repo, agent, user_uuid):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="not allowed to change this agent's capabilities",
            )

    # Budget "unlimited" convention: 0 from the client means "clear the cap".
    for budget_field in ("monthly_token_budget", "monthly_cost_cents_budget"):
        if updates.get(budget_field) == 0:
            updates[budget_field] = None

    # Deep-merge permission subtrees into capability_profile — never clobber
    # the existing Phase 4.5 keys (CHAT-PERM-01, lesson: user_settings clobber).
    # `chat` (governance) and `capabilities` (A8 high-risk grants) are siblings
    # and can arrive in the SAME request, so both merge into one shared copy of
    # the profile; two independent `updates["capability_profile"] = ...`
    # assignments would make the later block silently drop the earlier one.
    chat_audit: dict | None = None
    caps_audit: dict | None = None
    permission_audit: Optional[Dict[str, Any]] = None
    if payload.chat_permissions is not None or payload.capabilities is not None:
        existing_profile = agent.get("capability_profile")
        if not isinstance(existing_profile, dict):
            existing_profile = {}
        merged_profile = dict(existing_profile)

        if payload.chat_permissions is not None:
            raw_chat = merged_profile.get("chat")
            before_chat = dict(raw_chat) if isinstance(raw_chat, dict) else {}
            existing_chat = dict(before_chat)
            existing_chat.update(payload.chat_permissions.model_dump(exclude_none=True))
            merged_profile["chat"] = existing_chat
            chat_audit = {"before": before_chat, "after": existing_chat}

        if payload.capabilities is not None:
            raw_caps = merged_profile.get("capabilities")
            before_caps = dict(raw_caps) if isinstance(raw_caps, dict) else {}
            merged_caps = dict(before_caps)
            patch = payload.capabilities.model_dump(exclude_none=True)
            # `media` is the one nested dimension — merge it a level deeper so
            # toggling `image` alone doesn't wipe a stored max_calls_per_turn
            # (which high_risk_caps would then re-derive from its default).
            media_patch = patch.pop("media", None)
            merged_caps.update(patch)
            if media_patch is not None:
                raw_media = merged_caps.get("media")
                merged_media = dict(raw_media) if isinstance(raw_media, dict) else {}
                merged_media.update(media_patch)
                merged_caps["media"] = merged_media
            merged_profile["capabilities"] = merged_caps
            caps_audit = {"before": before_caps, "after": merged_caps}

        updates["capability_profile"] = merged_profile

        # Audit row (2026-08-10 spec §3): RESOLVED before/after snapshot of
        # both permission subtrees — never the raw JSONB — so what's stored
        # in agent_permission_audits matches what enforcement actually reads.
        # Resolved snapshots are fail-closed, so an agent whose raw profile
        # lacks a key still resolves to explicit defaults; comparing raw
        # dicts would then flag an unchanged Save as a "change" (raw {} ->
        # raw {defaults}). Skip the audit row when the RESOLVED before/after
        # are equal — that's the audit table's semantics: no observed change
        # in runtime behavior, no row. The profile itself still updates.
        resolved_before = _resolved_snapshot(existing_profile)
        resolved_after = _resolved_snapshot(merged_profile)
        if resolved_before != resolved_after:
            permission_audit = {
                "agent_id": agent_uuid,
                "changed_by": user_uuid,
                "before_json": resolved_before,
                "after_json": resolved_after,
                "reason": payload.permission_change_reason,
            }

    override_team_ctx: Optional[int] = None
    if override_updates:
        scope = payload.override_scope or "user"
        if scope == "team":
            if payload.override_team_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="override_scope='team' requires override_team_id",
                )
            allowed = await _is_team_owner(user_uuid, payload.override_team_id)
            if not allowed:
                try:
                    allowed = await _user_is_admin(user_uuid)
                except Exception:  # noqa: BLE001 — fail closed
                    allowed = False
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="team overrides require team owner or platform admin",
                )
            override_team_ctx = payload.override_team_id
            ok = await agent_repo.upsert_override(
                agent_uuid, override_updates, team_id=payload.override_team_id
            )
        else:
            ok = await agent_repo.upsert_override(
                agent_uuid, override_updates, user_id=user_uuid
            )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="failed to save agent override",
            )
        logger.info(
            f"agent override upserted by {auth.user_id} on {agent['slug']} "
            f"scope={scope} fields={sorted(override_updates)}"
        )

    if updates:
        await agent_repo.update_fields_versioned(
            agent_uuid, updates, created_by=user_uuid, permission_audit=permission_audit
        )
    if payload.skill_ids is not None:
        await agent_repo.update_skill_bindings(agent_uuid, payload.skill_ids)

    # Audit the grant/change, not just denials (CHAT-PERM-21 / review M3).
    if chat_audit is not None:
        logger.info(
            f"chat_permissions changed by {auth.user_id} on agent {agent['slug']}: {chat_audit}"
        )
    if caps_audit is not None:
        logger.info(
            f"capabilities granted/changed by {auth.user_id} on agent {agent['slug']}: {caps_audit}"
        )

    refreshed = await agent_repo.get_by_slug(
        slug,
        override_user_id=user_uuid,
        override_team_id=override_team_ctx,
    )
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="agent disappeared after update",
        )
    row = _with_resolved_permissions(
        {**refreshed, "skill_ids": await agent_repo.get_skill_ids(agent_uuid)}
    )
    enriched = await _enrich_agents_with_scope_names([row])
    return enriched[0]


@router.get(
    "/agents/{slug}/permission-audits",
    response_model=PermissionAuditListOut,
    summary="List an agent's permission-change audit trail (read-only)",
)
async def list_agent_permission_audits(
    slug: str,
    auth: AuthDep,
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    """Most-recent-first audit trail of chat_permissions/capabilities changes
    for one agent (2026-08-10 spec §3). Gated by the SAME role check as the
    write path (``_can_edit_chat_permissions``) — whoever can grant a
    permission can also see its history, no one else."""
    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        )
    user_uuid = _coerce_user_uuid(auth.user_id)
    if not await _can_edit_chat_permissions(agent_repo, agent, user_uuid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not allowed to view this agent's permission audit trail",
        )
    agent_uuid = UUID(str(agent["id"]))
    rows = await agent_repo.list_permission_audits(agent_uuid, limit=limit)
    return {"items": [PermissionAuditItem(**row) for row in rows]}


@router.delete(
    "/agents/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user-owned agent (system presets cannot be deleted)",
)
async def delete_agent(slug: str, auth: AuthDep) -> None:
    """Hard-delete a NON-PRESET agent the caller created.

    - 404: no such agent.
    - 403: system preset (seed files stay authoritative — reset an
      override instead) or the caller is not the creator.
    Every FK referencing ai_agents cascades or nulls out, so runs /
    skills bindings / overrides clean themselves up; direct-chat
    sessions survive with a null agent join (history stays readable).
    """
    agent_repo, _ = _repos()
    user_uuid = _coerce_user_uuid(auth.user_id)

    agent = await agent_repo.get_by_slug(slug)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"agent '{slug}' not found",
        )
    if agent.get("is_system_preset"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system presets cannot be deleted — reset the override instead",
        )
    if str(agent.get("user_id") or "") != str(user_uuid):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the agent's creator can delete it",
        )
    deleted = await agent_repo.delete_agent(UUID(str(agent["id"])))
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="delete failed — see server logs",
        )
    logger.info(f"agent '{slug}' deleted by {auth.user_id}")


@router.delete(
    "/agents/{slug}/override",
    response_model=AgentOut,
    summary="Reset a system preset to its defaults (drop the caller's override)",
)
async def delete_agent_override(
    slug: str,
    auth: AuthDep,
    scope: str = "user",
    team_id: Optional[int] = None,
) -> Dict[str, Any]:
    """复位: drop the caller's customization layer so the agent falls back to
    the admin/system defaults. scope='user' (default) drops the personal
    layer; scope='team' (+team_id, team owner or platform admin) drops the
    shared team layer. Returns the refreshed (merged) agent."""
    if scope not in ("user", "team"):
        raise HTTPException(status_code=400, detail="scope must be user|team")
    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        )
    agent_uuid = UUID(str(agent["id"]))
    user_uuid = _coerce_user_uuid(auth.user_id)

    if scope == "team":
        if team_id is None:
            raise HTTPException(status_code=400, detail="scope='team' requires team_id")
        allowed = await _is_team_owner(user_uuid, team_id)
        if not allowed:
            try:
                allowed = await _user_is_admin(user_uuid)
            except Exception:  # noqa: BLE001 — fail closed
                allowed = False
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="team overrides require team owner or platform admin",
            )
        await agent_repo.delete_override(agent_uuid, team_id=team_id)
    else:
        await agent_repo.delete_override(agent_uuid, user_id=user_uuid)

    refreshed = await agent_repo.get_by_slug(
        slug, override_user_id=user_uuid, override_team_id=team_id
    )
    row = _with_resolved_permissions(
        {**refreshed, "skill_ids": await agent_repo.get_skill_ids(agent_uuid)}
    )
    enriched = await _enrich_agents_with_scope_names([row])
    return enriched[0]


@router.post(
    "/agents/{slug}/resume",
    response_model=AgentOut,
    summary="Clear paused_reason (resume agent from manual or budget pause)",
)
async def resume_agent(slug: str, auth: AuthDep) -> Dict[str, Any]:
    """Resume a paused agent by setting paused_reason = null.

    Works for both 'manual' and 'budget' pauses. If the agent is still over
    its monthly budget, the heartbeat sweeper will re-flip paused_reason to
    'budget' within 60s — callers should raise the budget before resuming
    to avoid the flap. 400 when the agent isn't paused; 403 for presets;
    404 when not found.

    Dedicated endpoint (rather than PATCH {paused_reason: null}) so the
    router's exclude_none PATCH semantics stay uniform for every other
    field.
    """
    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        )
    if agent.get("is_system_preset"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system preset agents are read-only in phase 1",
        )
    if agent.get("paused_reason") is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="agent is not paused",
        )

    agent_uuid = UUID(str(agent["id"]))
    await agent_repo.update_fields(agent_uuid, {"paused_reason": None})

    refreshed = await agent_repo.get_by_slug(slug)
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="agent disappeared after resume",
        )
    row = {**refreshed, "skill_ids": await agent_repo.get_skill_ids(agent_uuid)}
    enriched = await _enrich_agents_with_scope_names([row])
    return enriched[0]


@router.post(
    "/agents/{slug}/pause",
    response_model=AgentOut,
    summary="Pause agent (sets paused_reason='manual')",
)
async def pause_agent(slug: str, auth: AuthDep) -> Dict[str, Any]:
    """Manually pause an agent — mirror of /resume (paperclip's Pause action).

    Sets paused_reason='manual'. RunRecorder's pre-flight check raises
    AgentPausedError before any LLM call while this is set, so a paused
    agent stops doing work immediately (between runs). 400 when already
    paused; 403 for presets; 404 when not found.
    """
    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        )
    if agent.get("is_system_preset"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system preset agents are read-only in phase 1",
        )
    if agent.get("paused_reason") is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="agent is already paused",
        )

    agent_uuid = UUID(str(agent["id"]))
    await agent_repo.update_fields(agent_uuid, {"paused_reason": "manual"})

    refreshed = await agent_repo.get_by_slug(slug)
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="agent disappeared after pause",
        )
    row = {**refreshed, "skill_ids": await agent_repo.get_skill_ids(agent_uuid)}
    enriched = await _enrich_agents_with_scope_names([row])
    return enriched[0]


@router.get(
    "/agents/{slug}/status",
    summary="Live agent status chip (idle / running / paused)",
)
async def get_agent_status(slug: str, auth: AuthDep) -> Dict[str, Any]:
    """Derived status for the agent header chip (paperclip-style).

    paused_reason set → 'paused'; else any of the caller's runs currently
    status='running' → 'running'; else 'idle'. Scoped to the caller's runs
    (same scoping as the Runs tab) so one user's chat doesn't light up the
    chip for everyone.
    """
    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        )
    if agent.get("paused_reason"):
        return {
            "status": "paused",
            "paused_reason": agent["paused_reason"],
            "running_count": 0,
        }

    user_uuid = _coerce_user_uuid(auth.user_id)
    from sqlalchemy import func, select

    from app.db.session import read_scope
    from app.models import AgentRuns

    async with read_scope() as session:
        running = (
            await session.execute(
                select(func.count())
                .select_from(AgentRuns)
                .where(AgentRuns.agent_id == str(agent["id"]))
                .where(AgentRuns.user_id == str(user_uuid))
                .where(AgentRuns.status == "running")
            )
        ).scalar() or 0
    return {
        "status": "running" if running > 0 else "idle",
        "paused_reason": None,
        "running_count": running,
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@router.get("/skills", response_model=List[SkillOut], summary="List accessible skills")
async def list_skills(request: Request, auth: AuthDep) -> List[Dict[str, Any]]:
    """Return skills visible to the current user, each enriched with its files.

    Visible set = public (system) skills + user's own private + skills scoped
    to the active team (narrowed by ``X-Team-Id`` header when present, else
    all teams the user is a member of) + user's project-scoped skills.
    """
    _, skill_repo = _repos()
    user_uuid = _coerce_user_uuid(auth.user_id)
    user_team_ids = await _fetch_user_team_ids(user_uuid)
    project_ids = await _fetch_user_project_ids(user_uuid)
    scoped_team = _scoped_team_id(request, user_team_ids)
    team_ids = [scoped_team] if scoped_team is not None else user_team_ids
    skills = await skill_repo.list_accessible(
        user_id=user_uuid,
        team_ids=team_ids,
        project_ids=project_ids,
    )

    valid: List[tuple[int, Dict[str, Any]]] = []
    for s in skills:
        try:
            valid.append((int(s["id"]), s))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(f"[ai-library] skipping skill with bad id: {exc}")

    # One JOIN for the page — the gallery shows "used by N agents" (and the
    # orphan warning) on every card, so a per-skill lookup would be N+1.
    binding_agents = await skill_repo.map_binding_agents([sid for sid, _ in valid])

    enriched: List[Dict[str, Any]] = []
    for skill_id, s in valid:
        files = await skill_repo.list_files(skill_id)
        enriched.append(
            {**s, "files": files, "agents": binding_agents.get(skill_id, [])}
        )
    return await _enrich_skills_with_scope_names(enriched)


@router.get(
    "/skills/{slug}", response_model=SkillOut, summary="Get a single skill by slug"
)
async def get_skill(slug: str, auth: AuthDep) -> Dict[str, Any]:
    """Fetch a single skill (with its files) by slug, 404 if missing.

    Response includes ``team_name`` / ``project_name`` for scope display.
    """
    _, skill_repo = _repos()
    skill = await skill_repo.get_by_slug(slug)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="skill not found"
        )
    skill_id = int(skill["id"])
    row = {
        **skill,
        "files": await skill_repo.list_files(skill_id),
        "agents": await skill_repo.list_binding_agents(skill_id),
    }
    enriched = await _enrich_skills_with_scope_names([row])
    return enriched[0]


@router.post(
    "/skills",
    response_model=SkillOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user-owned skill (optionally forked)",
)
async def create_skill(
    payload: SkillCreate,
    auth: AuthDep,
) -> Dict[str, Any]:
    """Create a non-preset skill owned by the current user.

    Scope:
      * No ``team_id`` / ``project_id``: private per-user skill (default).
      * ``team_id`` set: visible to all team members. Caller must be a member.
      * ``project_id`` set: visible to project owner + members. Caller must
        be the owner or a member.
      * ``team_id`` + ``project_id`` both set → 400 (mutually exclusive).

    If ``fork_from`` is set, copies body_md / frontmatter_json / category /
    icon / output_format / description from that skill as a starting point.
    Skill files (the auxiliary ``skill_files`` rows) are NOT forked — only
    the primary SKILL.md body + metadata. Explicit payload fields override
    forked values.
    """
    _, skill_repo = _repos()
    user_uuid = _coerce_user_uuid(auth.user_id)

    # Scope validation — schema already rejected both-set, but we still want
    # the HTTP-level 400 / 403 distinctions. Pydantic's model_validator raises
    # 422, so we handle the user-friendlier variant first via the payload.
    if payload.team_id is not None:
        if not await _user_is_team_member(user_uuid, payload.team_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"user is not a member of team {payload.team_id}",
            )
    if payload.project_id is not None:
        if not await _user_can_write_project(user_uuid, payload.project_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"user is not the owner or a member of project {payload.project_id}"
                ),
            )

    # Slug must be unique
    if await skill_repo.get_by_slug(payload.slug):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"skill slug '{payload.slug}' already exists",
        )

    # Base fields. User-created skills are private by default (is_public=False),
    # status='active', and owned by the creator.
    # Dual-write body_md → content_md so legacy readers (the /api/v1/skills
    # endpoints) can still see the body. See migration 152.
    fields: Dict[str, Any] = {
        "slug": payload.slug,
        "name": payload.name,
        "description": payload.description,
        "category": payload.category,
        "icon": payload.icon if payload.icon is not None else "✨",
        "body_md": payload.body_md,
        "content_md": payload.body_md,
        "frontmatter_json": payload.frontmatter_json or {},
        "output_format": payload.output_format,
        "is_public": False,
        "status": "active",
        "created_by": str(user_uuid),
        "team_id": payload.team_id,
        "project_id": payload.project_id,
    }

    # If fork_from given, copy body/metadata from source (source may be a
    # system preset — we're only READING its fields).
    if payload.fork_from:
        source = await skill_repo.get_by_slug(payload.fork_from)
        if not source:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"fork source skill '{payload.fork_from}' not found",
            )
        for key in (
            "body_md",
            "frontmatter_json",
            "category",
            "icon",
            "output_format",
            "description",
        ):
            if source.get(key) is not None:
                fields[key] = source[key]

    # Explicit payload field overrides win over forked values.
    for key in (
        "description",
        "category",
        "icon",
        "body_md",
        "frontmatter_json",
        "output_format",
    ):
        val = getattr(payload, key)
        if val is not None:
            fields[key] = val

    # Resync content_md with the final resolved body_md (after fork + overrides).
    fields["content_md"] = fields.get("body_md")

    created = await skill_repo.insert(fields)
    skill_id = int(created["id"])
    files = await skill_repo.list_files(skill_id)
    enriched = await _enrich_skills_with_scope_names([{**created, "files": files}])
    return enriched[0]


@router.patch(
    "/skills/{slug}",
    response_model=SkillOut,
    summary="Update skill fields",
)
async def update_skill(
    slug: str,
    payload: SkillUpdate,
    auth: AuthDep,
) -> Dict[str, Any]:
    """Patch a skill's mutable fields.

    Phase 1 policy: system-preset skills (public + no project owner) are
    read-only (403).
    """
    _, skill_repo = _repos()
    skill = await skill_repo.get_by_slug(slug)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="skill not found"
        )
    if _is_system_skill(skill):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system preset skills are read-only in phase 1",
        )

    skill_id = int(skill["id"])
    updates = payload.model_dump(exclude_none=True)
    # Keep legacy content_md in sync with body_md edits so the /api/v1/skills
    # readers see the latest text. See migration 152.
    if "body_md" in updates:
        updates["content_md"] = updates["body_md"]
    if updates:
        user_uuid = _coerce_user_uuid(auth.user_id)
        await skill_repo.update_fields_versioned(
            skill_id, updates, created_by=user_uuid
        )

    refreshed = await skill_repo.get_by_slug(slug)
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="skill disappeared after update",
        )
    row = {**refreshed, "files": await skill_repo.list_files(skill_id)}
    enriched = await _enrich_skills_with_scope_names([row])
    return enriched[0]


async def _user_is_admin(user_id: UUID) -> bool:
    """Return True iff the user has role='admin' in user_profiles.

    Mirrors ``AdminAuthDep`` but as an inline check so we can combine
    owner-OR-admin authorization in a single route without double-dep.
    """
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import UserProfiles
    from app.repositories._orm_helpers import _plain

    async with read_scope() as session:
        row = (
            await session.execute(
                select(UserProfiles.role)
                .where(UserProfiles.id == str(user_id))
                .limit(1)
            )
        ).first()
    if row is None:
        return False
    return _plain(row[0]) == "admin"


async def _is_team_owner(user_uuid, team_id) -> bool:
    """Return True iff user has role 'owner' or 'admin' in team_members for team_id.

    Fail closed (False) on any error or missing data.
    """
    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import TeamMembers

        async with read_scope() as session:
            row = (
                await session.execute(
                    select(TeamMembers.role)
                    .where(TeamMembers.team_id == int(team_id))
                    .where(TeamMembers.user_id == str(user_uuid))
                    .limit(1)
                )
            ).first()
        if row is None:
            return False
        return row[0] in ("owner", "admin")
    except Exception as exc:
        logger.warning(
            f"_is_team_owner check failed for user={user_uuid} team={team_id}, denying: {exc}"
        )
        return False


async def _can_edit_chat_permissions(
    agent_repo, agent: Dict[str, Any], user_uuid
) -> bool:
    """CHAT-PERM-19: who may edit an agent's chat permissions.

    - Agent owner (user-scoped agent) → allowed.
    - Owner of the agent's scope team → allowed.
    - Platform admin → allowed (covers system presets / platform-scope agents).
    """
    owner_id = agent.get("user_id")
    if owner_id is not None and str(owner_id) == str(user_uuid):
        return True
    team_id = agent.get("team_id")
    if team_id is not None and await _is_team_owner(user_uuid, team_id):
        return True
    # Fail-closed: a transient DB error on user_profiles must not surface as
    # a 500. Only _user_is_admin is wrapped here; _is_team_owner already has
    # its own try/except. The shared _user_is_admin helper keeps its
    # raise-on-error semantics for callers that want propagation (e.g. the
    # delete_skill admin gate).
    try:
        return await _user_is_admin(user_uuid)
    except Exception as exc:
        logger.warning(
            f"_user_is_admin check failed for user={user_uuid}, denying: {exc}"
        )
        return False


def _with_resolved_permissions(row: Dict[str, Any]) -> Dict[str, Any]:
    """Inject the resolved (fail-closed) permission subtrees so AgentOut
    reflects storage. AgentOut has no capability_profile field, so without this
    the response would always serialize the all-denied defaults — and the
    settings UI would render toggles that never show what is actually granted.

    Both projections are built by the SAME parsers the gates enforce with
    (``agent_chat_caps`` / ``high_risk_caps``), so what a client reads back is
    what the runtime will allow — not an independent re-reading of the JSONB
    that could drift from enforcement.
    """
    chat = agent_chat_caps(row)
    high_risk = high_risk_caps(row)
    return {
        **row,
        "chat_permissions": ChatPermissionsOut.from_caps(chat).model_dump(),
        "capabilities": CapabilitiesOut.from_caps(high_risk).model_dump(),
    }


@router.delete(
    "/skills/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a skill",
)
async def delete_skill(slug: str, auth: AuthDep) -> None:
    """Hard-delete a skill by slug.

    Authorization:
      * User-owned skills: only the ``created_by`` user can delete.
      * System-preset skills (``is_public`` + no team + no project):
        admin-only.

    Cascade: migration 138's FK constraints (``skill_files.skill_id``,
    ``agent_skills.skill_id``) both declare ``ON DELETE CASCADE`` — no
    manual cleanup needed.
    """
    _, skill_repo = _repos()
    skill = await skill_repo.get_by_slug(slug)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="skill not found"
        )

    user_uuid = _coerce_user_uuid(auth.user_id)
    is_preset = _is_system_skill(skill)
    created_by = skill.get("created_by")

    if is_preset:
        # System presets: admin-only.
        if not await _user_is_admin(user_uuid):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="only admins can delete system preset skills",
            )
    else:
        # User-owned: only the creator can delete.
        if created_by is None or str(created_by) != str(user_uuid):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="only the owner can delete this skill",
            )

    await skill_repo.delete(int(skill["id"]))


# ---------------------------------------------------------------------------
# Skill files
# ---------------------------------------------------------------------------


@router.get(
    "/skills/{slug}/files",
    response_model=List[SkillFileOut],
    summary="List a skill's files",
)
async def list_skill_files(slug: str, auth: AuthDep) -> List[Dict[str, Any]]:
    """Return every ``skill_files`` row associated with ``slug``."""
    _, skill_repo = _repos()
    skill = await skill_repo.get_by_slug(slug)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="skill not found"
        )
    return await skill_repo.list_files(int(skill["id"]))


@router.put(
    "/skills/{slug}/files/{path:path}",
    response_model=SkillFileOut,
    summary="Create or update a skill file",
)
async def upsert_skill_file(
    slug: str,
    path: str,
    payload: SkillFileUpsert,
    auth: AuthDep,
) -> Dict[str, Any]:
    """Upsert a file row under the skill identified by ``slug``.

    Path conflict resolution happens Postgres-side via the unique index on
    ``(skill_id, path)`` (see migration 139).
    """
    _, skill_repo = _repos()
    skill = await skill_repo.get_by_slug(slug)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="skill not found"
        )
    if _is_system_skill(skill):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system preset skills are read-only in phase 1",
        )

    # URL-decoded path from FastAPI; prefer the URL's path over payload.path
    # to keep routing + persistence in lockstep.
    skill_id = int(skill["id"])
    user_uuid = _coerce_user_uuid(auth.user_id)

    # Skill scanner: non-blocking warning for now. Findings are logged
    # + surfaced in the response so the UI can show a security badge.
    # A future deployment can enable strict-block by checking
    # has_blocking_findings() and raising 422 here.
    scan_findings: List[Dict[str, Any]] = []
    if payload.content:
        try:
            from app.boundary import skill_scanner

            findings = skill_scanner.scan(payload.content)
            scan_findings = skill_scanner.to_dict_list(findings)
            if findings:
                logger.warning(
                    f"[skill_scanner] {slug}/{path}: {len(findings)} finding(s) "
                    f"(highest={findings[0].severity.value}); upload allowed"
                )
        except Exception as exc:
            # Scanner failure must not block legitimate uploads
            logger.warning(f"[skill_scanner] failed (non-fatal): {exc}")

    result = await skill_repo.upsert_file_versioned(
        skill_id=skill_id,
        path=path,
        content=payload.content,
        file_type=payload.file_type,
        binary_url=payload.binary_url,
        created_by=user_uuid,
    )
    # Tack scanner findings onto the response (extra field — caller
    # can ignore safely if not present)
    if isinstance(result, dict):
        result = {**result, "security_findings": scan_findings}
    return result


@router.delete(
    "/skills/{slug}/files/{path:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a skill file",
)
async def delete_skill_file(slug: str, path: str, auth: AuthDep) -> None:
    """Hard-delete the ``skill_files`` row identified by ``(slug, path)``."""
    _, skill_repo = _repos()
    skill = await skill_repo.get_by_slug(slug)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="skill not found"
        )
    if _is_system_skill(skill):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system preset skills are read-only in phase 1",
        )
    await skill_repo.delete_file(int(skill["id"]), path)


# ---------------------------------------------------------------------------
# Admin: reload seeds from disk
# ---------------------------------------------------------------------------


@router.post("/admin/reload-seeds", response_model=Dict[str, Any])
async def reload_seeds(auth: AdminAuthDep) -> Dict[str, Any]:
    """Re-run SeedLoader.load_all() against backend/seeds/.

    Admin-only. Returns the same dict shape as the startup path:
    ``{"agents": N, "skills": M, "agent_skill_bindings": K, "errors": [...]}``.

    Intended for recovery after a startup seed failure — see
    ``docs/superpowers/plans/2026-04-21-ai-library-phase2-seed-loader-fix.md``.

    Returns 500 (not 403) on DB-level failures — see AdminAuthDep.
    """
    # AdminAuthDep raises 403 before body executes — no manual guard needed.
    agent_repo, skill_repo = _repos()
    # Path math: this file is at backend/app/api/ai_library_router.py,
    # so .parent.parent.parent / "seeds" points at backend/seeds/.
    seeds_root = Path(__file__).resolve().parent.parent.parent / "seeds"
    logger.info(f"reload_seeds: invoked by {auth.user_id}, seeds_root={seeds_root}")
    loader = SeedLoader(
        agent_repo=agent_repo,
        skill_repo=skill_repo,
        seeds_root=seeds_root,
    )
    results = await loader.load_all()
    logger.info(f"reload_seeds: completed, {results}")
    return results


# ---------------------------------------------------------------------------
# Agent Runs (telemetry read paths — writes go through RunRecorder)
# ---------------------------------------------------------------------------


def _row_to_run_list_item(row: Dict[str, Any]) -> Dict[str, Any]:
    """Slim projection for list endpoints — drops heavy metadata_json."""
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "status": row["status"],
        "trigger": row["trigger"],
        "model": row.get("model"),
        "provider": row.get("provider"),
        "prompt_tokens": row.get("prompt_tokens", 0),
        "completion_tokens": row.get("completion_tokens", 0),
        "total_tokens": row.get("total_tokens", 0),
        "cost_cents": (
            float(row["cost_cents"]) if row.get("cost_cents") is not None else None
        ),
        "started_at": row["started_at"],
        "ended_at": row.get("ended_at"),
        "error_code": row.get("error_code"),
        "skill_slugs_used": row.get("skill_slugs_used") or [],
        # Phase 3a/3b/4 of #199: surface parent_run_id so the Runs UI
        # can render sub-spawn hierarchy without an extra round-trip.
        "parent_run_id": row.get("parent_run_id"),
        # mig 331: lets the Runs UI tie a turn back to its conversation.
        "conversation_id": row.get("conversation_id"),
    }


def _month_bounds(month: str) -> tuple[str, str]:
    """Parse 'YYYY-MM' into (month_start_iso, next_month_start_iso)."""
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    try:
        parsed = _dt.strptime(month, "%Y-%m").replace(tzinfo=_tz.utc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="month must be YYYY-MM") from exc
    if parsed.month == 12:
        next_month = parsed.replace(year=parsed.year + 1, month=1)
    else:
        next_month = parsed.replace(month=parsed.month + 1)
    return parsed.isoformat(), next_month.isoformat()


@router.get(
    "/agents/{slug}/dashboard",
    summary="Per-agent dashboard aggregate (Paperclip-style)",
)
async def get_agent_dashboard(slug: str, auth: AuthDep) -> Dict[str, Any]:
    """One fat endpoint that backs the AgentEditor → Dashboard tab.

    Aggregates over the last 14 days, scoped to the authenticated user
    (same scoping as ``/agents/:slug/runs`` so the numbers match what
    the user sees in the Runs tab).

    Returns:
        agent: slim header (slug, name, icon, model, persistent flag,
            paused_reason).
        latest_run: most recent agent_runs row (or None).
        run_activity_14d: [{date, count}] — one entry per day, oldest
            first, zeros included so the bar chart renders flat tail.
        tasks_by_status_14d: lifecycle_status → count over 14d.
        success_rate_14d: [{date, success, total}] daily.
        costs_14d: prompt_tokens / completion_tokens / total_cost_cents
            summed over 14d.
        recent_tasks: 5 most recent agent_tasks rows.
        recent_runs: 10 most recent slim agent_runs rows.
    """
    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")

    user_uuid = _coerce_user_uuid(auth.user_id)
    agent_uuid = UUID(str(agent["id"]))

    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import AgentRuns, TaskTracking

    now = datetime.now(timezone.utc)
    # 14-day window inclusive of today: midnight of (today - 13 days)
    # through now. Bucketing keys are date-only ISO strings, so we want
    # day 0 = 13 days ago and day 13 = today.
    window_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=13
    )

    # Pull 14d of runs in one shot. Bounded — even busy agents rarely
    # break a few hundred runs/2wk; bucketing in Python beats issuing
    # 14 + 14 + N PostgREST calls.
    async with read_scope() as session:
        run_rows = (
            (
                await session.execute(
                    select(
                        AgentRuns.id,
                        AgentRuns.status,
                        AgentRuns.trigger,
                        AgentRuns.model,
                        AgentRuns.started_at,
                        AgentRuns.ended_at,
                        AgentRuns.prompt_tokens,
                        AgentRuns.completion_tokens,
                        AgentRuns.cost_cents,
                    )
                    .where(AgentRuns.agent_id == str(agent_uuid))
                    .where(AgentRuns.user_id == str(user_uuid))
                    .where(AgentRuns.started_at >= window_start)
                    .order_by(AgentRuns.started_at.desc())
                )
            )
            .mappings()
            .all()
        )
    # started_at → ISO str: the daily bucketing below slices ``started[:10]``.
    runs_14d: List[Dict[str, Any]] = [_serialize_row(r) for r in run_rows]

    # Most recent run, regardless of window. The dashboard shows a
    # banner even when the user hasn't run anything in 2 weeks.
    async with read_scope() as session:
        latest_rows = (
            (
                await session.execute(
                    select(
                        AgentRuns.id,
                        AgentRuns.status,
                        AgentRuns.trigger,
                        AgentRuns.model,
                        AgentRuns.started_at,
                        AgentRuns.ended_at,
                        AgentRuns.prompt_tokens,
                        AgentRuns.completion_tokens,
                        AgentRuns.cost_cents,
                        AgentRuns.input_summary,
                        AgentRuns.output_summary,
                        AgentRuns.error_code,
                        AgentRuns.error_message,
                    )
                    .where(AgentRuns.agent_id == str(agent_uuid))
                    .where(AgentRuns.user_id == str(user_uuid))
                    .order_by(AgentRuns.started_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .all()
        )
    latest_run = _serialize_row(latest_rows[0]) if latest_rows else None
    # agent_runs.id is a BIGINT Snowflake (mig 232). This endpoint returns a
    # raw Dict (no response_model), so unlike the run list/detail endpoints —
    # whose RunListItem opts into coerce_numbers_to_str — it would otherwise
    # leak the id as a JS number (precision loss >2^53, and the frontend
    # `id.slice()` crash). Coerce to str here, matching the RunListItem contract.
    if latest_run and latest_run.get("id") is not None:
        latest_run["id"] = str(latest_run["id"])

    # 14-day daily series — pre-fill with zeros so the chart's x axis
    # stays continuous when there are gaps. ``window_start`` is already
    # midnight of (today - 13d), so day 13 is today.
    days: List[str] = [
        (window_start + timedelta(days=i)).date().isoformat() for i in range(14)
    ]

    activity_buckets: Counter[str] = Counter()
    success_buckets: Dict[str, Dict[str, int]] = {
        d: {"success": 0, "total": 0} for d in days
    }
    sum_prompt = 0
    sum_completion = 0
    sum_cost_cents = 0.0
    for r in runs_14d:
        started = r.get("started_at")
        if not started:
            continue
        # ISO from PostgREST always YYYY-MM-DDTHH:MM:SS+HH:MM
        date_key = started[:10]
        activity_buckets[date_key] += 1
        if date_key in success_buckets:
            success_buckets[date_key]["total"] += 1
            if r.get("status") == "completed":
                success_buckets[date_key]["success"] += 1
        sum_prompt += int(r.get("prompt_tokens") or 0)
        sum_completion += int(r.get("completion_tokens") or 0)
        cost = r.get("cost_cents")
        if cost is not None:
            try:
                sum_cost_cents += float(cost)
            except (TypeError, ValueError):
                pass

    run_activity_14d = [{"date": d, "count": activity_buckets.get(d, 0)} for d in days]
    success_rate_14d = [{"date": d, **success_buckets[d]} for d in days]

    # Tasks: status counts over 14d, scoped by agent_id + user_id. Counts
    # EVERY task the agent worked, regardless of task_kind: chat-Delegate
    # dispatches (task_kind='agent_task') AND service workflows (visual
    # analysis / summary, task_kind='workflow') whose RunRecorder stamped
    # agent_id back onto the row (mig 282 paperclip-style task↔run linkage).
    # The old task_kind='agent_task' filter hid all service work, so an
    # agent that only ran analyses showed an empty task panel.
    async with read_scope() as session:
        tasks_rows = (
            (
                await session.execute(
                    select(
                        TaskTracking.dbos_workflow_id,
                        TaskTracking.phase,
                        TaskTracking.created_at,
                        TaskTracking.title,
                        TaskTracking.metadata_.label("metadata"),
                    )
                    .where(TaskTracking.agent_id == str(agent_uuid))
                    .where(TaskTracking.user_id == str(user_uuid))
                    .where(TaskTracking.created_at >= window_start)
                    .order_by(TaskTracking.created_at.desc())
                )
            )
            .mappings()
            .all()
        )
    tasks_14d: List[Dict[str, Any]] = [
        tt_row_to_task_shape(_serialize_row(r)) for r in tasks_rows
    ]
    status_counts: Counter[str] = Counter(
        (t.get("lifecycle_status") or "unknown") for t in tasks_14d
    )

    # Recent agent tasks (5) — pulled separately in case the 14d
    # window is empty but older tasks still matter for context.
    # Same agent_id-only scoping as the 14d query above.
    async with read_scope() as session:
        recent_tasks_rows = (
            (
                await session.execute(
                    select(
                        TaskTracking.dbos_workflow_id,
                        TaskTracking.phase,
                        TaskTracking.created_at,
                        TaskTracking.started_at,
                        TaskTracking.completed_at,
                        TaskTracking.title,
                        TaskTracking.error_code,
                        TaskTracking.error_msg,
                        TaskTracking.metadata_.label("metadata"),
                    )
                    .where(TaskTracking.agent_id == str(agent_uuid))
                    .where(TaskTracking.user_id == str(user_uuid))
                    .order_by(TaskTracking.created_at.desc())
                    .limit(5)
                )
            )
            .mappings()
            .all()
        )

        # Recent runs table (10 slim rows, all-time so an idle agent still
        # shows history).
        recent_runs_rows = (
            (
                await session.execute(
                    select(
                        AgentRuns.id,
                        AgentRuns.status,
                        AgentRuns.trigger,
                        AgentRuns.model,
                        AgentRuns.started_at,
                        AgentRuns.ended_at,
                        AgentRuns.prompt_tokens,
                        AgentRuns.completion_tokens,
                        AgentRuns.cost_cents,
                    )
                    .where(AgentRuns.agent_id == str(agent_uuid))
                    .where(AgentRuns.user_id == str(user_uuid))
                    .order_by(AgentRuns.started_at.desc())
                    .limit(10)
                )
            )
            .mappings()
            .all()
        )

    return {
        "agent": {
            "id": str(agent_uuid),
            "slug": agent.get("slug"),
            "name": agent.get("name") or agent.get("slug"),
            "icon": agent.get("icon"),
            "model": agent.get("model"),
            "persistent": bool(agent.get("persistent")),
            "paused_reason": agent.get("paused_reason"),
        },
        "latest_run": latest_run,
        "run_activity_14d": run_activity_14d,
        "tasks_by_status_14d": dict(status_counts),
        "success_rate_14d": success_rate_14d,
        "costs_14d": {
            "prompt_tokens": sum_prompt,
            "completion_tokens": sum_completion,
            "total_tokens": sum_prompt + sum_completion,
            "total_cost_cents": round(sum_cost_cents, 4),
            "run_count": len(runs_14d),
        },
        "recent_tasks": [
            tt_row_to_task_shape(_serialize_row(r)) for r in recent_tasks_rows
        ],
        "recent_runs": [
            {**r, "id": str(r["id"])} if r.get("id") is not None else r
            for r in (_serialize_row(row) for row in recent_runs_rows)
        ],
    }


@router.get(
    "/agents/{slug}/usage",
    summary="Which product modules use this agent (static registry + 30d run evidence)",
)
async def get_agent_usage(slug: str, auth: AuthDep) -> Dict[str, Any]:
    """Backs the AgentEditor → Dashboard "Used by" card.

    Merges the static consuming-module registry
    (:mod:`app.services.ai.agent_usage_registry`) with dynamic evidence:
    agent_runs trigger counts over the last 30 days, direct_agent
    conversation bindings, and agent routines (user_schedules rows with
    task_type='agent_routine'). Run/routine counts are scoped to the
    caller (same scoping as the dashboard tab); conversation count is
    per-agent across the workspace (conversation_ai_meta has no user
    column — it's a backend-only sidecar).
    """
    from app.services.ai.agent_usage_registry import (
        feature_for_trigger,
        modules_for_agent,
    )

    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")

    user_uuid = _coerce_user_uuid(auth.user_id)
    agent_uuid = UUID(str(agent["id"]))

    from sqlalchemy import func, select

    import app.db.session as db_session
    from app.models import AgentRuns, ConversationAiMeta, UserSchedules

    window_days = 30
    window_start = datetime.now(timezone.utc) - timedelta(days=window_days)

    async with db_session.read_scope() as session:
        trigger_rows = (
            (
                await session.execute(
                    select(
                        AgentRuns.trigger,
                        func.count().label("count"),
                    )
                    .where(
                        AgentRuns.agent_id == agent_uuid,
                        AgentRuns.user_id == user_uuid,
                        AgentRuns.started_at >= window_start,
                    )
                    .group_by(AgentRuns.trigger)
                )
            )
            .mappings()
            .all()
        )
        conversation_count = (
            await session.execute(
                select(func.count()).where(ConversationAiMeta.agent_id == agent_uuid)
            )
        ).scalar() or 0
        routine_count = (
            await session.execute(
                select(func.count()).where(
                    UserSchedules.user_id == user_uuid,
                    UserSchedules.task_type == "agent_routine",
                    UserSchedules.payload["agent_slug"].astext == slug,
                )
            )
        ).scalar() or 0

    trigger_counts = sorted(
        (
            {
                "trigger": str(r["trigger"] or "unknown"),
                "feature_key": feature_for_trigger(str(r["trigger"] or "")),
                "count": int(r["count"] or 0),
            }
            for r in trigger_rows
        ),
        key=lambda t: -t["count"],
    )

    return {
        "modules": modules_for_agent(slug),
        "trigger_counts": trigger_counts,
        "conversation_count": int(conversation_count),
        "routine_count": int(routine_count),
        "window_days": window_days,
    }


@router.get(
    "/agents/{slug}/runs",
    response_model=RunListResponse,
    summary="List runs for a single agent (paginated)",
)
async def list_agent_runs(
    slug: str,
    auth: AuthDep,
    limit: int = 50,
    offset: int = 0,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Runs for this agent, scoped to the authenticated user. Newest first.

    ``conversation_id`` narrows to one conversation's turns — the expand
    path of the grouped Runs view (numeric-string Snowflake)."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")
    if conversation_id is not None and not conversation_id.isdigit():
        raise HTTPException(status_code=400, detail="conversation_id must be numeric")

    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")

    runs_repo = get_agent_runs_repository()
    user_uuid = _coerce_user_uuid(auth.user_id)
    agent_uuid = UUID(str(agent["id"]))
    page = await runs_repo.list_by_agent(
        agent_id=agent_uuid,
        user_id=user_uuid,
        limit=limit,
        offset=offset,
        conversation_id=conversation_id,
    )
    return {
        "items": [_row_to_run_list_item(r) for r in page["items"]],
        "total": page["total"],
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/agents/{slug}/run-groups",
    response_model=RunGroupListResponse,
    summary="Conversation-grouped runs for a single agent (paginated)",
)
async def list_agent_run_groups(
    slug: str,
    auth: AuthDep,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """One item per chat conversation (turns rolled up) or per standalone
    run. Fixes the Runs tab flat-listing every turn of one conversation as
    its own record. ``total`` counts groups."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")

    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")

    runs_repo = get_agent_runs_repository()
    user_uuid = _coerce_user_uuid(auth.user_id)
    agent_uuid = UUID(str(agent["id"]))
    page = await runs_repo.list_groups_by_agent(
        agent_id=agent_uuid, user_id=user_uuid, limit=limit, offset=offset
    )
    return {
        "items": [
            {
                **r,
                "cost_cents": (
                    float(r["cost_cents"]) if r.get("cost_cents") is not None else None
                ),
            }
            for r in page["items"]
        ],
        "total": page["total"],
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/runs/live",
    summary="Currently-running agent runs across all agents (Workforce strip)",
)
async def list_live_runs(auth: AuthDep) -> Dict[str, Any]:
    """Caller-scoped status='running' runs, newest first, enriched with the
    agent's slug/name/icon. Powers the Workforce board's "Running now" strip
    (paperclip's live-runs dashboard, R4). NOTE: registered BEFORE
    /runs/{run_id} so the literal path wins route matching."""
    user_uuid = _coerce_user_uuid(auth.user_id)
    # NOTE: agent_runs.task_id (mig 282, TEXT → task_tracking.dbos_workflow_id)
    # is not on the AgentRuns ORM model (drift), so it is referenced via
    # ``column("task_id")`` — renders the same column the supabase-py select did.
    from sqlalchemy import column, select

    from app.db.session import read_scope
    from app.models import AgentRuns, AiAgents

    async with read_scope() as session:
        run_rows = (
            (
                await session.execute(
                    select(
                        AgentRuns.id,
                        AgentRuns.agent_id,
                        AgentRuns.status,
                        AgentRuns.trigger,
                        AgentRuns.model,
                        AgentRuns.started_at,
                        AgentRuns.prompt_tokens,
                        AgentRuns.completion_tokens,
                        AgentRuns.cost_cents,
                        AgentRuns.input_summary,
                        column("task_id"),
                    )
                    .where(AgentRuns.user_id == str(user_uuid))
                    .where(AgentRuns.status == "running")
                    .order_by(AgentRuns.started_at.desc())
                    .limit(20)
                )
            )
            .mappings()
            .all()
        )
    items = [_serialize_row(r) for r in run_rows]
    agent_ids = sorted({str(r["agent_id"]) for r in items})
    agents_by_id: Dict[str, Dict[str, Any]] = {}
    if agent_ids:
        async with read_scope() as session:
            agent_rows = (
                (
                    await session.execute(
                        select(
                            AiAgents.id,
                            AiAgents.slug,
                            AiAgents.name,
                            AiAgents.icon,
                        ).where(AiAgents.id.in_(agent_ids))
                    )
                )
                .mappings()
                .all()
            )
        agents_by_id = {a["id"]: a for a in (_serialize_row(r) for r in agent_rows)}
    for r in items:
        a = agents_by_id.get(str(r["agent_id"])) or {}
        r["id"] = str(r["id"])
        r["task_id"] = str(r["task_id"]) if r.get("task_id") else None
        r["agent_slug"] = a.get("slug")
        r["agent_name"] = a.get("name")
        r["agent_icon"] = a.get("icon")
        if r.get("cost_cents") is not None:
            r["cost_cents"] = float(r["cost_cents"])
    return {"items": items, "count": len(items)}


@router.get(
    "/runs/{run_id}",
    response_model=RunDetail,
    summary="Get run detail",
)
async def get_run(run_id: str, auth: AuthDep) -> Dict[str, Any]:
    """Full run row with metadata_json. 404 if not owned by caller."""
    runs_repo = get_agent_runs_repository()
    user_uuid = _coerce_user_uuid(auth.user_id)
    row = await runs_repo.get_by_id(run_id, user_id=user_uuid)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    for field in (
        "cost_cents",
        "prompt_cents_per_1k_snapshot",
        "completion_cents_per_1k_snapshot",
    ):
        if row.get(field) is not None:
            row[field] = float(row[field])

    # mig 282 task ↔ run linkage: resolve the task_tracking row this run
    # executed under so the detail pane can render "Tasks Touched"
    # (paperclip-style). Best-effort — a missing/stale task never 500s
    # the run detail.
    if row.get("task_id"):
        try:
            from sqlalchemy import select

            from app.db.session import read_scope
            from app.models import TaskTracking

            async with read_scope() as session:
                task = (
                    (
                        await session.execute(
                            select(
                                TaskTracking.dbos_workflow_id,
                                TaskTracking.title,
                                TaskTracking.phase,
                                TaskTracking.task_type,
                            )
                            .where(TaskTracking.dbos_workflow_id == str(row["task_id"]))
                            .limit(1)
                        )
                    )
                    .mappings()
                    .first()
                )
            if task:
                row["task"] = {
                    "id": task["dbos_workflow_id"],
                    "title": task.get("title"),
                    "phase": task.get("phase"),
                    "task_type": task.get("task_type"),
                }
        except Exception as e:  # noqa: BLE001 — decoration, never fatal
            logger.warning(f"[runs] task ref lookup failed for run {run_id}: {e}")
    return row


def _event_type_filter(te: Any, types: str) -> list:
    """``types`` CSV → zero or one ``IN`` clause. Blank tokens are dropped;
    all-blank means no filter (backward compatible)."""
    wanted = [t.strip() for t in (types or "").split(",") if t.strip()]
    return [te.event_type.in_(wanted)] if wanted else []


@router.get(
    "/runs/{run_id}/events",
    summary="Transcript event stream for one run (mig 285, paperclip P3)",
)
async def list_run_events(
    run_id: str,
    auth: AuthDep,
    after_seq: int = 0,
    limit: int = 500,
    types: str = "",
    upto_seq: Optional[int] = None,
) -> Dict[str, Any]:
    """Ordered agent_run_events for the Runs detail Transcript section.

    Ownership enforced the same way as the run detail (a foreign run_id
    reads as 404). ``after_seq`` supports incremental polling while the
    run is live. ``types`` is an optional CSV of event types (e.g.
    ``todo_write,llm_retry``) so a progress poller does not drag every
    assistant body along; empty means all — the consumers that predate it
    keep working unchanged. An unknown type is an empty result, never 500.
    ``upto_seq`` (phase 2b-1 replay) is an INCLUSIVE upper bound — the
    scrubber reads ``events[:seq]`` with it; absent means no bound.
    """
    runs_repo = get_agent_runs_repository()
    user_uuid = _coerce_user_uuid(auth.user_id)
    row = await runs_repo.get_by_id(run_id, user_id=user_uuid)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")

    # agent_run_transcript_events (mig 397, ORM AgentRunTranscriptEvents).
    # NOT `agent_run_events` — that name is the mig-155 cost-audit log; mig
    # 285's attempt to reuse it no-oped on IF NOT EXISTS, which is why this
    # endpoint 500'd with `column "seq" does not exist` until 397.
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import AgentRunTranscriptEvents as TE

    stmt = (
        select(
            TE.seq,
            TE.event_type,
            TE.payload,
            TE.created_at,
            TE.turn,
            TE.step,
        )
        .where(TE.run_id == int(run_id))
        .where(TE.seq > after_seq)
        .where(*_event_type_filter(TE, types))
    )
    if upto_seq is not None:
        stmt = stmt.where(TE.seq <= upto_seq)
    async with read_scope() as session:
        rows = (
            (
                await session.execute(
                    stmt.order_by(TE.seq.asc()).limit(max(1, min(limit, 1000)))
                )
            )
            .mappings()
            .all()
        )
    items = [_serialize_row(r) for r in rows]
    return {"items": items, "count": len(items)}


@router.get(
    "/runs/{run_id}/view-at",
    summary="Folded run.view / run.cost AS OF seq (phase 2b-1 replay)",
)
async def get_run_view_at(run_id: str, auth: AuthDep, seq: int) -> Dict[str, Any]:
    """The same fold registry the recorder runs live, applied to
    ``events[:seq]`` — one fold, never copied to TS. The scrubber calls this
    per tick (steps are few; no cache). Foreign run_id reads as 404."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import AgentRunTranscriptEvents as TE
    from app.services.ai.runner.run_projection import replay

    runs_repo = get_agent_runs_repository()
    row = await runs_repo.get_by_id(run_id, user_id=_coerce_user_uuid(auth.user_id))
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    async with read_scope() as session:
        rows = (
            (
                await session.execute(
                    select(TE.seq, TE.event_type, TE.payload)
                    .where(TE.run_id == int(run_id))
                    .where(TE.seq <= seq)
                    .order_by(TE.seq.asc())
                )
            )
            .mappings()
            .all()
        )
    views = replay(
        [(r["event_type"], r["payload"] or {}) for r in rows],
        seqs=[int(r["seq"]) for r in rows],
    )
    return {"seq": seq, "view": views["view"], "cost": views["cost"]}


@router.get(
    "/runs/{run_id}/children",
    response_model=List[RunListItem],
    summary="List direct sub-runs spawned by this run via the Task tool",
)
async def list_run_children(run_id: str, auth: AuthDep) -> List[Dict[str, Any]]:
    """Phase 4 of issue #199. Direct children only — UI calls
    recursively when it wants a full tree. Returns [] when the parent
    run is unknown / not owned (avoids leaking existence)."""
    runs_repo = get_agent_runs_repository()
    user_uuid = _coerce_user_uuid(auth.user_id)
    # Verify the parent is visible to the caller before exposing
    # children. Without this, a stray run_id from another user would
    # leak the fact that sub-runs exist (count != 0 vs count == 0).
    parent = await runs_repo.get_by_id(run_id, user_id=user_uuid)
    if not parent:
        raise HTTPException(status_code=404, detail="run not found")
    rows = await runs_repo.list_children(run_id, user_id=user_uuid)
    return [_row_to_run_list_item(r) for r in rows]


@router.post(
    "/runs/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request cancellation (runner observes via RunRecorder polling)",
)
async def cancel_run(run_id: str, auth: AuthDep) -> Dict[str, Any]:
    """Flip cancel_requested=true. Idempotent; 404 if not found or not running.

    The cancel is asynchronous. The runner polls cancel_requested between
    tool iterations and flips status to 'cancelled' when observed. If the
    process dies before observing, the heartbeat sweeper marks it
    heartbeat_lost within 2 minutes.
    """
    runs_repo = get_agent_runs_repository()
    user_uuid = _coerce_user_uuid(auth.user_id)
    ok = await runs_repo.request_cancel(run_id, user_id=user_uuid)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="run not found, not running, or not owned by you",
        )
    return {"status": "cancel_requested", "run_id": str(run_id)}


@router.post(
    "/runs/{run_id}/undo",
    response_model=UndoReport,
    summary="Undo everything this run wrote (one-shot, skip + typed report)",
)
async def undo_run(run_id: str, auth: AuthDep) -> Dict[str, Any]:
    """整 run 一键撤销（mig 415 立项）。逐项 CAS：被后续修改碰过的写入
    跳过并报告，永不销毁别人的工作。一次性，无 redo；重复调用返回
    already_undone。运行中的 run 不可撤（先 cancel）。"""
    runs_repo = get_agent_runs_repository()
    user_uuid = _coerce_user_uuid(auth.user_id)
    claim = await runs_repo.claim_undo(run_id, user_id=user_uuid)
    if claim == "not_found":
        raise HTTPException(status_code=404, detail="run not found or not owned by you")
    if claim == "running":
        raise HTTPException(
            status_code=409, detail="run is still running — cancel it first"
        )
    if claim == "already_undone":
        return {
            "status": "already_undone",
            "shots_deleted": 0,
            "shots_reverted": 0,
            "scene_elements_reverted": 0,
            "skipped": [],
        }
    # Module-attribute access (not `from ... import execute_undo`) is
    # deliberate — tests patch `app.services.ai.undo.run_undo_service.
    # execute_undo`, which only takes effect if this call resolves it
    # through the module at call time rather than binding a local name
    # at import time.
    from app.services.ai.undo import run_undo_service

    report = await run_undo_service.execute_undo(int(run_id))
    return {"status": "done", **report}


@router.get(
    "/usage",
    response_model=UsageAggregate,
    summary="Aggregate token / cost usage per agent for a given month",
)
async def get_usage(
    auth: AuthDep,
    month: str,
    scope: str = "user",
    team_id: int | None = None,
    project_id: int | None = None,
) -> Dict[str, Any]:
    """Monthly rollup for Settings → AI Usage.

    - scope='user' (default): caller's own runs
    - scope='team':  runs tagged team_id (caller must belong to team — RLS-enforced)
    - scope='project': runs tagged project_id (RLS-enforced)
    """
    if scope not in ("user", "team", "project"):
        raise HTTPException(status_code=400, detail="scope must be user|team|project")
    if scope == "team" and team_id is None:
        raise HTTPException(status_code=400, detail="team scope requires team_id")
    if scope == "project" and project_id is None:
        raise HTTPException(status_code=400, detail="project scope requires project_id")

    start_iso, end_iso = _month_bounds(month)
    from datetime import datetime as _dt

    runs_repo = get_agent_runs_repository()
    rows = await runs_repo.monthly_usage_by_agent(
        month_start=_dt.fromisoformat(start_iso),
        month_end=_dt.fromisoformat(end_iso),
    )

    user_uuid = _coerce_user_uuid(auth.user_id)
    if scope == "user":
        # str() both sides: REST renders user_id as a JSON str, but an ORM-backed
        # repo could hand back a native uuid.UUID — type-tolerant compare so the
        # user-scope filter never silently returns zero rows.
        rows = [r for r in rows if str(r.get("user_id")) == str(user_uuid)]
    elif scope == "team":
        rows = [r for r in rows if r.get("team_id") == team_id]
    else:
        rows = [r for r in rows if r.get("project_id") == project_id]

    per_agent: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        aid = r["agent_id"]
        bucket = per_agent.setdefault(
            aid,
            {
                "agent_id": aid,
                "run_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_cents": 0.0,
                "failed_count": 0,
            },
        )
        bucket["run_count"] += 1
        bucket["prompt_tokens"] += int(r.get("prompt_tokens") or 0)
        bucket["completion_tokens"] += int(r.get("completion_tokens") or 0)
        bucket["total_tokens"] += int(r.get("total_tokens") or 0)
        if r.get("cost_cents") is not None:
            bucket["cost_cents"] += float(r["cost_cents"])
        if r.get("status") in ("failed", "heartbeat_lost"):
            bucket["failed_count"] += 1

    agent_repo, _ = _repos()
    enriched: list[Dict[str, Any]] = []
    for aid, bucket in per_agent.items():
        try:
            # UUID(str(aid)): aid is a REST str, but an ORM repo could pass a
            # native uuid.UUID — UUID(uuid_obj) raises AttributeError (swallowed
            # below → enrichment silently dropped). str() first is idempotent.
            agent = await agent_repo.get_by_id(UUID(str(aid)))
            if agent:
                bucket["agent_slug"] = agent.get("slug")
                bucket["agent_name"] = agent.get("name")
        except Exception as exc:
            logger.warning(f"[usage] failed to enrich agent {aid}: {exc}")
        enriched.append(bucket)

    return {
        "scope": scope,
        "month": month,
        "total_runs": sum(b["run_count"] for b in enriched),
        "total_tokens": sum(b["total_tokens"] for b in enriched),
        "total_cost_cents": sum(b["cost_cents"] for b in enriched),
        "per_agent": enriched,
    }


@router.get(
    "/usage/runs",
    summary="Caller's own per-call usage detail (paginated)",
)
async def get_usage_runs(
    auth: AuthDep,
    page: int = 1,
    page_size: int = 25,
    model: str | None = None,
    status: str | None = None,
    days: int = 30,
    month: str | None = None,
) -> Dict[str, Any]:
    """Row-level usage for the AI Usage page: every run the CALLER made —
    time / agent / model / provider / tokens / cost / status / duration.

    Hard-scoped to the authenticated user (the repo filter is forced to
    ``auth.user_id`` — there is no way to read another user's runs here).
    ``days`` windows to the last N days (default 30, max 365); ``month``
    (YYYY-MM) switches to a calendar-month window and overrides days."""
    if page < 1 or not (1 <= page_size <= 100):
        raise HTTPException(status_code=400, detail="bad page/page_size")
    if not (1 <= days <= 365):
        raise HTTPException(status_code=400, detail="days must be 1..365")

    started_before: datetime | None = None
    if month is not None:
        start_iso, end_iso = _month_bounds(month)
        started_after = datetime.fromisoformat(start_iso)
        started_before = datetime.fromisoformat(end_iso)
    else:
        started_after = datetime.now(timezone.utc) - timedelta(days=days)

    user_uuid = _coerce_user_uuid(auth.user_id)
    runs_repo = get_agent_runs_repository()
    result = await runs_repo.list_runs_admin(
        user_id=user_uuid,  # forced — user scope, never cross-user
        model=model,
        status=status,
        started_after=started_after,
        started_before=started_before,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    rows = result["items"]

    # Enrich agent_id → slug/name (bounded: unique agents on one page).
    agent_repo, _ = _repos()
    agent_names: Dict[str, Dict[str, Any]] = {}
    for aid in {str(r["agent_id"]) for r in rows if r.get("agent_id")}:
        try:
            agent = await agent_repo.get_by_id(UUID(aid))
            if agent:
                agent_names[aid] = {
                    "agent_slug": agent.get("slug"),
                    "agent_name": agent.get("name"),
                }
        except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
            logger.warning(f"[usage/runs] agent enrich failed for {aid}: {exc}")

    def _dur_ms(r: Dict[str, Any]) -> int | None:
        s, e = r.get("started_at"), r.get("ended_at")
        if not s or not e:
            return None
        try:
            s_dt = s if isinstance(s, datetime) else datetime.fromisoformat(str(s))
            e_dt = e if isinstance(e, datetime) else datetime.fromisoformat(str(e))
            ms = (e_dt - s_dt).total_seconds() * 1000.0
            return int(ms) if ms >= 0 else None
        except (ValueError, TypeError):
            return None

    items = [
        {
            "id": str(r.get("id")),
            "agent_id": str(r["agent_id"]) if r.get("agent_id") else None,
            **agent_names.get(str(r.get("agent_id")), {}),
            "model": r.get("model"),
            "provider": r.get("provider"),
            "status": r.get("status", ""),
            "trigger": r.get("trigger"),
            "prompt_tokens": int(r.get("prompt_tokens") or 0),
            "completion_tokens": int(r.get("completion_tokens") or 0),
            "total_tokens": int(r.get("total_tokens") or 0),
            "cost_cents": float(r.get("cost_cents") or 0.0),
            "duration_ms": _dur_ms(r),
            "started_at": str(r["started_at"]) if r.get("started_at") else None,
            "error_code": r.get("error_code"),
        }
        for r in rows
    ]
    return {
        "items": items,
        "total": result["total"],
        "page": page,
        "page_size": page_size,
    }


@router.get(
    "/usage/daily",
    summary="Caller's own daily usage rollup grouped by model or agent",
)
async def get_usage_daily(
    auth: AuthDep, days: int = 30, group_by: str = "model", month: str | None = None
) -> Dict[str, Any]:
    """Daily buckets powering the usage page's hero chart — grouped by
    ``model`` (default) or ``agent``, with failure counts so the page can
    surface a success rate. Hard-scoped to the caller."""
    if not (1 <= days <= 365):
        raise HTTPException(status_code=400, detail="days must be 1..365")
    if group_by not in ("model", "agent"):
        raise HTTPException(status_code=400, detail="group_by must be model|agent")

    started_before: datetime | None = None
    if month is not None:
        start_iso, end_iso = _month_bounds(month)
        started_after = datetime.fromisoformat(start_iso)
        started_before = datetime.fromisoformat(end_iso)
    else:
        started_after = datetime.now(timezone.utc) - timedelta(days=days)

    user_uuid = _coerce_user_uuid(auth.user_id)
    runs_repo = get_agent_runs_repository()
    rows = await runs_repo.daily_usage(
        started_after=started_after,
        started_before=started_before,
        user_id=user_uuid,
        group_by=group_by,
    )

    # Agent grouping keys are uuids — enrich to display labels (bounded by
    # the caller's distinct agents). Model keys label as themselves.
    labels: Dict[str, str] = {}
    if group_by == "agent":
        agent_repo, _ = _repos()
        for key in {str(r["key"]) for r in rows if r.get("key")}:
            try:
                agent = await agent_repo.get_by_id(UUID(key))
                if agent:
                    labels[key] = agent.get("name") or agent.get("slug") or key
            except Exception as exc:  # noqa: BLE001 — label is cosmetic
                logger.warning(f"[usage/daily] agent label failed for {key}: {exc}")

    daily = [
        {
            "date": str(r.get("date")),
            "key": str(r["key"]) if r.get("key") else None,
            "label": (
                labels.get(str(r.get("key")), str(r.get("key")))
                if r.get("key")
                else None
            ),
            "requests": int(r.get("requests") or 0),
            "failed_requests": int(r.get("failed_requests") or 0),
            "prompt_tokens": int(r.get("prompt_tokens") or 0),
            "completion_tokens": int(r.get("completion_tokens") or 0),
            "total_tokens": int(r.get("total_tokens") or 0),
            "cost_cents": float(r.get("cost_cents") or 0.0),
        }
        for r in rows
    ]
    return {
        "days": days,
        "month": month,
        "group_by": group_by,
        "total_requests": sum(d["requests"] for d in daily),
        "total_failed": sum(d["failed_requests"] for d in daily),
        "total_tokens": sum(d["total_tokens"] for d in daily),
        "total_cost_cents": sum(d["cost_cents"] for d in daily),
        "daily": daily,
    }


# ---------------------------------------------------------------------------
# Chat sessions (replaces legacy /api/v1/ai/sessions + /api/v1/ai/agents/{id}/call)
# ---------------------------------------------------------------------------


@router.post(
    "/agents/{slug}/sessions",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a chat session bound to this agent",
)
async def create_chat_session(
    slug: str, payload: SessionCreate, auth: AuthDep
) -> Dict[str, Any]:
    """Open a new ai_sessions row tied to ``slug``. Every chat message
    in this session will run through AgentRunner + RunRecorder, so
    tokens / cost / budget / pulse all flow through the standard
    telemetry pipeline for chat too.

    A2 review fix (Critical): ``payload.project_id`` used to flow straight
    into ``conversations.project_id`` with no ownership check at all —
    ``agent_runs.project_id`` (and therefore every agent run's bound
    ``AgentRunScope``) is stamped from exactly that column, so an
    unvalidated project_id here was a silent cross-tenant hole one layer
    up the stack from the resolver itself. Reject it here with a clean 403
    rather than let it become a wrong-but-bound scope discovered later.
    ``scope_for_run`` re-checks this independently at derivation time
    (belt-and-suspenders for any OTHER path that stamps a project id), but
    checking here means the caller gets an immediate, actionable error
    instead of a run that silently fails to resolve anything."""
    if payload.project_id is not None:
        await verify_project_read_access(str(payload.project_id), auth)
    svc = AILibraryChatService()
    user_uuid = _coerce_user_uuid(auth.user_id)
    session = await svc.create_session(
        user_id=user_uuid,
        agent_slug=slug,
        title=payload.title,
        project_id=payload.project_id,
        team_id=payload.team_id,
        context_type=payload.context_type,
        context_id=payload.context_id,
    )
    return session


@router.get(
    "/agents/{slug}/sessions",
    response_model=List[SessionOut],
    summary="List chat sessions this caller owns for the given agent",
)
async def list_chat_sessions(
    slug: str,
    auth: AuthDep,
    project_id: Optional[int] = None,
    limit: int = 50,
    search: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    svc = AILibraryChatService()
    user_uuid = _coerce_user_uuid(auth.user_id)
    return await svc.list_sessions(
        user_id=user_uuid,
        agent_slug=slug,
        project_id=project_id,
        limit=limit,
        search=search,
    )


@router.get(
    "/sessions",
    response_model=List[SessionOut],
    summary="List chat sessions this caller owns across ALL agents",
)
async def list_all_chat_sessions(
    auth: AuthDep,
    search: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Cross-agent session list for the floating chat's "All sessions" view.

    Same rows as the per-agent endpoint minus the agent filter; each item
    carries ``agent_slug`` so the UI can badge the source agent and jump to
    it on select. ``search`` is a server-side ILIKE title filter — the whole
    history is searchable, not just the returned page."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    svc = AILibraryChatService()
    user_uuid = _coerce_user_uuid(auth.user_id)
    return await svc.list_sessions(user_id=user_uuid, limit=limit, search=search)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionWithMessages,
    summary="Get a chat session with its message history",
)
async def get_chat_session(session_id: str, auth: AuthDep) -> Dict[str, Any]:
    svc = AILibraryChatService()
    user_uuid = _coerce_user_uuid(auth.user_id)
    session = await svc.get_session(session_id, user_id=user_uuid)
    messages = await svc.get_messages(session_id, user_id=user_uuid)
    return {**session, "messages": messages}


@router.patch(
    "/sessions/{session_id}",
    response_model=SessionOut,
    summary="Rename a chat session",
)
async def update_chat_session(
    session_id: str, payload: SessionUpdate, auth: AuthDep
) -> Dict[str, Any]:
    svc = AILibraryChatService()
    user_uuid = _coerce_user_uuid(auth.user_id)
    return await svc.update_session(session_id, user_id=user_uuid, title=payload.title)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a chat session (status='deleted')",
)
async def delete_chat_session(session_id: str, auth: AuthDep) -> None:
    svc = AILibraryChatService()
    user_uuid = _coerce_user_uuid(auth.user_id)
    await svc.delete_session(session_id, user_id=user_uuid)


@router.post(
    "/sessions/{session_id}/chat",
    response_model=ChatResponse,
    summary="Send a user turn and get the assistant response",
)
async def send_chat_message(
    session_id: str, payload: ChatRequest, auth: AuthDep
) -> Dict[str, Any]:
    """Non-streaming chat endpoint. Persists both the user and the
    assistant message, increments session counters, and returns
    ``{message, usage, run_id}``. The run_id links the generated
    assistant message to the corresponding ``agent_runs`` row so the
    UI can deep-link from chat → Runs tab.
    """
    svc = AILibraryChatService()
    user_uuid = _coerce_user_uuid(auth.user_id)
    result = await svc.chat(
        session_id,
        user_id=user_uuid,
        content=payload.content,
        plan_mode=payload.plan_mode,
        attachments=payload.attachments or None,  # G2
        script_context=(
            payload.script_context.model_dump() if payload.script_context else None
        ),  # §5.3
        answer_to=payload.answer_to,  # phase 2a
    )
    return {
        "message": result["assistant_message"],
        "usage": result["usage"],
        "run_id": result["run_id"],
        "tool_calls": result.get("tool_calls", []),
        "attachment_failures": result.get("attachment_failures", []),  # G2
    }


def _stream_error_payload(exc: BaseException) -> str:
    """SSE error event 的 data。委托 provider_errors.stream_error_data——同一份
    映射同时喂给这里(generator 自身抛出时)与 AILibraryChatService.chat_stream
    的异常分支(chat task 内部抛出时),两条路径不会各写一份判断逻辑。"""
    from app.core.provider_errors import stream_error_data

    return json.dumps(stream_error_data(exc))


@router.post(
    "/sessions/{session_id}/chat-stream",
    summary="Send a user turn and stream the assistant response (SSE)",
)
async def send_chat_message_stream(
    session_id: str, payload: ChatRequest, auth: AuthDep
):
    """Wave I (I2): Server-Sent Events streaming variant.

    Emits incremental text chunks as `event: delta` SSE frames; ends
    with `event: done` carrying usage + run_id. On any error emits
    `event: error` and closes.

    Frontend should treat unknown event types as no-op (forward-compat
    when we add `event: tool_call_delta` later).

    NOTE: Tool-using turns currently degrade to one-shot — the streaming
    path doesn't execute tool_calls mid-stream yet (text-first MVP).
    Frontends should fall back to /chat (non-streaming) when they need
    full tool execution semantics.
    """
    from fastapi.responses import StreamingResponse

    svc = AILibraryChatService()
    user_uuid = _coerce_user_uuid(auth.user_id)

    async def _generator():
        try:
            async for evt in svc.chat_stream(
                session_id,
                user_id=user_uuid,
                content=payload.content,
                plan_mode=payload.plan_mode,
                attachments=payload.attachments or None,  # G2
                script_context=(
                    payload.script_context.model_dump()
                    if payload.script_context
                    else None
                ),  # §5.3
                answer_to=payload.answer_to,  # phase 2a
            ):
                # evt: dict with type + payload
                event_name = evt.get("type", "delta")
                data = json.dumps(evt.get("data") or {}, ensure_ascii=False)
                yield f"event: {event_name}\ndata: {data}\n\n"
        except Exception as exc:
            # SSE errors never surface as HTTP 5xx — without this log the
            # server side is blind (2026-07-13 incident: chat dead for a
            # week with zero rows in application_logs).
            logger.exception(
                f"[ai-library] chat-stream failed session={session_id}: {exc}"
            )
            data = _stream_error_payload(exc)
            yield f"event: error\ndata: {data}\n\n"

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


# ─── O2: Admin telemetry — system-wide rollup ─────────────────────────


@router.get(
    "/admin/telemetry",
    summary="Admin-only system-wide agent telemetry snapshot",
)
async def admin_telemetry(
    auth: AdminAuthDep,
    days: int = 7,
) -> Dict[str, Any]:
    """Return a snapshot of agent_runs telemetry across ALL users for the
    last N days (default 7, max 30).

    Sections:
      - overview: total runs, total tokens, total cost (cents),
        success/fail/cancel rates
      - top_agents: top 10 by run_count + cost
      - top_users:  top 10 by run_count + cost (id only — admin enriches)
      - daily_trend: per-day buckets {date, runs, cost_cents,
        prompt_tokens, completion_tokens}
      - status_breakdown: counts by status
      - failure_modes: top error_codes for status='failed' rows

    Admin-gated via AdminAuthDep — non-admin gets 403.
    """
    if days < 1 or days > 30:
        raise HTTPException(status_code=400, detail="days must be 1..30")

    from datetime import datetime as _dt

    end = _dt.now(timezone.utc)
    start = end - timedelta(days=days)

    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import AgentRuns

    async with read_scope() as session:
        run_rows = (
            (
                await session.execute(
                    select(
                        AgentRuns.agent_id,
                        AgentRuns.user_id,
                        AgentRuns.status,
                        AgentRuns.prompt_tokens,
                        AgentRuns.completion_tokens,
                        AgentRuns.total_tokens,
                        AgentRuns.cost_cents,
                        AgentRuns.started_at,
                        AgentRuns.error_code,
                    )
                    .where(AgentRuns.started_at >= start)
                    .where(AgentRuns.started_at <= end)
                    .order_by(AgentRuns.started_at.desc())
                    .limit(20000)
                )
            )
            .mappings()
            .all()
        )
    # started_at → ISO str: the daily bucketing below slices ``ts[:10]``.
    rows = [_serialize_row(r) for r in run_rows]

    # ---- overview rollup ----
    n = len(rows)
    total_prompt = sum(int(r.get("prompt_tokens") or 0) for r in rows)
    total_completion = sum(int(r.get("completion_tokens") or 0) for r in rows)
    total_cost = sum(float(r.get("cost_cents") or 0.0) for r in rows)
    status_counts: Dict[str, int] = {}
    for r in rows:
        s = r.get("status") or "unknown"
        status_counts[s] = status_counts.get(s, 0) + 1

    # ---- per-agent + per-user buckets ----
    per_agent: Dict[str, Dict[str, Any]] = {}
    per_user: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        aid = r.get("agent_id") or "?"
        uid = r.get("user_id") or "?"
        a = per_agent.setdefault(
            aid,
            {"agent_id": aid, "run_count": 0, "cost_cents": 0.0, "total_tokens": 0},
        )
        a["run_count"] += 1
        a["cost_cents"] += float(r.get("cost_cents") or 0.0)
        a["total_tokens"] += int(r.get("total_tokens") or 0)

        u = per_user.setdefault(
            uid,
            {"user_id": uid, "run_count": 0, "cost_cents": 0.0, "total_tokens": 0},
        )
        u["run_count"] += 1
        u["cost_cents"] += float(r.get("cost_cents") or 0.0)
        u["total_tokens"] += int(r.get("total_tokens") or 0)

    top_agents = sorted(
        per_agent.values(), key=lambda x: x["cost_cents"], reverse=True
    )[:10]
    top_users = sorted(per_user.values(), key=lambda x: x["cost_cents"], reverse=True)[
        :10
    ]

    # ---- daily trend ----
    daily: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        ts = r.get("started_at") or ""
        day = ts[:10] if ts else "unknown"
        d = daily.setdefault(
            day,
            {
                "date": day,
                "runs": 0,
                "cost_cents": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            },
        )
        d["runs"] += 1
        d["cost_cents"] += float(r.get("cost_cents") or 0.0)
        d["prompt_tokens"] += int(r.get("prompt_tokens") or 0)
        d["completion_tokens"] += int(r.get("completion_tokens") or 0)

    daily_trend = sorted(daily.values(), key=lambda x: x["date"])

    # ---- failure modes ----
    failure_codes: Dict[str, int] = {}
    for r in rows:
        if r.get("status") == "failed":
            code = r.get("error_code") or "unknown"
            failure_codes[code] = failure_codes.get(code, 0) + 1
    failure_modes = sorted(
        ({"error_code": k, "count": v} for k, v in failure_codes.items()),
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    return {
        "window_days": days,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "overview": {
            "total_runs": n,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_cost_cents": round(total_cost, 4),
            "unique_agents": len(per_agent),
            "unique_users": len(per_user),
        },
        "status_breakdown": status_counts,
        "top_agents": top_agents,
        "top_users": top_users,
        "daily_trend": daily_trend,
        "failure_modes": failure_modes,
    }


# ─── O4: User commitments listing ─────────────────────────────────────


@router.get(
    "/commitments",
    summary="List the caller's agent commitments (followups)",
)
async def list_my_commitments(
    auth: AuthDep,
    status: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Return commitments where ``user_id`` = authenticated caller.

    Query params:
      - status: optional 'pending' | 'fulfilled' | 'cancelled' | 'failed'
                | 'expired'. Omit for all.
      - limit:  1..200, default 100.
    """
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")

    from app.agent_framework.commitments import CommitmentStatus
    from app.repositories.commitment_repository import get_commitment_repository

    status_filter: Optional[CommitmentStatus] = None
    if status:
        try:
            status_filter = CommitmentStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"invalid status; expected one of {[s.value for s in CommitmentStatus]}",
            )

    repo = get_commitment_repository()
    items = await repo.list_for_user(
        str(auth.user_id), status=status_filter, limit=limit
    )
    return {
        "items": [
            {
                "id": c.id,
                "agent_id": c.agent_id,
                "session_id": c.session_id,
                "description": c.description,
                "trigger_type": c.trigger_type.value if c.trigger_type else None,
                "trigger_at": c.trigger_at.isoformat() if c.trigger_at else None,
                "trigger_event": c.trigger_event,
                "status": c.status.value,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "fulfilled_at": (
                    c.fulfilled_at.isoformat() if c.fulfilled_at else None
                ),
                "expires_at": c.expires_at.isoformat() if c.expires_at else None,
            }
            for c in items
        ],
        "count": len(items),
    }


@router.post(
    "/commitments/{commitment_id}/fulfill",
    summary="Mark a commitment fulfilled (user-initiated)",
)
async def fulfill_commitment(
    commitment_id: int,
    auth: AuthDep,
) -> Dict[str, Any]:
    """User says 'I took care of this' — marks the commitment fulfilled.

    Ownership-checked: only the user who is named on the commitment row
    can fulfill it; any other caller gets 404.
    """
    from app.repositories.commitment_repository import get_commitment_repository

    repo = get_commitment_repository()
    existing = await repo.get_by_id(commitment_id)
    if not existing or existing.user_id != str(auth.user_id):
        raise HTTPException(status_code=404, detail="commitment not found")

    updated = await repo.mark_fulfilled(commitment_id, notes="user-initiated")
    if not updated:
        raise HTTPException(
            status_code=409, detail="commitment already in terminal state"
        )
    return {"id": updated.id, "status": updated.status.value}


@router.post(
    "/commitments/{commitment_id}/cancel",
    summary="Cancel a commitment (user-initiated)",
)
async def cancel_commitment(
    commitment_id: int,
    auth: AuthDep,
) -> Dict[str, Any]:
    """User dismisses a pending followup."""
    from app.repositories.commitment_repository import get_commitment_repository

    repo = get_commitment_repository()
    existing = await repo.get_by_id(commitment_id)
    if not existing or existing.user_id != str(auth.user_id):
        raise HTTPException(status_code=404, detail="commitment not found")

    updated = await repo.mark_cancelled(commitment_id, notes="user dismissed")
    if not updated:
        raise HTTPException(
            status_code=409, detail="commitment already in terminal state"
        )
    return {"id": updated.id, "status": updated.status.value}


# ─── G1+G5: User MCP server CRUD ──────────────────────────────────────


class _MCPServerCreate(BaseModel):
    name: str = Field(..., pattern=r"^[A-Za-z0-9_]+$")
    url: str = Field(..., pattern=r"^https?://")
    bearer_token: Optional[str] = None
    description: Optional[str] = None
    enabled: bool = True


class _MCPServerUpdate(BaseModel):
    url: Optional[str] = Field(default=None, pattern=r"^https?://")
    bearer_token: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None


def _mcp_row_to_dict(row, *, include_token: bool = False):
    out = {
        "id": str(row.id),
        "name": row.name,
        "url": row.url,
        "description": row.description,
        "enabled": row.enabled,
    }
    if include_token:
        out["bearer_token"] = row.bearer_token
    else:
        # Mask presence without leaking value
        out["has_bearer_token"] = bool(row.bearer_token)
    return out


@router.get("/mcp-servers", summary="List the caller's MCP server registrations")
async def list_mcp_servers(auth: AuthDep) -> Dict[str, Any]:
    from app.repositories.user_mcp_servers_repository import (
        get_user_mcp_servers_repository,
    )

    repo = get_user_mcp_servers_repository()
    rows = await repo.list_for_user(_coerce_user_uuid(auth.user_id), only_enabled=False)
    return {"items": [_mcp_row_to_dict(r) for r in rows], "count": len(rows)}


@router.post(
    "/mcp-servers",
    status_code=status.HTTP_201_CREATED,
    summary="Register an MCP server for the caller",
)
async def create_mcp_server(payload: _MCPServerCreate, auth: AuthDep) -> Dict[str, Any]:
    from app.repositories.user_mcp_servers_repository import (
        get_user_mcp_servers_repository,
    )

    repo = get_user_mcp_servers_repository()
    try:
        row = await repo.create(
            user_id=_coerce_user_uuid(auth.user_id),
            name=payload.name,
            url=payload.url,
            bearer_token=payload.bearer_token,
            description=payload.description,
            enabled=payload.enabled,
        )
    except Exception as exc:
        # Most likely UNIQUE (user_id, name) conflict
        raise HTTPException(status_code=409, detail=f"create failed: {exc}")
    if not row:
        raise HTTPException(status_code=500, detail="create returned no row")
    return _mcp_row_to_dict(row)


@router.patch("/mcp-servers/{server_id}", summary="Update an MCP server registration")
async def update_mcp_server(
    server_id: UUID,
    payload: _MCPServerUpdate,
    auth: AuthDep,
) -> Dict[str, Any]:
    from app.repositories.user_mcp_servers_repository import (
        get_user_mcp_servers_repository,
    )

    user_uuid = _coerce_user_uuid(auth.user_id)
    repo = get_user_mcp_servers_repository()
    existing = await repo.get_by_id(server_id)
    if not existing or existing.user_id != user_uuid:
        raise HTTPException(status_code=404, detail="MCP server not found")
    ok = await repo.update(
        server_id,
        owner_user_id=user_uuid,
        url=payload.url,
        bearer_token=payload.bearer_token,
        description=payload.description,
        enabled=payload.enabled,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="no fields to update")
    fresh = await repo.get_by_id(server_id)
    return _mcp_row_to_dict(fresh) if fresh else {}


@router.delete(
    "/mcp-servers/{server_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an MCP server registration",
)
async def delete_mcp_server(server_id: UUID, auth: AuthDep) -> None:
    from app.repositories.user_mcp_servers_repository import (
        get_user_mcp_servers_repository,
    )

    user_uuid = _coerce_user_uuid(auth.user_id)
    repo = get_user_mcp_servers_repository()
    existing = await repo.get_by_id(server_id)
    if not existing or existing.user_id != user_uuid:
        raise HTTPException(status_code=404, detail="MCP server not found")
    await repo.delete(server_id, owner_user_id=user_uuid)


# ─── B: Chat attachment upload (temp storage for one-off chat use) ────


# Per-attachment hard cap. Bounds how much we buffer in memory while
# validating an upload before it is persisted as a temp resource on the
# shared library (via save_chat_temp_upload). Buffering a validated upload
# costs ~50MB transient memory per request (acceptable: capped + single
# worker); the old code streamed to /tmp but /tmp is not shared with the
# worker container, which is exactly what this change fixes.
_CHAT_ATTACHMENT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB

_ALLOWED_EXTS = {
    # image
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    # video
    ".mp4",
    ".mov",
    ".webm",
    ".mkv",
    ".avi",
    # pdf
    ".pdf",
}


# C2: magic-byte signatures keyed by extension. Defends against
# extension-spoofing (uploading malicious binary as .jpg). Schema:
#   ext → list[Signature]   where Signature = list[(offset, bytes)]
# Outer list is OR (any signature matches → accept). Inner list is
# AND (every (offset, bytes) pair within a signature must match).
# This lets us express e.g. WEBP = "RIFF at 0 AND WEBP at 8" without
# accidentally accepting AVI files (which also have RIFF at 0).
_MAGIC_BYTES: dict[str, list[list[tuple[int, bytes]]]] = {
    ".jpg": [[(0, b"\xff\xd8\xff")]],
    ".jpeg": [[(0, b"\xff\xd8\xff")]],
    ".png": [[(0, b"\x89PNG\r\n\x1a\n")]],
    ".gif": [[(0, b"GIF87a")], [(0, b"GIF89a")]],
    ".webp": [[(0, b"RIFF"), (8, b"WEBP")]],
    ".bmp": [[(0, b"BM")]],
    ".mp4": [[(4, b"ftyp")]],
    ".mov": [[(4, b"ftyp")]],
    ".webm": [[(0, b"\x1a\x45\xdf\xa3")]],  # EBML / Matroska
    ".mkv": [[(0, b"\x1a\x45\xdf\xa3")]],
    ".avi": [[(0, b"RIFF"), (8, b"AVI ")]],
    ".pdf": [[(0, b"%PDF-")]],
}


def _check_magic_bytes(ext: str, head: bytes) -> bool:
    """Return True iff the first bytes match one of the signatures
    registered for this extension. Unknown extensions accept (caller
    has already filtered via _ALLOWED_EXTS)."""
    signatures = _MAGIC_BYTES.get(ext)
    if not signatures:
        return True
    for sig in signatures:
        # Inner AND: every (offset, expected) within this signature
        # must match for the signature to count.
        if all(
            len(head) >= offset + len(expected)
            and head[offset : offset + len(expected)] == expected
            for offset, expected in sig
        ):
            return True
    return False


@router.post(
    "/chat-attachments/upload",
    summary="Upload a one-off chat attachment (image/video/pdf) as a temp resource",
)
async def upload_chat_attachment(
    auth: AuthDep, request: Request, _scope: ScopedRequestDep = None
) -> Dict[str, Any]:
    """Multipart upload for chat/issue attachments.

    The validated bytes are persisted as a temp resource on the shared
    library (``${DOWNLOAD_PATH}/.../temp/``) via ``save_chat_temp_upload``
    instead of gateway-local ``/tmp``. Both the gateway (regular chat
    turns) and the worker (issue turns) mount that volume, so the chat
    attachment resolver can read the file from either container.

    Returns: ``{ kind, resource_id, file_path, url, size_bytes, mime, filename }``
      - ``kind``: image | video | pdf (inferred from content-type / extension)
      - ``resource_id``: the persisted temp resource id (promotable later)
      - ``file_path``: path relative to ``DOWNLOAD_PATH`` (worker-readable)
      - ``url``: back-compat alias of ``file_path``

    Scope: an optional ``session_id`` (form field or query param) routes
    the temp resource into the session's team scope; otherwise it lands
    in the caller's personal scope.
    """
    user_id = _coerce_user_uuid(auth.user_id)

    # FastAPI doesn't auto-bind UploadFile when the route param is just
    # `request: Request`. Use the lower-level form() API.
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "filename"):
        raise HTTPException(status_code=400, detail="missing 'file' field")

    filename = upload.filename or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported file type {ext!r}; allowed: {sorted(_ALLOWED_EXTS)}",
        )

    # Reject oversize before reading the body fully (defensive — also
    # enforced again below in case content-length was lying).
    cl = request.headers.get("content-length")
    if cl and int(cl) > _CHAT_ATTACHMENT_MAX_BYTES * 1.1:
        raise HTTPException(
            status_code=413,
            detail=f"file too large; max {_CHAT_ATTACHMENT_MAX_BYTES} bytes",
        )

    # Buffer the validated bytes in memory (capped at 50MB). The first
    # chunk is checked against magic bytes (C2) — extension alone is not a
    # trust boundary. We buffer (rather than stream to /tmp) so the bytes
    # can be handed to save_chat_temp_upload for persistence on the shared
    # library, which both the gateway and worker containers can read.
    buf = bytearray()
    try:
        magic_checked = False
        while True:
            chunk = await upload.read(64 * 1024)
            if not chunk:
                break
            if not magic_checked:
                if not _check_magic_bytes(ext, chunk[:32]):
                    raise HTTPException(
                        status_code=415,
                        detail=f"file content does not match {ext} format",
                    )
                magic_checked = True
            buf.extend(chunk)
            if len(buf) > _CHAT_ATTACHMENT_MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"file exceeds {_CHAT_ATTACHMENT_MAX_BYTES} bytes",
                )
    finally:
        # Best-effort release of the multipart field's underlying buffer.
        # The bytes are already buffered above, so a close error here is
        # irrelevant to the upload's success.
        try:
            await upload.close()
        except Exception:
            pass

    # An empty body never reaches the magic-byte gate (the read loop exits
    # immediately), so guard it explicitly — an empty file is not a valid
    # image/video/pdf attachment.
    if not buf:
        raise HTTPException(status_code=400, detail="uploaded file is empty")

    mime_guess = upload.content_type if hasattr(upload, "content_type") else None

    # Scope: prefer a session_id from the multipart form, fall back to a
    # query param. A non-string form value (e.g. a stray file field) is
    # ignored. None → personal scope (resolved in save_chat_temp_upload).
    session_raw = form.get("session_id")
    session_id = session_raw if isinstance(session_raw, str) else None
    if session_id is None:
        session_id = request.query_params.get("session_id")

    from app.services.library.chat_upload import save_chat_temp_upload

    result = await save_chat_temp_upload(
        user_id=str(user_id),
        session_id=session_id,
        file_bytes=bytes(buf),
        filename=filename,
        mime=mime_guess or "",
    )

    return {
        "kind": result["kind"],
        "resource_id": result["resource_id"],
        "file_path": result["file_path"],  # relative to DOWNLOAD_PATH (worker-readable)
        "url": result["file_path"],  # back-compat alias
        "size_bytes": result["size_bytes"],
        "mime": mime_guess,
        "filename": filename,
    }


# ─── G1: Approval requests (human-in-loop gates) ──────────────────────


@router.get("/approval-requests", summary="List the caller's pending approval requests")
async def list_approval_requests(auth: AuthDep, limit: int = 50) -> Dict[str, Any]:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    from app.repositories.approval_requests_repository import (
        get_approval_requests_repository,
    )

    repo = get_approval_requests_repository()
    rows = await repo.list_pending_for_user(
        _coerce_user_uuid(auth.user_id), limit=limit
    )
    return {
        "items": [
            {
                "id": str(r.id),
                "agent_id": str(r.agent_id),
                "session_id": str(r.session_id) if r.session_id else None,
                "run_id": str(r.run_id) if r.run_id else None,
                "hook_name": r.hook_name,
                "reason": r.reason,
                "payload": r.payload,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }


class _ApprovalDecision(BaseModel):
    note: Optional[str] = None


@router.post(
    "/approval-requests/{request_id}/approve", summary="Approve a pending request"
)
async def approve_approval_request(
    request_id: UUID,
    payload: _ApprovalDecision,
    auth: AuthDep,
) -> Dict[str, Any]:
    from app.repositories.approval_requests_repository import (
        get_approval_requests_repository,
    )

    user_uuid = _coerce_user_uuid(auth.user_id)
    repo = get_approval_requests_repository()
    existing = await repo.get_by_id(request_id)
    if not existing or existing.user_id != user_uuid:
        raise HTTPException(status_code=404, detail="approval request not found")
    if existing.status != "pending":
        raise HTTPException(status_code=409, detail=f"already {existing.status}")
    ok = await repo.decide(
        request_id,
        owner_user_id=user_uuid,
        approve=True,
        note=payload.note,
    )
    if not ok:
        raise HTTPException(status_code=409, detail="decide failed")
    _signal_dbos_workflow_if_any(existing, approved=True, note=payload.note)
    return {"id": str(request_id), "status": "approved"}


@router.post(
    "/approval-requests/{request_id}/reject", summary="Reject a pending request"
)
async def reject_approval_request(
    request_id: UUID,
    payload: _ApprovalDecision,
    auth: AuthDep,
) -> Dict[str, Any]:
    from app.repositories.approval_requests_repository import (
        get_approval_requests_repository,
    )

    user_uuid = _coerce_user_uuid(auth.user_id)
    repo = get_approval_requests_repository()
    existing = await repo.get_by_id(request_id)
    if not existing or existing.user_id != user_uuid:
        raise HTTPException(status_code=404, detail="approval request not found")
    if existing.status != "pending":
        raise HTTPException(status_code=409, detail=f"already {existing.status}")
    ok = await repo.decide(
        request_id,
        owner_user_id=user_uuid,
        approve=False,
        note=payload.note,
    )
    if not ok:
        raise HTTPException(status_code=409, detail="decide failed")
    _signal_dbos_workflow_if_any(existing, approved=False, note=payload.note)
    return {"id": str(request_id), "status": "rejected"}


def _signal_dbos_workflow_if_any(
    existing,
    *,
    approved: bool,
    note: Optional[str],
) -> None:
    """G2: when an approval row was created from inside a DBOS
    workflow (payload includes ``workflow_id``), wake the paused
    workflow via DBOS.send. No-op for chat-style approvals where
    no workflow is waiting — those rely on G1's next-turn-replay."""
    workflow_id = (existing.payload or {}).get("workflow_id")
    if not workflow_id:
        return
    try:
        from app.agent_framework.approval_gate import signal_approval_decision

        signal_approval_decision(
            workflow_id=str(workflow_id),
            approval_id=str(existing.id),
            approved=approved,
            note=note,
        )
    except Exception as exc:
        logger.warning(
            f"[approval] DBOS signal failed for workflow={workflow_id} "
            f"(non-fatal — chat path unaffected): {exc}"
        )


# ─── G3: Lane queue snapshot for ops ──────────────────────────────────


@router.get("/admin/lanes/snapshot", summary="Per-lane queue depth snapshot")
async def admin_lane_snapshot(auth: AdminAuthDep) -> Dict[str, Any]:
    """Returns current depth of each LaneQueue partition. Lets ops see
    which lane is backed up (User vs Background vs Scheduled vs Subagent).
    """
    from app.main import app as _app

    lq = getattr(_app.state, "lane_queue", None)
    if lq is None:
        return {"available": False, "reason": "lane_queue not wired on this process"}

    queues = getattr(lq, "_queues", {})
    snapshot: Dict[str, Any] = {}
    for lane, q in queues.items():
        try:
            lane_name = lane.value if hasattr(lane, "value") else str(lane)
            snapshot[lane_name] = {
                "depth": q.qsize() if hasattr(q, "qsize") else 0,
            }
        except Exception:
            continue
    return {"available": True, "lanes": snapshot}


# ─── Version history (Phase 3) ────────────────────────────────────────


def _serialize_versions(rows, *, kind: str) -> List[Dict[str, Any]]:
    """Trim version rows for list view — full body only on detail fetch."""
    out = []
    for r in rows:
        item = {
            "id": str(r["id"]),
            "version_number": r.get("version_number"),
            "notes": r.get("notes"),
            "created_by": str(r["created_by"]) if r.get("created_by") else None,
            "created_at": r.get("created_at"),
        }
        if kind == "agent":
            item["model"] = r.get("model")
            item["temperature"] = r.get("temperature")
            item["max_tokens"] = r.get("max_tokens")
        elif kind == "skill_file":
            item["path"] = r.get("path")
            item["file_type"] = r.get("file_type")
        out.append(item)
    return out


@router.get(
    "/agents/{slug}/versions",
    summary="List version history of an agent",
)
async def list_agent_versions(
    slug: str, auth: AuthDep, limit: int = 50
) -> Dict[str, Any]:
    """Versions ordered newest first. Includes model/temp/max_tokens in
    list view so the user can spot config changes; full markdown bodies
    only via the detail endpoint."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")

    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import AiAgentVersions

    async with read_scope() as session:
        rows = (
            (
                await session.execute(
                    select(
                        AiAgentVersions.id,
                        AiAgentVersions.version_number,
                        AiAgentVersions.model,
                        AiAgentVersions.temperature,
                        AiAgentVersions.max_tokens,
                        AiAgentVersions.notes,
                        AiAgentVersions.created_by,
                        AiAgentVersions.created_at,
                    )
                    .where(AiAgentVersions.agent_id == str(agent["id"]))
                    .order_by(AiAgentVersions.version_number.desc())
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
    return {
        "items": _serialize_versions(rows, kind="agent"),
        "current_version": agent.get("current_version"),
    }


@router.get(
    "/agents/{slug}/versions/{version_number}",
    summary="Get a specific agent version (full body)",
)
async def get_agent_version(
    slug: str,
    version_number: int,
    auth: AuthDep,
) -> Dict[str, Any]:
    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import AiAgentVersions

    async with read_scope() as session:
        row = (
            (
                await session.execute(
                    select(*AiAgentVersions.__table__.columns)
                    .where(AiAgentVersions.agent_id == str(agent["id"]))
                    .where(AiAgentVersions.version_number == version_number)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
    if not row:
        raise HTTPException(status_code=404, detail="version not found")
    return _serialize_row(row)


@router.post(
    "/agents/{slug}/rollback/{version_number}",
    summary="Rollback agent to a previous version (creates a new version with the old content)",
)
async def rollback_agent(
    slug: str,
    version_number: int,
    auth: AuthDep,
) -> Dict[str, Any]:
    """Rollback writes a NEW version with the old body content rather than
    moving the current_version pointer back. This preserves the audit
    trail (you can see "v7 was a rollback of v3" in the version list)
    and never loses intermediate versions."""
    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    if _is_system_skill(agent):
        raise HTTPException(
            status_code=403,
            detail="cannot rollback a system preset agent",
        )

    user_uuid = _coerce_user_uuid(auth.user_id)
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import AiAgentVersions

    # Fetch the snapshot
    async with read_scope() as session:
        snap = (
            (
                await session.execute(
                    select(
                        AiAgentVersions.identity_md,
                        AiAgentVersions.soul_md,
                        AiAgentVersions.agent_md,
                        AiAgentVersions.model,
                        AiAgentVersions.temperature,
                        AiAgentVersions.max_tokens,
                    )
                    .where(AiAgentVersions.agent_id == str(agent["id"]))
                    .where(AiAgentVersions.version_number == version_number)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
    if not snap:
        raise HTTPException(status_code=404, detail="version not found")

    # Use the existing versioned update — it snapshots current then writes new
    notes = f"rollback of v{version_number}"
    updated = (
        await agent_repo.update_fields_versioned(
            agent_id=int(agent["id"]) if isinstance(agent["id"], int) else None,
            agent_uuid=agent["id"],
            updates={
                "identity_md": snap.get("identity_md"),
                "soul_md": snap.get("soul_md"),
                "agent_md": snap.get("agent_md"),
                "model": snap.get("model"),
                "temperature": snap.get("temperature"),
                "max_tokens": snap.get("max_tokens"),
            },
            editor_user_id=user_uuid,
            notes=notes,
        )
        if hasattr(agent_repo, "update_fields_versioned")
        else None
    )

    # Fall back to direct table update if signature mismatch (defensive)
    if updated is None:
        return {
            "warning": "rollback signature mismatch — repo refactor needed",
            "snap_loaded": True,
        }

    return {
        "rolled_back_to": version_number,
        "new_version": (
            updated.get("current_version") if isinstance(updated, dict) else None
        ),
        "notes": notes,
    }


@router.get(
    "/skills/{slug}/versions",
    summary="List version history of a skill",
)
async def list_skill_versions(
    slug: str, auth: AuthDep, limit: int = 50
) -> Dict[str, Any]:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    _, skill_repo = _repos()
    skill = await skill_repo.get_by_slug(slug)
    if not skill:
        raise HTTPException(status_code=404, detail="skill not found")
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import SkillVersions

    async with read_scope() as session:
        rows = (
            (
                await session.execute(
                    select(
                        SkillVersions.id,
                        SkillVersions.version_number,
                        SkillVersions.notes,
                        SkillVersions.created_by,
                        SkillVersions.created_at,
                    )
                    .where(SkillVersions.skill_id == int(skill["id"]))
                    .order_by(SkillVersions.version_number.desc())
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
    return {
        "items": _serialize_versions(rows, kind="skill"),
        "current_version": skill.get("current_version"),
    }


@router.get(
    "/skills/{slug}/versions/{version_number}",
    summary="Get a specific skill version (full body)",
)
async def get_skill_version(
    slug: str,
    version_number: int,
    auth: AuthDep,
) -> Dict[str, Any]:
    """Full snapshot of one skill version (body_md / frontmatter_json), so the
    version-history UI can preview what a rollback would restore. Mirrors
    get_agent_version."""
    _, skill_repo = _repos()
    skill = await skill_repo.get_by_slug(slug)
    if not skill:
        raise HTTPException(status_code=404, detail="skill not found")
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import SkillVersions

    async with read_scope() as session:
        row = (
            (
                await session.execute(
                    select(*SkillVersions.__table__.columns)
                    .where(SkillVersions.skill_id == int(skill["id"]))
                    .where(SkillVersions.version_number == version_number)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
    if not row:
        raise HTTPException(status_code=404, detail="version not found")
    return _serialize_row(row)


@router.get(
    "/skills/{slug}/files/{path:path}/versions",
    summary="List version history of a skill file",
)
async def list_skill_file_versions(
    slug: str,
    path: str,
    auth: AuthDep,
    limit: int = 50,
) -> Dict[str, Any]:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    _, skill_repo = _repos()
    skill = await skill_repo.get_by_slug(slug)
    if not skill:
        raise HTTPException(status_code=404, detail="skill not found")
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import SkillFiles, SkillFileVersions

    # Find the file row first
    async with read_scope() as session:
        file_row = (
            (
                await session.execute(
                    select(SkillFiles.id, SkillFiles.current_version)
                    .where(SkillFiles.skill_id == int(skill["id"]))
                    .where(SkillFiles.path == path)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if not file_row:
            raise HTTPException(status_code=404, detail="skill file not found")
        file_id = file_row["id"]
        cur_v = file_row.get("current_version")

        rows = (
            (
                await session.execute(
                    select(
                        SkillFileVersions.id,
                        SkillFileVersions.version_number,
                        SkillFileVersions.path,
                        SkillFileVersions.file_type,
                        SkillFileVersions.notes,
                        SkillFileVersions.created_by,
                        SkillFileVersions.created_at,
                    )
                    .where(SkillFileVersions.skill_file_id == str(file_id))
                    .order_by(SkillFileVersions.version_number.desc())
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
    return {
        "items": _serialize_versions(rows, kind="skill_file"),
        "current_version": cur_v,
    }


@router.post(
    "/skills/{slug}/rollback/{version_number}",
    summary="Rollback skill to a previous version (creates a new version with the old content)",
)
async def rollback_skill(
    slug: str,
    version_number: int,
    auth: AuthDep,
) -> Dict[str, Any]:
    """Rollback writes a NEW version with the old body content rather than
    moving the current_version pointer back — same audit-preserving pattern
    as rollback_agent. Snapshots the live row, then writes the old
    body_md / frontmatter_json (+ legacy content_md mirror) as a new version."""
    _, skill_repo = _repos()
    skill = await skill_repo.get_by_slug(slug)
    if not skill:
        raise HTTPException(status_code=404, detail="skill not found")
    if _is_system_skill(skill):
        raise HTTPException(
            status_code=403,
            detail="cannot rollback a system preset skill",
        )

    skill_id = int(skill["id"])
    user_uuid = _coerce_user_uuid(auth.user_id)
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import SkillVersions

    async with read_scope() as session:
        snap = (
            (
                await session.execute(
                    select(SkillVersions.body_md, SkillVersions.frontmatter_json)
                    .where(SkillVersions.skill_id == skill_id)
                    .where(SkillVersions.version_number == version_number)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
    if not snap:
        raise HTTPException(status_code=404, detail="version not found")

    notes = f"rollback of v{version_number}"
    updates: Dict[str, Any] = {
        "body_md": snap.get("body_md"),
        "frontmatter_json": snap.get("frontmatter_json"),
        # Keep legacy content_md in sync with body_md (see update_skill / mig 152).
        "content_md": snap.get("body_md"),
    }
    await skill_repo.update_fields_versioned(
        skill_id, updates, created_by=user_uuid, notes=notes
    )
    refreshed = await skill_repo.get_by_slug(slug)
    return {
        "rolled_back_to": version_number,
        "new_version": (refreshed.get("current_version") if refreshed else None),
        "notes": notes,
    }


# ─── Phase 3: Token billing usage summary ─────────────────────────────


@router.get(
    "/usage/summary",
    summary="Per-user token usage rollup (model + day breakdown)",
)
async def get_usage_summary(
    auth: AuthDep,
    days: int = 30,
) -> Dict[str, Any]:
    """30-day default window; cap 90. Returns by_model (top costs) +
    by_day (sparkline) + overall totals. Driven by ai_usage_logs."""
    if days < 1 or days > 90:
        raise HTTPException(status_code=400, detail="days must be 1..90")

    from app.services.ai.billing.token_billing import summarize_user_usage

    user_uuid = _coerce_user_uuid(auth.user_id)
    summary = await summarize_user_usage(user_uuid, days=days)

    return {
        "window_start": summary.window_start.isoformat(),
        "window_end": summary.window_end.isoformat(),
        "overall": {
            "total_tokens": summary.overall_total_tokens,
            "cost_points": summary.overall_cost_points,
            "run_count": summary.overall_run_count,
        },
        "by_model": [
            {
                "model": r.model,
                "total_tokens": r.total_tokens,
                "cost_points": r.cost_points,
                "run_count": r.run_count,
            }
            for r in summary.by_model
        ],
        "by_day": [
            {
                "date": d.date,
                "total_tokens": d.total_tokens,
                "cost_points": d.cost_points,
                "run_count": d.run_count,
            }
            for d in summary.by_day
        ],
    }
