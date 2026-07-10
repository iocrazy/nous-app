"""Episodes Router — episode dimension CRUD (Phase B P2, spec v3 §2.1).

Endpoints:
  GET  /projects/{project_id}/episodes   — verify_project_read_access
  POST /projects/{project_id}/episodes   — verify_project_write_access
  PATCH  /episodes/{episode_id}          — verify_episode_write_access
  DELETE /episodes/{episode_id}          — verify_episode_write_access

Every route declares an authz guard as a FastAPI dependency (structural
IDOR fix, pinned by test_episodes_scenes_authz_wiring.py). The PATCH/DELETE
guards resolve episode → project before applying project write-access.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.core.deps import AuthDep
from app.core.scope_guards import (
    verify_episode_write_access,
    verify_project_read_access,
    verify_project_write_access,
)
from app.repositories.episode_repository import get_episode_repository
from app.schemas.script import EpisodeCreate, EpisodeUpdate

router = APIRouter()


@router.get("/projects/{project_id}/episodes")
async def list_episodes(
    project_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_project_read_access),
) -> Dict[str, Any]:
    """List all episodes for a project, ordered by sort_order."""
    try:
        episodes = await get_episode_repository().list_by_project(project_id)
        return {"success": True, "data": episodes}
    except Exception as exc:
        logger.error(f"[Episodes] list for project {project_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list episodes")


@router.get("/projects/{project_id}/episodes/progress")
async def get_episodes_progress(
    project_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_project_read_access),
) -> Dict[str, Any]:
    """Per-episode progress (script/scene/shot counts + derived status) for
    the workspace shell Episodes panel (spec G12). A static 'progress'
    segment after the collection path — no route-order conflict with the
    single-segment `/episodes` list above, FastAPI matches by full path."""
    try:
        items = await get_episode_repository().progress_by_project(project_id)
        return {"success": True, "data": items}
    except Exception as exc:
        logger.error(f"[Episodes] progress for project {project_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to load episode progress")


@router.post("/projects/{project_id}/episodes")
async def create_episode(
    project_id: str,
    auth: AuthDep,
    body: EpisodeCreate,
    _guard: None = Depends(verify_project_write_access),
) -> Dict[str, Any]:
    """Create an episode under a project. Title defaults to 'Ep 1' (DB
    default) when omitted."""
    try:
        data = body.model_dump(exclude_none=True)
        data["project_id"] = project_id
        episode = await get_episode_repository().create(data)
        return {"success": True, "data": episode}
    except Exception as exc:
        logger.error(f"[Episodes] create for project {project_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to create episode")


@router.patch("/episodes/{episode_id}")
async def update_episode(
    episode_id: str,
    auth: AuthDep,
    body: EpisodeUpdate,
    _guard: None = Depends(verify_episode_write_access),
) -> Dict[str, Any]:
    """Update an episode (title / sort_order)."""
    try:
        episode = await get_episode_repository().update(
            episode_id, body.model_dump(exclude_none=True)
        )
        if episode is None:
            raise HTTPException(status_code=404, detail="Episode not found")
        return {"success": True, "data": episode}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[Episodes] update {episode_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update episode")


@router.delete("/episodes/{episode_id}")
async def delete_episode(
    episode_id: str,
    auth: AuthDep,
    _guard: None = Depends(verify_episode_write_access),
) -> Dict[str, Any]:
    """Delete an episode.

    script_projects.episode_id is ON DELETE RESTRICT (mig 338): deleting an
    episode that still owns scripts violates the FK. Surface that as a clean
    409 instead of a raw 500 — caught by the live API round-trip 2026-07-06.
    """
    try:
        await get_episode_repository().delete(episode_id)
        return {"success": True}
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "episode_not_empty",
                "message": "Episode still has scripts",
            },
        )
    except Exception as exc:
        logger.error(f"[Episodes] delete {episode_id} failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to delete episode")
