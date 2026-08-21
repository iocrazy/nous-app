"""Generations library — read-only Tier-1 surfacing (sub-plan 5).

Routes (all under prefix /generated-media, registered in app/api/__init__.py):
  GET  /generated-media                → {data: {items, next_cursor}}
  POST /generated-media/import         → {data: {id, url, media_kind, mime}}
  GET  /generated-media/{id}           → {data: row}   (404 if not in scope)
  GET  /generated-media/{id}/cover     → FileResponse  (image, no auth — <img>)
  GET  /generated-media/{id}/stream    → FileResponse  (video, no auth — <video>)
  GET  /generated-media/{id}/file      → FileResponse  (404 if row/file missing)
  DELETE /generated-media/{id}         → {data: {deleted: bool}}

Scope = caller's personal team resolved via _resolve_personal_team_id.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.db.scope import Scope, request_scope
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.services.library.media_serving import (
    filesystem_response,
    range_stream_response,
)
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
    return filesystem_response(loc.rel_path or "", mime, headers)


async def _serve_video_stream(
    row: dict, request: Request, *, headers: Optional[dict] = None
):
    """Serve a video row with HTTP Range support (progress-bar seeking).

    Delegates to the shared media_serving helpers. Deliberately does NOT use
    serve_stored_file's 302 branch: STORAGE_SIGNED_URL_PUBLIC_BASE may be set
    for agent vision, and generated-media endpoints must keep their current
    stream-proxy behavior until the unified-storage rollout revisits them.
    """
    mime = row.get("mime") or "application/octet-stream"
    loc = resolve_media_source(row["file_path"])
    if not loc.is_object_store:
        return filesystem_response(loc.rel_path or "", mime, dict(headers or {}))
    return await range_stream_response(
        ObjectStore(loc.bucket), loc.key, mime, request, headers
    )


async def _scope(auth) -> int:
    """Resolve the caller's personal team id as an int scope key."""
    return int(await _resolve_personal_team_id(str(auth.user_id)))


