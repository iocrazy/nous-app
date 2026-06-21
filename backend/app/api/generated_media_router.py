"""Generations library — read-only Tier-1 surfacing (sub-plan 5).

Routes (all under prefix /generated-media, registered in app/api/__init__.py):
  GET  /generated-media                → {data: {items, next_cursor}}
  GET  /generated-media/{id}           → {data: row}   (404 if not in scope)
  GET  /generated-media/{id}/file      → FileResponse  (404 if row/file missing)
  DELETE /generated-media/{id}         → {data: {deleted: bool}}

Scope = caller's personal team resolved via _resolve_personal_team_id.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.deps import AuthDep
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.services.library.resources_service import _resolve_personal_team_id

router = APIRouter(prefix="/generated-media", tags=["generated-media"])


async def _scope(auth) -> int:
    """Resolve the caller's personal team id as an int scope key."""
    return int(await _resolve_personal_team_id(str(auth.user_id)))


@router.get("")
async def list_generations(
    auth: AuthDep,
    kind: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=100),
) -> dict:
    page = await GeneratedMediaRepository().list_for_scope(
        await _scope(auth), kind=kind, cursor=cursor, limit=limit
    )
    return {"data": page}


@router.get("/{gen_id}/file")
async def get_generation_file(gen_id: int, auth: AuthDep):
    row = await GeneratedMediaRepository().get(gen_id, await _scope(auth))
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    path = os.path.join(settings.DOWNLOAD_PATH, row["file_path"])
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(path, media_type=row.get("mime") or "application/octet-stream")


@router.get("/{gen_id}")
async def get_generation(gen_id: int, auth: AuthDep) -> dict:
    row = await GeneratedMediaRepository().get(gen_id, await _scope(auth))
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return {"data": row}


@router.delete("/{gen_id}")
async def delete_generation(gen_id: int, auth: AuthDep) -> dict:
    ok = await GeneratedMediaRepository().delete(gen_id, await _scope(auth))
    return {"data": {"deleted": ok}}
