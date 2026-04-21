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
from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.schemas.ai_library import (
    AgentCreate,
    AgentOut,
    AgentUpdate,
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
# Agents
# ---------------------------------------------------------------------------


@router.get("/agents", response_model=List[AgentOut], summary="List accessible agents")
async def list_agents(auth: AuthDep) -> List[Dict[str, Any]]:
    """Return all agents visible to the current user with their skill bindings.

    Includes system presets + user-owned agents (+ team/project scoped when
    applicable). Each row is enriched with ``skill_ids`` (ordered, enabled only).
    """
    agent_repo, _ = _repos()
    user_uuid = _coerce_user_uuid(auth.user_id)
    rows = await agent_repo.list_accessible(user_id=user_uuid)

    enriched: List[Dict[str, Any]] = []
    for row in rows:
        try:
            agent_uuid = UUID(str(row["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("[ai-library] skipping agent with bad id: %s", exc)
            continue
        skill_ids = await agent_repo.get_skill_ids(agent_uuid)
        enriched.append({**row, "skill_ids": skill_ids})
    return enriched


@router.get(
    "/agents/{slug}",
    response_model=AgentOut,
    summary="Get a single agent by slug",
)
async def get_agent(slug: str, auth: AuthDep) -> Dict[str, Any]:
    """Fetch a single agent by slug, 404 if missing."""
    agent_repo, _ = _repos()
    agent = await agent_repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent not found"
        )
    agent_uuid = UUID(str(agent["id"]))
    agent = {**agent, "skill_ids": await agent_repo.get_skill_ids(agent_uuid)}
    return agent


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

    If ``fork_from`` is set, copies identity_md / soul_md / agent_md /
    model / temperature / max_tokens from that agent as a starting point.
    Explicit fields in the payload override forked values. Skill bindings
    are NOT copied — the user adds them separately via PATCH.
    """
    agent_repo, _ = _repos()

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
        "user_id": str(_coerce_user_uuid(auth.user_id)),
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
    return {**created, "skill_ids": []}


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
    return {**refreshed, "skill_ids": await agent_repo.get_skill_ids(agent_uuid)}


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@router.get("/skills", response_model=List[SkillOut], summary="List accessible skills")
async def list_skills(auth: AuthDep) -> List[Dict[str, Any]]:
    """Return skills visible to the current user, each enriched with its files."""
    _, skill_repo = _repos()
    user_uuid = _coerce_user_uuid(auth.user_id)
    skills = await skill_repo.list_accessible(user_id=user_uuid)

    enriched: List[Dict[str, Any]] = []
    for s in skills:
        try:
            skill_id = int(s["id"])
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("[ai-library] skipping skill with bad id: %s", exc)
            continue
        files = await skill_repo.list_files(skill_id)
        enriched.append({**s, "files": files})
    return enriched


@router.get(
    "/skills/{slug}", response_model=SkillOut, summary="Get a single skill by slug"
)
async def get_skill(slug: str, auth: AuthDep) -> Dict[str, Any]:
    """Fetch a single skill (with its files) by slug, 404 if missing."""
    _, skill_repo = _repos()
    skill = await skill_repo.get_by_slug(slug)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="skill not found"
        )
    skill_id = int(skill["id"])
    return {**skill, "files": await skill_repo.list_files(skill_id)}


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
    return {**refreshed, "files": await skill_repo.list_files(skill_id)}


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
