"""Generations library — read-only Tier-1 surfacing (sub-plan 5).

Routes (all under prefix /generated-media, registered in app/api/__init__.py):
  GET  /generated-media                → {data: {items, next_cursor}}
  POST /generated-media/import         → {data: {id, url, media_kind, mime}}
  GET  /generated-media/{id}           → {data: row}   (404 if not in scope)
  GET  /generated-media/{id}/cover     → Response      (image preview; ?full=1 = original, no auth — <img>)
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
from app.services.library.generated_roles import (
    REFERENCE,
    ROLE_KEY,
    UPSCALE_RESULT,
    normalize_role,
)
from app.services.library.generation_access import can_read_generation_scope
from app.services.library.media_preview import ensure_preview
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
    cover_source: Optional[str] = Query(None, max_length=32),
) -> dict:
    page = await GeneratedMediaRepository().list_for_scope(
        await _scope(auth),
        kind=kind,
        entity_kind=entity_kind,
        entity_id=entity_id,
        cursor=cursor,
        limit=limit,
        cover_source=cover_source,
    )
    return {"data": page}


@router.get("/{gen_id}/cover")
async def get_generation_cover(gen_id: int, full: int = 0):
    """Serve a generated image (no auth required).

    Default: the PREVIEW tier — 1024px longest edge, WebP, derived from the
    original by key convention and generated on first request. This is what a
    canvas node should paint; it used to be handed the full-size original
    (1.3 MB PNGs, ~75 MB of decoded bitmap on a 24-node board).

    ``?full=1``: the original bytes, for the lightbox and the editor.

    Both branches are world-readable-by-id (same posture as GET
    /resources/{id}/cover) — ``?full=1`` does not widen anything, it only makes
    explicit what /cover already served. Use these URLs in browser <img> tags,
    no Bearer header needed; GET /{gen_id}/file stays the auth-gated download.

    If the preview cannot be produced for any reason, the original is served
    instead — never an error.
    """
    row = await GeneratedMediaRepository().get_by_id(gen_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row.get("media_kind") != "image":
        raise HTTPException(status_code=404, detail="no cover")
    headers = {"Cache-Control": "public, max-age=604800, immutable"}
    if not full:
        preview = await ensure_preview(row)
        if preview is not None:
            return Response(content=preview, media_type="image/webp", headers=headers)
    return await _serve_media_row(row, headers=headers)


@router.get("/{gen_id}/stream")
async def get_generation_stream(gen_id: int, request: Request):
    """Serve a generated VIDEO or AUDIO row's bytes (no auth required).

    Same world-readable-by-id posture as ``/cover``: a bare ``<video src>``
    can't carry a Bearer header, and neither can a bare ``<audio src>`` — the
    inbox lightbox's audio player is exactly that. The snowflake id is
    unguessable. Timed media only — image rows 404 here (use ``/cover``);
    ``/file`` stays the auth-gated download. Both backends support Range:
    filesystem via FileResponse, object-store via a streamed range-passthrough
    (no full-file memory spike), and both serve the row's own ``mime``, so
    audio needed no separate serving path.
    """
    row = await GeneratedMediaRepository().get_by_id(gen_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    if row.get("media_kind") not in ("video", "audio"):
        raise HTTPException(status_code=404, detail="not streamable")
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
    role: Optional[str] = Form(None),
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

    # Rejected, not defaulted: a misspelled role that quietly became
    # ``user_upload`` would put a mask back in the inbox with nothing saying
    # the classification had failed.
    try:
        role_value = normalize_role(role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

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
                params={
                    "filename": file.filename or "",
                    ROLE_KEY: role_value,
                },
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


# Formats the image models will not take as references; converted to PNG at
# import time. Pillow in the production image reads AVIF (features.check('avif')
# is True there), and PNG is lossless so nothing is thrown away twice.
_TRANSCODE_TO_PNG = frozenset(
    {"image/avif", "image/heic", "image/heif", "image/tiff", "image/bmp"}
)


def _transcode_to_png(src_path: str) -> str:
    """Write ``src_path`` out as a PNG temp file and return its path."""
    from PIL import Image

    fd, out = tempfile.mkstemp(prefix="genmedia_ref_", suffix=".png")
    os.close(fd)
    with Image.open(src_path) as im:
        im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(
            out, format="PNG"
        )
    return out


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

    # ⚠️ resources is a scoped table: reading it outside a request_scope raises
    # UnscopedQueryError (2026-08-27: every template → reference import in Cover
    # Studio failed this way, surfacing only as "could not prepare"). The scope
    # is the caller's own user — the access check below is what authorises.
    async with request_scope(Scope(user_id=str(auth.user_id))):
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
    transcoded: Optional[str] = None
    try:
        source_path, mime = str(local_path), args["mime"]
        if mime in _TRANSCODE_TO_PNG:
            # Upstream image models accept jpeg/png/gif/webp only (codex's own
            # error text: "supported image formats: ['image/jpeg', 'image/png',
            # 'image/gif', 'image/webp']"). AVIF is what the library actually
            # holds — every sample in the first real cover folder was AVIF — so
            # convert here, at the one place a library asset becomes a
            # reference, rather than in each provider.
            transcoded = _transcode_to_png(source_path)
            source_path, mime = transcoded, "image/png"
        row = await register_generated_media(
            user_id=str(auth.user_id),
            scope_id=await _scope(auth),
            source_path=source_path,
            mime=mime,
            # A library asset minted into a durable URL so the i2i bridge
            # can fetch it. The user already owns this image in My Uploads;
            # surfacing the copy in the inbox as something to triage is what
            # this role stops.
            origin=GenerationOrigin(kind="canvas_upload", params={ROLE_KEY: REFERENCE}),
        )
    finally:
        await loc_cm.__aexit__(None, None, None)
        if transcoded and os.path.exists(transcoded):
            try:
                os.unlink(transcoded)
            except OSError:
                pass
    return {
        "data": {
            "id": str(row["id"]),
            "url": f"/api/v1/generated-media/{row['id']}/"
            f"{_IMPORT_KIND_ENDPOINT.get(media_kind_from_mime(mime), 'file')}",
            "media_kind": media_kind_from_mime(mime),
            "mime": mime,
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


def _membership():
    """Seam: the team-membership lookup (patched in tests)."""
    from app.repositories.conversation_repository import get_conversation_repository

    return get_conversation_repository()


def _upscale_provider():
    """Seam: the jimeng CLI provider (patched in tests)."""
    from app.services.media.parsers.video_providers.jimeng_cli import (
        JimengCliProvider,
    )

    return JimengCliProvider()


def _materialize_gen_file(gen_id: int):
    """Seam: async-context yielding a local Path for the generation file."""
    from app.services.library.generated_media_service import (
        generated_media_local_path,
    )

    return generated_media_local_path(
        f"/api/v1/generated-media/{gen_id}/file", media_kind="image"
    )


async def _register_upscale_result(**kwargs):
    from app.services.library.generated_media_service import (
        GenerationOrigin,
        register_generated_media,
    )

    origin = kwargs.pop("origin_params")
    # An upscale is something the user asked for, so it stays VISIBLE — the
    # role is stamped to say which of the three canvas_upload writers made the
    # row, not to hide it (see ``generated_roles.INTERMEDIATE_ROLES``).
    return await register_generated_media(
        origin=GenerationOrigin(
            kind="canvas_upload", params={**(origin or {}), ROLE_KEY: UPSCALE_RESULT}
        ),
        **kwargs,
    )


class UpscaleRequest(BaseModel):
    resolution: str = "2k"


@router.post("/{gen_id}/upscale")
async def upscale_generation(
    gen_id: int, payload: UpscaleRequest, auth: AuthDep
) -> dict:
    """IC 放大: run jimeng ``image_upscale`` on this generation and register
    the result as a NEW generated-media row (the source stays)."""
    # Read gate on the SOURCE, then file the result where the source lives —
    # the caller's personal team is the wrong home for a team board's image
    # (the promote fix in #2212 settled the same question).
    source = await GeneratedMediaRepository().get_by_id(gen_id)
    if source is None or not await can_read_generation_scope(
        source,
        user_id=str(auth.user_id),
        personal_team_id=await _scope(auth),
        membership=_membership(),
    ):
        raise HTTPException(status_code=404, detail="generation not found")
    scope_id = int(source["scope_id"])
    async with request_scope(Scope(user_id=str(auth.user_id))):
        async with _materialize_gen_file(gen_id) as src:
            if src is None:
                raise HTTPException(status_code=404, detail="generation file missing")
            try:
                result = await _upscale_provider().upscale_image(
                    image_path=str(src), resolution=payload.resolution
                )
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"upscale failed: {exc}")
        row = await _register_upscale_result(
            user_id=str(auth.user_id),
            scope_id=scope_id,
            source_path=result.local_path,
            mime=getattr(result, "mime", "image/png"),
            origin_params={
                "upscaled_from": str(gen_id),
                "resolution": payload.resolution,
            },
        )
        new_id = row.get("id")
        if new_id is None:
            raise HTTPException(status_code=500, detail="upscale registration failed")
    return {
        "data": {"id": str(new_id), "url": f"/api/v1/generated-media/{new_id}/file"}
    }


@router.post("/{gen_id}/promote")
async def promote_generation(gen_id: int, auth: AuthDep) -> dict:
    """Promote a generation into a resource, in the scope it already lives in.

    Deliberately does NOT pass ``_scope(auth)``. That helper resolves the
    CALLER'S personal team, which is right for this router's other endpoints
    (they browse a personal inbox) and wrong here: promoting is a copy with a
    destination, and pinning the destination to the caller's private library
    tore a team board's generation out of the team the moment any member
    opened an editor on it. The service defaults to the generation's own
    scope and still gates the write on membership — see its docstring, and
    `_registration_scope_id` in canvas_generation.py for the same argument
    settled on the registration side.
    """
    # The service INSERTs a scoped model (Resources) — the scoped-ORM guard
    # requires an ambient Scope at the request boundary (2026-08-21 prod
    # UnscopedQueryError: promote 500ed for every upload).
    async with request_scope(Scope(user_id=str(auth.user_id))):
        try:
            resource = await PromoteGeneratedMediaService().promote(
                gen_id=gen_id,
                user_id=str(auth.user_id),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
    return {"data": {"promoted_resource_id": str(resource["id"])}}
