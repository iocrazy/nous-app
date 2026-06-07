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

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger
from pydantic import BaseModel, Field

from app.core.admin_deps import AdminAuthDep
from app.core.deps import AuthDep
from app.core.scope_dep import ScopedRequestDep
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.agent_repository import AgentRepository, get_agent_repository
from app.repositories.agent_runs_repository import (
    get_agent_runs_repository,
)
from app.repositories.agent_workforce_repository import (
    TASK_KIND_AGENT,
    tt_row_to_task_shape,
)
from app.repositories.skill_repository import (
    SkillRepository,
    get_skill_repository,
)
from app.schemas.agent_runs import (
    RunDetail,
    RunListItem,
    RunListResponse,
    UsageAggregate,
)
from app.schemas.ai_library import (
    AgentCreate,
    AgentOut,
    AgentUpdate,
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
from app.services.ai.runner.seed_loader import SeedLoader

router = APIRouter(prefix="/ai-library", tags=["AI Library"])


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


# ---------------------------------------------------------------------------
# Scope membership helpers (Phase 2 PR 2.9)
# ---------------------------------------------------------------------------


async def _user_is_team_member(user_id: UUID, team_id: int) -> bool:
    """Return True iff the user has a ``team_members`` row for this team."""
    client = await get_async_supabase_admin()
    result = (
        await client.table("team_members")
        .select("team_id")
        .eq("team_id", team_id)
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    return bool(result.data)


async def _user_can_write_project(user_id: UUID, project_id: int) -> bool:
    """True iff user is owner of ``project_id`` OR a ``project_members`` row.

    Any project membership qualifies (including viewer) — we only need
    presence to let the user attach an agent to the project's scope. Finer
    role-based restrictions can layer on later if needed.
    """
    client = await get_async_supabase_admin()
    # Owner check
    proj = (
        await client.table("projects")
        .select("owner_id")
        .eq("id", project_id)
        .maybe_single()
        .execute()
    )
    if proj and proj.data and str(proj.data.get("owner_id")) == str(user_id):
        return True
    # Explicit membership
    member = (
        await client.table("project_members")
        .select("project_id")
        .eq("project_id", project_id)
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    return bool(member.data)


async def _fetch_user_team_ids(user_id: UUID) -> List[int]:
    """Return BIGINT team ids the user is a member of (empty on miss)."""
    client = await get_async_supabase_admin()
    result = (
        await client.table("team_members")
        .select("team_id")
        .eq("user_id", str(user_id))
        .execute()
    )
    return [
        int(r["team_id"]) for r in (result.data or []) if r.get("team_id") is not None
    ]


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
    client = await get_async_supabase_admin()
    owned = (
        await client.table("projects")
        .select("id")
        .eq("owner_id", str(user_id))
        .execute()
    )
    member = (
        await client.table("project_members")
        .select("project_id")
        .eq("user_id", str(user_id))
        .execute()
    )
    ids: set[int] = set()
    for row in owned.data or []:
        if row.get("id") is not None:
            ids.add(int(row["id"]))
    for row in member.data or []:
        if row.get("project_id") is not None:
            ids.add(int(row["project_id"]))
    return sorted(ids)


async def _fetch_team_names(team_ids: List[int]) -> Dict[int, str]:
    """Return {team_id: name} for the given BIGINT ids ([] → {})."""
    if not team_ids:
        return {}
    client = await get_async_supabase_admin()
    result = (
        await client.table("teams").select("id, name").in_("id", team_ids).execute()
    )
    return {int(r["id"]): r["name"] for r in (result.data or [])}


async def _fetch_project_names(project_ids: List[int]) -> Dict[int, str]:
    """Return {project_id: name} for the given BIGINT ids ([] → {})."""
    if not project_ids:
        return {}
    client = await get_async_supabase_admin()
    result = (
        await client.table("projects")
        .select("id, name")
        .in_("id", project_ids)
        .execute()
    )
    return {int(r["id"]): r["name"] for r in (result.data or [])}


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

    enriched_with_skills: List[Dict[str, Any]] = []
    for row in rows:
        try:
            agent_uuid = UUID(str(row["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(f"[ai-library] skipping agent with bad id: {exc}")
            continue
        skill_ids = await agent_repo.get_skill_ids(agent_uuid)
        enriched_with_skills.append({**row, "skill_ids": skill_ids})
    return await _enrich_agents_with_scope_names(enriched_with_skills)


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
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        )
    agent_uuid = UUID(str(agent["id"]))
    agent = {**agent, "skill_ids": await agent_repo.get_skill_ids(agent_uuid)}
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
    ):
        val = getattr(payload, key)
        if val is not None:
            fields[key] = val

    created = await agent_repo.insert(fields)
    enriched = await _enrich_agents_with_scope_names([{**created, "skill_ids": []}])
    return enriched[0]


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

    Phase 1 policy: system-preset agents are read-only (403).
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

    agent_uuid = UUID(str(agent["id"]))
    # skill_ids is handled separately; strip from the field-level update.
    updates = payload.model_dump(exclude_none=True, exclude={"skill_ids"})
    # Budget "unlimited" convention: 0 from the client means "clear the cap"
    # — rewrite to explicit None so the DB stores NULL and the sweeper's
    # ``is not None`` check keeps treating it as uncapped. The frontend
    # can't reach "set to NULL" through the PATCH body because
    # exclude_none=True drops nulls; this 0→None bridge keeps the wire
    # format simple without regressing the rest of the endpoint.
    for budget_field in ("monthly_token_budget", "monthly_cost_cents_budget"):
        if updates.get(budget_field) == 0:
            updates[budget_field] = None
    if updates:
        user_uuid = _coerce_user_uuid(auth.user_id)
        await agent_repo.update_fields_versioned(
            agent_uuid, updates, created_by=user_uuid
        )
    if payload.skill_ids is not None:
        await agent_repo.update_skill_bindings(agent_uuid, payload.skill_ids)

    refreshed = await agent_repo.get_by_slug(slug)
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="agent disappeared after update",
        )
    row = {**refreshed, "skill_ids": await agent_repo.get_skill_ids(agent_uuid)}
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

    enriched: List[Dict[str, Any]] = []
    for s in skills:
        try:
            skill_id = int(s["id"])
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(f"[ai-library] skipping skill with bad id: {exc}")
            continue
        files = await skill_repo.list_files(skill_id)
        enriched.append({**s, "files": files})
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
    # Dual-write body_md → content_md so legacy readers (storyboard_ai_service,
    # /api/v1/skills endpoints) can still see the body. See migration 152.
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
    # Keep legacy content_md in sync with body_md edits so storyboard_ai_service
    # and /api/v1/skills readers see the latest text. See migration 152.
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
    client = await get_async_supabase_admin()
    result = (
        await client.table("user_profiles")
        .select("role")
        .eq("id", str(user_id))
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        return False
    return result.data.get("role") == "admin"


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
    client = await get_async_supabase_admin()

    now = datetime.now(timezone.utc)
    # 14-day window inclusive of today: midnight of (today - 13 days)
    # through now. Bucketing keys are date-only ISO strings, so we want
    # day 0 = 13 days ago and day 13 = today.
    window_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=13
    )
    iso_start = window_start.isoformat()

    # Pull 14d of runs in one shot. Bounded — even busy agents rarely
    # break a few hundred runs/2wk; bucketing in Python beats issuing
    # 14 + 14 + N PostgREST calls.
    runs_q = await (
        client.table("agent_runs")
        .select(
            "id,status,trigger,model,started_at,ended_at,"
            "prompt_tokens,completion_tokens,cost_cents"
        )
        .eq("agent_id", str(agent_uuid))
        .eq("user_id", str(user_uuid))
        .gte("started_at", iso_start)
        .order("started_at", desc=True)
        .execute()
    )
    runs_14d: List[Dict[str, Any]] = runs_q.data or []

    # Most recent run, regardless of window. The dashboard shows a
    # banner even when the user hasn't run anything in 2 weeks.
    latest_q = await (
        client.table("agent_runs")
        .select(
            "id,status,trigger,model,started_at,ended_at,"
            "prompt_tokens,completion_tokens,cost_cents,"
            "input_summary,output_summary,error_code,error_message"
        )
        .eq("agent_id", str(agent_uuid))
        .eq("user_id", str(user_uuid))
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    latest_run = latest_q.data[0] if latest_q.data else None

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

    # Tasks: status counts over 14d. A4: tasks live in task_tracking
    # WHERE task_kind='agent_task' scoped by user_id (Delegate from chat
    # carries the caller's user_id, and direct dispatches get their owner
    # stamped). Match the same window so the dashboard tells one
    # consistent story.
    tasks_q = await (
        client.table("task_tracking")
        .select("dbos_workflow_id,phase,created_at,title,metadata")
        .eq("task_kind", TASK_KIND_AGENT)
        .eq("agent_id", str(agent_uuid))
        .eq("user_id", str(user_uuid))
        .gte("created_at", iso_start)
        .order("created_at", desc=True)
        .execute()
    )
    tasks_14d_raw = tasks_q.data or []
    tasks_14d: List[Dict[str, Any]] = [tt_row_to_task_shape(r) for r in tasks_14d_raw]
    status_counts: Counter[str] = Counter(
        (t.get("lifecycle_status") or "unknown") for t in tasks_14d
    )

    # Recent agent tasks (5) — pulled separately in case the 14d
    # window is empty but older tasks still matter for context.
    recent_tasks_q = await (
        client.table("task_tracking")
        .select(
            "dbos_workflow_id,phase,created_at,started_at,completed_at,title,"
            "error_code,error_msg,metadata"
        )
        .eq("task_kind", TASK_KIND_AGENT)
        .eq("agent_id", str(agent_uuid))
        .eq("user_id", str(user_uuid))
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )

    # Recent runs table (10 slim rows, all-time so an idle agent still
    # shows history).
    recent_runs_q = await (
        client.table("agent_runs")
        .select(
            "id,status,trigger,model,started_at,ended_at,"
            "prompt_tokens,completion_tokens,cost_cents"
        )
        .eq("agent_id", str(agent_uuid))
        .eq("user_id", str(user_uuid))
        .order("started_at", desc=True)
        .limit(10)
        .execute()
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
        "recent_tasks": [tt_row_to_task_shape(r) for r in (recent_tasks_q.data or [])],
        "recent_runs": recent_runs_q.data or [],
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
) -> Dict[str, Any]:
    """Runs for this agent, scoped to the authenticated user. Newest first."""
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
    page = await runs_repo.list_by_agent(
        agent_id=agent_uuid, user_id=user_uuid, limit=limit, offset=offset
    )
    return {
        "items": [_row_to_run_list_item(r) for r in page["items"]],
        "total": page["total"],
        "limit": limit,
        "offset": offset,
    }


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
    return row


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
    telemetry pipeline for chat too."""
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
) -> List[Dict[str, Any]]:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be 1..200")
    svc = AILibraryChatService()
    user_uuid = _coerce_user_uuid(auth.user_id)
    return await svc.list_sessions(
        user_id=user_uuid, agent_slug=slug, project_id=project_id, limit=limit
    )


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
    )
    return {
        "message": result["assistant_message"],
        "usage": result["usage"],
        "run_id": result["run_id"],
        "tool_calls": result.get("tool_calls", []),
        "attachment_failures": result.get("attachment_failures", []),  # G2
    }


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
    import json

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
            ):
                # evt: dict with type + payload
                event_name = evt.get("type", "delta")
                data = json.dumps(evt.get("data") or {}, ensure_ascii=False)
                yield f"event: {event_name}\ndata: {data}\n\n"
        except Exception as exc:
            data = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
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
    from datetime import timedelta as _td

    end = _dt.now(timezone.utc)
    start = end - _td(days=days)

    client = await get_async_supabase_admin()
    result = (
        await client.table("agent_runs")
        .select(
            "agent_id,user_id,status,prompt_tokens,completion_tokens,"
            "total_tokens,cost_cents,started_at,error_code"
        )
        .gte("started_at", start.isoformat())
        .lte("started_at", end.isoformat())
        .order("started_at", desc=True)
        .limit(20000)
        .execute()
    )
    rows = result.data or []

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


# ─── O5: User memory listing (visualization page) ─────────────────────


@router.get(
    "/memories",
    summary="List the caller's agent memories (for visualization page)",
)
async def list_my_memories(
    auth: AuthDep,
    agent_slug: Optional[str] = None,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Return memories visible to the calling user.

    Filters:
      - agent_slug: limit to one agent (else all agents user has touched)
      - status: 'active' / 'archived' / 'superseded'
      - kind:   'declarative' / 'procedural' / 'episodic'
      - limit:  1..500, default 100
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be 1..500")

    client = await get_async_supabase_admin()
    q = (
        client.table("agent_memories")
        .select(
            "id,agent_id,user_id,scope,summary,when_to_use,status,kind,"
            "thread_id,session_id,extracted_from,reinforce_count,"
            "last_reinforced_at,decay_score,created_at,updated_at"
        )
        .eq("user_id", str(auth.user_id))
        .order("updated_at", desc=True)
        .limit(limit)
    )
    if status:
        q = q.eq("status", status)
    if kind:
        q = q.eq("kind", kind)
    if agent_slug:
        # Resolve agent_id from slug
        agent_repo = get_agent_repository()
        agent = await agent_repo.get_by_slug(agent_slug)
        if not agent:
            raise HTTPException(
                status_code=404, detail=f"agent slug not found: {agent_slug}"
            )
        q = q.eq("agent_id", str(agent["id"]))

    result = await q.execute()
    rows = result.data or []

    # Aggregate stats
    by_kind: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    by_agent: Dict[str, int] = {}
    for r in rows:
        k = r.get("kind") or "unknown"
        s = r.get("status") or "unknown"
        a = r.get("agent_id") or "unknown"
        by_kind[k] = by_kind.get(k, 0) + 1
        by_status[s] = by_status.get(s, 0) + 1
        by_agent[a] = by_agent.get(a, 0) + 1

    return {
        "items": rows,
        "count": len(rows),
        "stats": {
            "by_kind": by_kind,
            "by_status": by_status,
            "by_agent": by_agent,
        },
    }


@router.delete(
    "/memories/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a memory (user-initiated soft delete)",
)
async def archive_memory(memory_id: UUID, auth: AuthDep) -> None:
    """Mark a memory archived. Only the user who owns it (user_id match)
    can archive; archived memories are excluded from recall but kept
    for audit/replay (R3 snapshot lineage)."""
    client = await get_async_supabase_admin()
    existing = (
        await client.table("agent_memories")
        .select("user_id")
        .eq("id", str(memory_id))
        .maybe_single()
        .execute()
    )
    if not existing or not existing.data:
        raise HTTPException(status_code=404, detail="memory not found")
    if existing.data.get("user_id") != str(auth.user_id):
        raise HTTPException(status_code=404, detail="memory not found")

    await (
        client.table("agent_memories")
        .update({"status": "archived"})
        .eq("id", str(memory_id))
        .execute()
    )


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

    client = await get_async_supabase_admin()
    result = (
        await client.table("ai_agent_versions")
        .select(
            "id,version_number,model,temperature,max_tokens,notes,created_by,created_at"
        )
        .eq("agent_id", str(agent["id"]))
        .order("version_number", desc=True)
        .limit(limit)
        .execute()
    )
    return {
        "items": _serialize_versions(result.data or [], kind="agent"),
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
    client = await get_async_supabase_admin()
    result = (
        await client.table("ai_agent_versions")
        .select("*")
        .eq("agent_id", str(agent["id"]))
        .eq("version_number", version_number)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="version not found")
    row = dict(result.data)
    row["id"] = str(row["id"])
    if row.get("created_by"):
        row["created_by"] = str(row["created_by"])
    return row


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
    client = await get_async_supabase_admin()
    # Fetch the snapshot
    snap_q = (
        await client.table("ai_agent_versions")
        .select("identity_md,soul_md,agent_md,model,temperature,max_tokens")
        .eq("agent_id", str(agent["id"]))
        .eq("version_number", version_number)
        .maybe_single()
        .execute()
    )
    if not snap_q or not snap_q.data:
        raise HTTPException(status_code=404, detail="version not found")
    snap = snap_q.data

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
    client = await get_async_supabase_admin()
    result = (
        await client.table("skill_versions")
        .select("id,version_number,notes,created_by,created_at")
        .eq("skill_id", int(skill["id"]))
        .order("version_number", desc=True)
        .limit(limit)
        .execute()
    )
    return {
        "items": _serialize_versions(result.data or [], kind="skill"),
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
    client = await get_async_supabase_admin()
    result = (
        await client.table("skill_versions")
        .select("*")
        .eq("skill_id", int(skill["id"]))
        .eq("version_number", version_number)
        .maybe_single()
        .execute()
    )
    if not result or not result.data:
        raise HTTPException(status_code=404, detail="version not found")
    row = dict(result.data)
    row["id"] = str(row["id"])
    if row.get("created_by"):
        row["created_by"] = str(row["created_by"])
    return row


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
    client = await get_async_supabase_admin()
    # Find the file row first
    file_q = (
        await client.table("skill_files")
        .select("id,current_version")
        .eq("skill_id", int(skill["id"]))
        .eq("path", path)
        .maybe_single()
        .execute()
    )
    if not file_q or not file_q.data:
        raise HTTPException(status_code=404, detail="skill file not found")
    file_id = file_q.data["id"]
    cur_v = file_q.data.get("current_version")

    result = (
        await client.table("skill_file_versions")
        .select("id,version_number,path,file_type,notes,created_by,created_at")
        .eq("skill_file_id", str(file_id))
        .order("version_number", desc=True)
        .limit(limit)
        .execute()
    )
    return {
        "items": _serialize_versions(result.data or [], kind="skill_file"),
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
    client = await get_async_supabase_admin()
    snap_q = (
        await client.table("skill_versions")
        .select("body_md,frontmatter_json")
        .eq("skill_id", skill_id)
        .eq("version_number", version_number)
        .maybe_single()
        .execute()
    )
    if not snap_q or not snap_q.data:
        raise HTTPException(status_code=404, detail="version not found")
    snap = snap_q.data

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
