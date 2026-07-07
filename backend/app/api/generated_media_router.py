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

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.core.config import settings
from app.core.deps import AuthDep
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.services.library.media_storage import ObjectStore, resolve_media_source
from app.services.library.promote_generated_media_service import (
    PromoteGeneratedMediaService,
)
from app.services.library.resources_service import _resolve_personal_team_id

router = APIRouter(prefix="/generated-media", tags=["generated-media"])


class _RangeNotSatisfiable(Exception):
    """A syntactically valid byte-range that falls outside the object (→ 416)."""


def _parse_byte_range(
    range_header: Optional[str], size: int
) -> Optional[tuple[int, int]]:
    """Parse a single HTTP byte-range against a known object ``size``.

    Returns an inclusive ``(start, end)`` pair, or ``None`` when there is no
    usable range and the full object should be served (200). A malformed or
    multi-range header is ignored per RFC 7233 (serve full), but a well-formed
    range that lies outside the object raises ``_RangeNotSatisfiable`` (416).
    """
    if not range_header:
        return None
    header = range_header.strip()
    if not header.startswith("bytes="):
        return None  # unknown unit → ignore
    spec = header[len("bytes=") :].strip()
    if "," in spec or "-" not in spec:
        return None  # multi-range unsupported / malformed → serve full
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s == "":
            # suffix range: bytes=-N → last N bytes
            n = int(end_s)
            if n <= 0:
                raise _RangeNotSatisfiable
            start, end = max(0, size - n), size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s != "" else size - 1
    except ValueError:
        return None  # non-integer bounds → ignore
    if size == 0 or start > end or start >= size:
        raise _RangeNotSatisfiable
    return start, min(end, size - 1)


def _filesystem_response(loc, mime: str, headers: Optional[dict]):
    """Serve a filesystem-backed media file (FileResponse; Range-capable).

    Guards against path traversal escaping DOWNLOAD_PATH. 404 on escape/miss.
    """
    base = os.path.realpath(settings.DOWNLOAD_PATH)
    real = os.path.realpath(os.path.join(settings.DOWNLOAD_PATH, loc.rel_path))
    if not (real == base or real.startswith(base + os.sep)):
        raise HTTPException(status_code=404, detail="not found")
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(real, media_type=mime, headers=headers)


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
    return _filesystem_response(loc, mime, headers)


async def _serve_video_stream(
    row: dict, request: Request, *, headers: Optional[dict] = None
):
    """Serve a video row with HTTP Range support (progress-bar seeking).

    Filesystem rows keep FileResponse (Starlette already answers Range with
    206). Object-store rows are streamed chunk-by-chunk with the request's
    Range header passed through to storage — no full-file memory spike, and a
    ``<video>`` can seek. A well-formed but out-of-bounds range yields 416.
    """
    mime = row.get("mime") or "application/octet-stream"
    loc = resolve_media_source(row["file_path"])
    headers = dict(headers or {})
    if not loc.is_object_store:
        return _filesystem_response(loc, mime, headers)

    store = ObjectStore(loc.bucket)
    try:
        size = await store.get_size(loc.key)
    except Exception:
        raise HTTPException(status_code=404, detail="file missing")

    try:
        byte_range = _parse_byte_range(request.headers.get("range"), size)
    except _RangeNotSatisfiable:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        )

    if byte_range is None:
        return StreamingResponse(
            store.get_stream(loc.key),
            status_code=200,
            media_type=mime,
            headers={
                **headers,
                "Accept-Ranges": "bytes",
                "Content-Length": str(size),
            },
        )

    start, end = byte_range
    return StreamingResponse(
        store.get_stream(loc.key, start=start, end=end),
        status_code=206,
        media_type=mime,
        headers={
            **headers,
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        },
    )


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
async def get_generation_stream(gen_id: int, request: Request):
    """Serve a generated VIDEO's bytes (no auth required).

    Same world-readable-by-id posture as ``/cover``: a bare ``<video src>`` can't
    carry a Bearer header, and the snowflake id is unguessable. Video-only —
    image rows 404 here (use ``/cover``). ``/file`` stays the auth-gated download.
    Both backends support Range: filesystem via FileResponse, object-store via a
    streamed range-passthrough (no full-file memory spike).
    """
    row = await GeneratedMediaRepository().get_by_id(gen_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row.get("media_kind") != "video":
        raise HTTPException(status_code=404, detail="no video")
    return await _serve_video_stream(
        row,
        request,
        headers={"Cache-Control": "public, max-age=604800, immutable"},
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
