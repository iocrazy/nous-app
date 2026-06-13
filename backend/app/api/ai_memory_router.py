# backend/app/api/ai_memory_router.py

"""User-facing memory management (Claude-style).

What the AI has learned about the requesting user (Honcho observations),
their self-curated "About me" card, per-item delete, and a
forget-everything reset. All endpoints are scoped to the authenticated
user's own peer — there is no way to read or mutate another user's
memory. Raw chat messages are retained; only derived memory is managed
here.

Workspace param: omitted/"default" → deployment default workspace;
"team-{id}" → requires team membership.
"""

from __future__ import annotations

import re
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.deps import AuthDep
from app.repositories.team_repository import TeamRepository
from app.repositories.user_settings_repository import UserSettingsRepository
from app.services.ai.memory.honcho_memory import get_honcho_memory_service
from app.services.ai.memory.memory_prefs import get_memory_prefs

router = APIRouter(prefix="/ai/memory", tags=["AI Memory"])

_TEAM_WS = re.compile(r"^team-(\d+)$")
_MAX_CARD_LINES = 20
_MAX_CARD_LINE_CHARS = 200


class CardUpdate(BaseModel):
    lines: List[str] = Field(..., max_length=_MAX_CARD_LINES)


class PrefsUpdate(BaseModel):
    learn_enabled: Optional[bool] = None
    inject_enabled: Optional[bool] = None


class MemoryProfile(BaseModel):
    workspace: str
    service_available: bool
    learn_enabled: bool
    inject_enabled: bool
    card: Optional[List[str]] = None
    observations: List[dict] = Field(default_factory=list)


async def _resolve_workspace(workspace: Optional[str], user_id: str) -> Optional[str]:
    """Validate + map the workspace query param.

    Returns None for the deployment default, ``team-{id}`` after a
    membership check, raises 422/403 otherwise.
    """
    if not workspace or workspace == "default":
        return None
    match = _TEAM_WS.match(workspace)
    if not match:
        raise HTTPException(status_code=422, detail="Invalid workspace")
    team = await TeamRepository().get_team_by_id(match.group(1), str(user_id))
    if not team:
        raise HTTPException(status_code=403, detail="Not a member of this team")
    return workspace


@router.get("/profile", response_model=MemoryProfile)
async def get_memory_profile(
    auth: AuthDep, workspace: Optional[str] = Query(default=None)
):
    """Everything the system remembers about the requesting user."""
    user_id = str(auth.user_id)
    ws = await _resolve_workspace(workspace, user_id)
    service = get_honcho_memory_service()
    prefs = await get_memory_prefs(user_id)

    available = service.config.operative()
    observations: List[dict] = []
    card: Optional[List[str]] = None
    if available:
        observations = await service.list_conclusions(user_id=user_id, workspace_id=ws)
        card = await service.get_peer_card(user_id=user_id, workspace_id=ws)

    return MemoryProfile(
        workspace=workspace or "default",
        service_available=available,
        learn_enabled=prefs.learn,
        inject_enabled=prefs.inject,
        card=card,
        observations=observations,
    )


@router.put("/prefs")
async def put_memory_prefs(body: PrefsUpdate, auth: AuthDep):
    """Toggle the per-user learn / inject switches. Patches only the two
    keys into the shared ai_settings subtree (merge, never clobber —
    same contract as #485)."""
    user_id = str(auth.user_id)
    patch: dict = {}
    if body.learn_enabled is not None:
        patch["memory_learn_enabled"] = body.learn_enabled
    if body.inject_enabled is not None:
        patch["memory_inject_enabled"] = body.inject_enabled
    if patch:
        repo = UserSettingsRepository()
        existing = await repo.get_by_user_id(user_id)
        settings_json = (existing or {}).get("settings_json") or {}
        ai_settings = dict(settings_json.get("ai_settings") or {})
        ai_settings.update(patch)
        await repo.patch_settings_json(user_id, {"ai_settings": ai_settings})
    prefs = await get_memory_prefs(user_id)
    return {"learn_enabled": prefs.learn, "inject_enabled": prefs.inject}


@router.put("/card")
async def put_memory_card(
    body: CardUpdate, auth: AuthDep, workspace: Optional[str] = Query(default=None)
):
    """Replace the user's self-curated "About me" card."""
    user_id = str(auth.user_id)
    ws = await _resolve_workspace(workspace, user_id)
    lines = [line.strip() for line in body.lines if line.strip()]
    for line in lines:
        if len(line) > _MAX_CARD_LINE_CHARS:
            raise HTTPException(
                status_code=422,
                detail=f"Card lines must be at most {_MAX_CARD_LINE_CHARS} characters",
            )
    service = get_honcho_memory_service()
    ok = await service.set_peer_card(user_id=user_id, lines=lines, workspace_id=ws)
    if not ok:
        raise HTTPException(status_code=502, detail="Memory service unavailable")
    return {"saved": True, "lines": lines}


@router.delete("/observations/{conclusion_id}")
async def delete_memory_observation(
    conclusion_id: str,
    auth: AuthDep,
    workspace: Optional[str] = Query(default=None),
):
    """Delete a single observation — only if it is about the caller."""
    user_id = str(auth.user_id)
    ws = await _resolve_workspace(workspace, user_id)
    service = get_honcho_memory_service()
    item = await service.get_conclusion(conclusion_id=conclusion_id, workspace_id=ws)
    if not item or item.get("observed_id") != f"user-{user_id}":
        # 404 for both missing and not-yours — don't leak existence.
        raise HTTPException(status_code=404, detail="Observation not found")
    ok = await service.delete_conclusion(conclusion_id=conclusion_id, workspace_id=ws)
    if not ok:
        raise HTTPException(status_code=502, detail="Memory service unavailable")
    return {"deleted": conclusion_id}


@router.delete("")
async def forget_all_memory(
    auth: AuthDep, workspace: Optional[str] = Query(default=None)
):
    """Forget everything derived about the caller (observations + card).

    Raw chat history is retained — same semantics as deleting memory in
    Claude: the model stops "knowing" you, your transcripts stay."""
    user_id = str(auth.user_id)
    ws = await _resolve_workspace(workspace, user_id)
    service = get_honcho_memory_service()
    deleted = await service.forget_user(user_id=user_id, workspace_id=ws)
    return {"deleted": deleted}
