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

from pathlib import Path
from typing import Any, Dict, List
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
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
from app.services.seed_loader import SeedLoader

router = APIRouter(prefix="/ai-library", tags=["AI Library"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repos() -> tuple[AgentRepository, SkillRepository]:
    """Return a fresh (AgentRepository, SkillRepository) pair per request."""
    return AgentRepository(), SkillRepository()


def _coerce_user_uuid(user_id: str) -> UUID:
    """Convert an auth user_id string to UUID; 401 on malformed token."""
    try:
        return UUID(user_id)
    except (TypeError, ValueError) as exc:
        logger.error("[ai-library] malformed user_id from auth: %r (%s)", user_id, exc)
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
async def list_agents(auth: AuthDep) -> List[Dict[str, Any]]:
    """Return all agents visible to the current user with their skill bindings.

    Visible set = union of:
      * system presets (``is_system_preset=true``)
      * user's own agents (``user_id = me``)
      * agents on any team the user is a member of
      * agents on any project the user owns or is a member of

    Each row is enriched with ``skill_ids`` (ordered, enabled only) plus the
    denormalized ``team_name`` / ``project_name`` for UI scope badges.
    """
    agent_repo, _ = _repos()
    user_uuid = _coerce_user_uuid(auth.user_id)
    team_ids = await _fetch_user_team_ids(user_uuid)
    project_ids = await _fetch_user_project_ids(user_uuid)
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
            logger.warning("[ai-library] skipping agent with bad id: %s", exc)
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
                    f"user is not the owner or a member of project "
                    f"{payload.project_id}"
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
        "persona": payload.description or f"{payload.name} (user-created)",
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


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@router.get("/skills", response_model=List[SkillOut], summary="List accessible skills")
async def list_skills(auth: AuthDep) -> List[Dict[str, Any]]:
    """Return skills visible to the current user, each enriched with its files.

    Visible set = public (system) skills + user's own + team/project-scoped
    skills for teams/projects the user belongs to.
    """
    _, skill_repo = _repos()
    user_uuid = _coerce_user_uuid(auth.user_id)
    team_ids = await _fetch_user_team_ids(user_uuid)
    project_ids = await _fetch_user_project_ids(user_uuid)
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
            logger.warning("[ai-library] skipping skill with bad id: %s", exc)
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
    row = {**skill, "files": await skill_repo.list_files(skill_id)}
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
                    f"user is not the owner or a member of project "
                    f"{payload.project_id}"
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
    fields: Dict[str, Any] = {
        "slug": payload.slug,
        "name": payload.name,
        "description": payload.description,
        "category": payload.category,
        "icon": payload.icon if payload.icon is not None else "✨",
        "body_md": payload.body_md,
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
    return await skill_repo.upsert_file_versioned(
        skill_id=skill_id,
        path=path,
        content=payload.content,
        file_type=payload.file_type,
        binary_url=payload.binary_url,
        created_by=user_uuid,
    )


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