@router.get("")
async def list_generations(
    auth: AuthDep,
    kind: Optional[str] = Query(None),
    entity_kind: Optional[str] = Query(None, pattern="^(character|location|prop)$"),
    entity_id: Optional[str] = Query(None, max_length=32),
    cursor: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=100),
) -> dict:
    page = await GeneratedMediaRepository().list_for_scope(
        await _scope(auth),
        kind=kind,
        entity_kind=entity_kind,
        entity_id=entity_id,
        cursor=cursor,
        limit=limit,
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


IMPORT_MAX_BYTES = 50 * 1024 * 1024  # matches Infinite-Canvas's 50MB upload cap
IMPORT_CHUNK_BYTES = 1024 * 1024
_IMPORT_KIND_ENDPOINT = {"image": "cover", "video": "stream"}


@router.post("/import")
async def import_generation(
    auth: AuthDep,
    file: UploadFile = File(...),
    canvas_id: Optional[str] = Form(None),
    node_id: Optional[str] = Form(None),
) -> dict:
    """Ingest a user-uploaded image/video into Tier-1 (canvas media node).

    The canvas generation bridge can only read durable /generated-media/
    URLs, so uploads must land in the same store as generations — this is
    the resource-upload-free path the smart canvas's Upload node uses.
    Streams to a temp file (bounded at IMPORT_MAX_BYTES) and reuses
    register_generated_media's object-store/filesystem dual write.
    """
    from app.services.library.generated_media_service import (
        GenerationOrigin,
        media_kind_from_mime,
        register_generated_media,
    )

    mime = (file.content_type or "").lower()
    if not (mime.startswith("image/") or mime.startswith("video/")):
        raise HTTPException(status_code=400, detail="only image/* or video/* uploads")

    canvas_id_int: Optional[int] = None
    if canvas_id:
        try:
            canvas_id_int = int(canvas_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="canvas_id must be an integer")

    tmp_path: Optional[str] = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="genmedia_import_")
        total = 0
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(IMPORT_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > IMPORT_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="file exceeds 50MB")
                out.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="empty file")

        row = await register_generated_media(
            user_id=str(auth.user_id),
            scope_id=await _scope(auth),
            source_path=tmp_path,
            mime=mime,
            origin=GenerationOrigin(
                kind="canvas_upload",
                canvas_id=canvas_id_int,
                node_id=node_id,
                params={"filename": file.filename or ""},
            ),
        )
        gen_id = row.get("id")
        if gen_id is None:
            raise HTTPException(status_code=500, detail="import failed")
        media_kind = media_kind_from_mime(mime)
        endpoint = _IMPORT_KIND_ENDPOINT.get(media_kind, "file")
        return {
            "data": {
                "id": str(gen_id),
                "url": f"/api/v1/generated-media/{gen_id}/{endpoint}",
                "media_kind": media_kind,
                "mime": mime,
            }
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class ResourceImportError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def resolve_resource_import(resource: dict, file_path: Optional[str]) -> dict:
    """Validate a resources row + its ALREADY-RESOLVED path; return import args.

    Pure/sync (no I/O), so it stays testable without mocking — which is why
    ``file_path`` is a PARAMETER rather than something read off ``resource``
    here. Locating the file needs a DB read for platform-downloaded rows (see
    below), and doing that inside this function would have cost it exactly the
    property its docstring promises.

    ``file_path`` comes from ``resolve_resource_file_path`` (the PR-B ladder:
    ``resources.file_path`` → ``parsed_media.download_path``). Reading
    ``resource["file_path"]`` directly — as this did — is wrong for
    ``source_type='web'`` rows, whose path lives only on ``parsed_media``:
    every downloaded video/image 404'd with "Resource has no local file" when
    the user tried to load it as an i2i/i2v reference.

    The ladder also (correctly) resolves image ALBUMS to ``None``: an album is
    a directory of slides, not a single reference image, so 404 is the right
    answer for it here — the guard that protects this call site is the same one
    that would be WRONG for an "is it downloaded?" question (see
    ``ResourcesRepository.get_owned_platform_ids``).

    The returned path is NOT an absolute host path — storage unification means
    it's either a legacy filesystem-relative-to-DOWNLOAD_PATH path or an
    `sb://bucket/key` object-store URI (see resources_service.py /
    resources_crud_router.py "Storage unification" comments and
    media_storage.resolve_media_source). The endpoint resolves it to real
    bytes via `materialize()` — the same shared reader ffprobe/HLS/promote
    already use for either shape.
    """
    file_path = (file_path or "").strip()
    if not file_path:
        raise ResourceImportError(404, "Resource has no local file")
    mime = (resource.get("mime_type") or "").lower()
    if not (mime.startswith("image/") or mime.startswith("video/")):
        raise ResourceImportError(400, "only image/* or video/* resources")
    return {"file_path": file_path, "mime": mime}


class ResourceImportRequest(BaseModel):
    resource_id: str


@router.post("/import-from-resource")
async def import_from_resource(payload: ResourceImportRequest, auth: AuthDep) -> dict:
    """Mint a durable /generated-media/ URL from an existing library resource.

    The canvas i2i bridge only reads durable generated-media URLs
    (promptInputs.ts DURABLE_PREFIX), so loading a library asset as an i2i
    reference requires re-registering its file server-side — no client
    download/upload round-trip.
    """
    from app.api.media_permissions import check_media_access
    from app.repositories.resources_repository import ResourcesRepository
    from app.services.library.generated_media_service import (
        GenerationOrigin,
        media_kind_from_mime,
        register_generated_media,
    )
    from app.services.library.media_storage import materialize

    resource = await ResourcesRepository().get_resource_by_id(payload.resource_id)
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if not await check_media_access(payload.resource_id, auth.user_id, None):
        raise HTTPException(status_code=403, detail="Access denied")
    # PR-B 阶梯：平台下载来的素材路径在 parsed_media,不在 resources 行上。
    # 在这里解析(需要 DB),让 resolve_resource_import 保持纯函数。
    from app.services.library.resource_file_path import resolve_resource_file_path

    resolved_path = await resolve_resource_file_path(resource)
    try:
        args = resolve_resource_import(resource, resolved_path)
    except ResourceImportError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    # materialize() resolves both file_path shapes (filesystem-relative-to-
    # DOWNLOAD_PATH or sb://) to a real local file, streaming object-store
    # rows to a temp file that's cleaned up on exit. Entry/exit are driven
    # manually rather than a plain `async with` so a resolution failure
    # (missing file, bad sb:// key) maps to 404 without also catching
    # unrelated errors register_generated_media might raise later (e.g. its
    # own oversized-copy ValueError).
    loc_cm = materialize(args["file_path"])
    try:
        local_path = await loc_cm.__aenter__()
    except Exception as e:
        raise HTTPException(status_code=404, detail="Resource file missing") from e
    if not os.path.isfile(local_path):
        # materialize()'s filesystem branch doesn't check existence itself
        # (it only guards containment) — a row whose file was since deleted
        # would otherwise surface as a raw FileNotFoundError from
        # register_generated_media's copy step instead of a clean 404.
        await loc_cm.__aexit__(None, None, None)
        raise HTTPException(status_code=404, detail="Resource file missing")
    try:
        row = await register_generated_media(
            user_id=str(auth.user_id),
            scope_id=await _scope(auth),
            source_path=str(local_path),
            mime=args["mime"],
            origin=GenerationOrigin(kind="canvas_upload"),
        )
    finally:
        await loc_cm.__aexit__(None, None, None)
    return {
        "data": {
            "id": str(row["id"]),
            "url": f"/api/v1/generated-media/{row['id']}/"
            f"{_IMPORT_KIND_ENDPOINT.get(media_kind_from_mime(args['mime']), 'file')}",
            "media_kind": media_kind_from_mime(args["mime"]),
            "mime": args["mime"],
        }
    }


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
    # The service INSERTs a scoped model (Resources) — the scoped-ORM guard
    # requires an ambient Scope at the request boundary (2026-08-21 prod
    # UnscopedQueryError: promote 500ed for every upload).
    async with request_scope(Scope(user_id=str(auth.user_id))):
        try:
            resource = await PromoteGeneratedMediaService().promote(
                gen_id=gen_id,
                user_id=str(auth.user_id),
                target_scope_id=target_scope_id,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
    return {"data": {"promoted_resource_id": str(resource["id"])}}
