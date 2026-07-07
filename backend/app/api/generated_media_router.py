"""Generations library — read-only Tier-1 surfacing (sub-plan 5).

Routes (all under prefix /generated-media, registered in app/api/__init__.py):
  GET  /generated-media                → {data: {items, next_cursor}}
  GET  /generated-media/{id}           → {data: row}   (404 if not in scope)
  GET  /generated-media/{id}/cover     → FileResponse  (image, no auth — <img>)
  GET  /generated-media/{id}/stream    → FileResponse  (video, no auth — <video>)
  GET  /generated-media/{id}/file      → FileResponse  (404 if row/file missing)
  DELETE /generated-media/{id}         → {data: {deleted: bool}}

Scope = caller's personal team resolved via _resolve_personal_team_id.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.core.config import settings
from app.core.deps import AuthDep
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.services.library.media_storage import ObjectStore, resolve_media_source
from app.services.library.promote_generated_media_service import (
    PromoteGeneratedMediaService,
)
from app.services.library.resources_service import _resolve_personal_team_id

router = APIRouter(prefix="/generated-media", tags=["generated-media"])


async def _serve_media_row(row: dict, *, headers: Optional[dict] = None):
    """Serve a generated_media row's bytes from whichever backend holds it.

    Filesystem rows keep the efficient FileResponse (sendfile). Object-store
    rows are stream-proxied through the backend — the endpoint contract (same
    URL, same auth gate, same cache headers) is preserved and the storage host
    stays hidden; the browser needs no change. Small images only, so buffering
    is fine. Raises 404 on a missing/escaping file either way.
    """
    mime = row.get("mime") or "application/octet-stream"
    loc = resolve_media_source(row["file_path"])
    if loc.is_object_store:
        try:
            data = await ObjectStore(loc.bucket).get_bytes(loc.key)
        except Exception:
            raise HTTPException(status_code=404, detail="file missing")
        return Response(content=data, media_type=mime, headers=headers)
    base = os.path.realpath(settings.DOWNLOAD_PATH)
    real = os.path.realpath(os.path.join(settings.DOWNLOAD_PATH, loc.rel_path))
    if not (real == base or real.startswith(base + os.sep)):
        raise HTTPException(status_code=404, detail="not found")
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(real, media_type=mime, headers=headers)


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


@router.get("/{gen_id}/cover")
async def get_generation_cover(gen_id: int):
    """Serve a thumbnail/preview for a generated-media item (no auth required).

    Thumbnails are world-readable-by-id (same posture as GET /resources/{id}/cover).
    Use this URL in browser <img> tags — no Bearer header needed.
    Keep GET /{gen_id}/file for auth-gated full-resolution downloads.
    """
    row = await GeneratedMediaRepository().get_by_id(gen_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row.get("media_kind") != "image":
        raise HTTPException(status_code=404, detail="no cover")
    return await _serve_media_row(
        row, headers={"Cache-Control": "public, max-age=604800, immutable"}
    )


@router.get("/{gen_id}/stream")
async def get_generation_stream(gen_id: int):
    """Serve a generated VIDEO's bytes (no auth required).

    Same world-readable-by-id posture as ``/cover``: a bare ``<video src>`` can't
    carry a Bearer header, and the snowflake id is unguessable. Video-only —
    image rows 404 here (use ``/cover``). ``/file`` stays the auth-gated download.
    Filesystem rows keep FileResponse (Range-capable); object-store rows buffer.
    """
    row = await GeneratedMediaRepository().get_by_id(gen_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row.get("media_kind") != "video":
        raise HTTPException(status_code=404, detail="no video")
    return await _serve_media_row(
        row, headers={"Cache-Control": "public, max-age=604800, immutable"}
    )


@router.get("/{gen_id}/file")
async def get_generation_file(gen_id: int, auth: AuthDep):
    row = await GeneratedMediaRepository().get(gen_id, await _scope(auth))
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return await _serve_media_row(row)


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


@router.post("/{gen_id}/promote")
async def promote_generation(gen_id: int, auth: AuthDep) -> dict:
    target_scope_id = await _scope(auth)
    try:
        resource = await PromoteGeneratedMediaService().promote(
            gen_id=gen_id, user_id=str(auth.user_id), target_scope_id=target_scope_id
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"data": {"promoted_resource_id": str(resource["id"])}}
