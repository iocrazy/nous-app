"""Admin API for the system-agent catalog (the master set users overlay).

The override model (mig 341) makes the admin the owner of the CATALOG: system
preset rows in ai_agents are the single source each user/team customizes via
agent_overrides. This router gives admin the missing CRUD surface — before it,
presets were seed-file-driven only.

Editing here writes the BASE row (versioned via ai_agent_versions), which is
what un-customized users see immediately. User/team override layers are
untouched. Note: a later seed-file change re-seeds the row (seed files stay
authoritative over admin edits when the file itself changes).
"""

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.core.admin_deps import AdminAuthDep
from app.repositories.agent_repository import get_agent_repository

router = APIRouter()

# Base-row fields admin may edit from the catalog page. Slug stays immutable
# (it is the stable FK-ish identifier seeds / sessions / code reference).
_ADMIN_EDITABLE = (
    "name",
    "description",
    "icon",
    "model",
    "temperature",
    "max_tokens",
    "identity_md",
    "soul_md",
    "agent_md",
    "fallback_models",
    "enabled",
)


class AdminAgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = Field(default=None, max_length=64)
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    identity_md: Optional[str] = None
    soul_md: Optional[str] = None
    agent_md: Optional[str] = None
    fallback_models: Optional[List[str]] = None
    enabled: Optional[bool] = None


@router.get("")
async def list_catalog_agents(auth: AdminAuthDep) -> Dict[str, Any]:
    """System-preset catalog + per-agent override adoption counts."""
    repo = get_agent_repository()
    presets = await repo.list_presets()
    counts = await repo.count_overrides_by_agent()
    items = [
        {
            **{
                k: p.get(k)
                for k in (
                    "id",
                    "slug",
                    "name",
                    "description",
                    "icon",
                    "model",
                    "temperature",
                    "max_tokens",
                    "identity_md",
                    "soul_md",
                    "agent_md",
                    "fallback_models",
                    "enabled",
                    "updated_at",
                    "current_version",
                )
            },
            "override_counts": counts.get(str(p["id"]), {"user": 0, "team": 0}),
        }
        for p in presets
    ]
    return {"items": items, "total": len(items)}


@router.put("/{slug}")
async def update_catalog_agent(
    slug: str, body: AdminAgentUpdate, auth: AdminAuthDep
) -> Dict[str, Any]:
    """Edit a system preset's BASE row (versioned). Overrides untouched."""
    repo = get_agent_repository()
    agent = await repo.get_by_slug(slug)
    if not agent or not agent.get("is_system_preset"):
        raise HTTPException(status_code=404, detail="system preset not found")

    updates = {
        k: v
        for k, v in body.model_dump(exclude_none=True).items()
        if k in _ADMIN_EDITABLE
    }
    if not updates:
        raise HTTPException(status_code=400, detail="no editable fields provided")

    await repo.update_fields_versioned(
        UUID(str(agent["id"])),
        updates,
        created_by=UUID(str(auth.user_id)),
        notes="admin catalog edit",
    )
    logger.info(f"[Admin] catalog agent '{slug}' updated: {sorted(updates)}")

    refreshed = await repo.get_by_slug(slug)
    counts = await repo.count_overrides_by_agent()
    return {
        **(refreshed or {}),
        "override_counts": counts.get(str(agent["id"]), {"user": 0, "team": 0}),
    }
