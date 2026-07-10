"""K — register AI-generated media into the Tier-1 generated_media store."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import re
import tempfile
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiofiles
import httpx
from loguru import logger

from app.boundary import cap_aiter
from app.core.config import settings
from app.db import engine as db_engine
from app.services.library.media_storage import (
    CHAT_MEDIA_BUCKET,
    chat_media_store,
    content_key,
    content_key_from_sha,
    resolve_media_source,
    to_file_path,
)

_DEFAULT_MAX_BYTES = 512 * 1024 * 1024  # 512 MiB ceiling per generation


def _date_bucket() -> str:
    """UTC yyyy/mm/dd path segment for new media writes.

    Without it every upload/generation lands a new uuid dir in one flat
    parent (teams/{scope}/chat/, …/generations/) whose entry count grows
    unbounded — large directories slow listing and lookups on the NAS.
    Read paths are unaffected: consumers resolve files via the
    generated_media.file_path column, so pre-bucket rows keep working.
    """
    return datetime.now(timezone.utc).strftime("%Y/%m/%d")


def media_kind_from_mime(mime: str) -> str:
    m = (mime or "").lower()
    if m.startswith("video/"):
        return "video"
    return "image"  # default for images / unknown


def ext_for(mime: str, kind: str) -> str:
    guessed = mimetypes.guess_extension((mime or "").split(";")[0].strip() or "")
    if guessed:
        return guessed
    return ".mp4" if kind == "video" else ".png"


async def _download_to(
    dest_path: str,
    source_url: str,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> int:
    """Stream source_url → dest_path, byte-capped + atomic (.part → os.replace)."""
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    written = 0
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            async with client.stream("GET", source_url) as resp:
                resp.raise_for_status()
                async with aiofiles.open(part, "wb") as fp:
                    async for chunk in cap_aiter(resp.aiter_bytes(), max_bytes):
                        await fp.write(chunk)
                        written += len(chunk)
        os.replace(part, dest)
        return written
    except BaseException:
        Path(part).unlink(missing_ok=True)
        logger.opt(exception=True).warning("[genmedia] download failed: {}", source_url)
        raise


async def _download_to_bytes(source_url: str, *, max_bytes: int) -> bytes:
    """Stream source_url into memory, byte-capped. For small (image) blobs
    destined for the object store — video streams to a temp file instead."""
    chunks: list[bytes] = []
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        async with client.stream("GET", source_url) as resp:
            resp.raise_for_status()
            async for chunk in cap_aiter(resp.aiter_bytes(), max_bytes):
                chunks.append(chunk)
    return b"".join(chunks)


async def _copy_local_to(
    dest_path: str,
    source_path: str,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> int:
    """Copy a LOCAL source file → dest_path, byte-capped + atomic (.part → replace).

    The local-file counterpart of ``_download_to`` for provider outputs that are
    already on disk (dreamina/jimeng-cli writes to a temp dir; no URL to fetch)."""
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    written = 0
    try:
        async with (
            aiofiles.open(source_path, "rb") as src,
            aiofiles.open(part, "wb") as fp,
        ):
            while True:
                chunk = await src.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError(
                        f"source file exceeds max_bytes ({max_bytes}): {source_path}"
                    )
                await fp.write(chunk)
        os.replace(part, dest)
        return written
    except BaseException:
        Path(part).unlink(missing_ok=True)
        logger.opt(exception=True).warning(
            "[genmedia] local copy failed: {}", source_path
        )
        raise


def _read_file_capped(path: str, max_bytes: int) -> bytes:
    """Read a local file fully, raising if it exceeds ``max_bytes`` (image path)."""
    size = os.path.getsize(path)
    if size > max_bytes:
        raise ValueError(f"source file exceeds max_bytes ({max_bytes}): {path}")
    with open(path, "rb") as fp:
        return fp.read()


def _sha256_file(path: str) -> str:
    """Stream-hash a file (no full read into memory) — for video blobs."""
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# Object-store caps: generated images buffer in memory (small); generated
# short videos stream via a temp file (no memory blowup). Over-cap → filesystem.
_OBJECT_STORE_IMAGE_MAX_BYTES = 16 * 1024 * 1024
_OBJECT_STORE_VIDEO_MAX_BYTES = 256 * 1024 * 1024


async def _write_generation_to_object_store(
    *, scope_id: int, source_url: str, mime: str, kind: str
) -> tuple[str, int, str]:
    """Content-address a generated image/video into the chat-media bucket.

    Returns (sb:// file_path, size_bytes, sha256). Images buffer in memory;
    videos stream to a temp file then upload from disk (avoids memory blowup
    and the small in-memory cap). Dedup: an already-present key skips the
    upload. Raises on any failure so the caller falls back to the filesystem.
    """
    store = chat_media_store()
    if kind == "video":
        fd, tmp = tempfile.mkstemp(suffix=ext_for(mime, kind))
        os.close(fd)
        try:
            size = await _download_to(
                tmp, source_url, max_bytes=_OBJECT_STORE_VIDEO_MAX_BYTES
            )
            sha = await asyncio.to_thread(_sha256_file, tmp)
            key = content_key_from_sha(scope_id=scope_id, sha=sha, mime=mime)
            if not await store.exists(key):
                await store.put_file(key, tmp, mime)
        finally:
            Path(tmp).unlink(missing_ok=True)
    else:
        data = await _download_to_bytes(
            source_url, max_bytes=_OBJECT_STORE_IMAGE_MAX_BYTES
        )
        sha, key = content_key(scope_id=scope_id, data=data, mime=mime)
        size = len(data)
        if not await store.exists(key):
            await store.put_bytes(key, data, mime)
    return to_file_path(CHAT_MEDIA_BUCKET, key), size, sha


async def _write_local_generation_to_object_store(
    *, scope_id: int, source_path: str, mime: str, kind: str
) -> tuple[str, int, str]:
    """Content-address a LOCAL generated file into the chat-media bucket.

    The local-file counterpart of ``_write_generation_to_object_store`` — reads
    the file instead of downloading a URL (video: stream-hash + put_file from
    disk; image: read bytes + put_bytes). Same dedup + return contract."""
    store = chat_media_store()
    if kind == "video":
        sha = await asyncio.to_thread(_sha256_file, source_path)
        key = content_key_from_sha(scope_id=scope_id, sha=sha, mime=mime)
        size = os.path.getsize(source_path)
        if not await store.exists(key):
            await store.put_file(key, source_path, mime)
    else:
        data = await asyncio.to_thread(
            _read_file_capped, source_path, _OBJECT_STORE_IMAGE_MAX_BYTES
        )
        sha, key = content_key(scope_id=scope_id, data=data, mime=mime)
        size = len(data)
        if not await store.exists(key):
            await store.put_bytes(key, data, mime)
    return to_file_path(CHAT_MEDIA_BUCKET, key), size, sha


@dataclass
class GenerationOrigin:
    kind: str  # 'agent_run' | 'canvas_run' | 'chat_upload'
    run_id: Optional[str] = None
    agent_id: Optional[str] = None
    canvas_id: Optional[int] = None
    node_id: Optional[str] = None
    prompt: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    params: dict[str, Any] = field(default_factory=dict)
    cost_cents: Optional[float] = None
    parent_resource_id: Optional[int] = None
    derivation_kind: Optional[str] = None
    conversation_id: Optional[int] = None


async def register_generated_media(
    *,
    user_id: str,
    scope_id: int,
    source_url: Optional[str] = None,
    source_path: Optional[str] = None,
    mime: str,
    origin: GenerationOrigin,
) -> dict:
    """Ingest a generated media blob into Tier-1 and insert one row. Returns it.

    Exactly one of ``source_url`` (fetch a URL) or ``source_path`` (a local file
    a subprocess provider already wrote, e.g. dreamina/jimeng-cli) must be given.

    Object-store path (flag on + generated image/video): the blob is
    content-addressed into the chat-media bucket — images buffer in memory,
    short videos stream via a temp file (URL) or read from disk (local). Any
    failure (storage error OR over-cap) falls back to the filesystem path, so a
    generation never fails to persist. NOTE: this covers only AI *generations*
    (Tier-1 generated_media) — the media library's downloaded/uploaded videos
    live on their own path and stay on the filesystem+nginx.
    """
    if bool(source_url) == bool(source_path):
        raise ValueError(
            "register_generated_media requires exactly one of source_url / source_path"
        )
    kind = media_kind_from_mime(mime)
    file_path: Optional[str] = None
    size: int = 0
    content_sha256: Optional[str] = None

    if settings.FEATURE_CHAT_MEDIA_OBJECT_STORE and kind in ("image", "video"):
        try:
            if source_path is not None:
                file_path, size, content_sha256 = (
                    await _write_local_generation_to_object_store(
                        scope_id=scope_id, source_path=source_path, mime=mime, kind=kind
                    )
                )
            else:
                file_path, size, content_sha256 = (
                    await _write_generation_to_object_store(
                        scope_id=scope_id, source_url=source_url, mime=mime, kind=kind
                    )
                )
        except Exception as exc:
            logger.warning(
                f"[register_generated_media] object-store write failed, "
                f"falling back to filesystem: scope={scope_id} "
                f"kind={kind} error={exc!r}"
            )
            file_path = None  # fall through

    if file_path is None:
        gen_uuid = _uuid.uuid4().hex
        rel = (
            f"teams/{scope_id}/generations/{_date_bucket()}/"
            f"{gen_uuid}/media{ext_for(mime, kind)}"
        )
        dest = f"{settings.DOWNLOAD_PATH}/{rel}"
        if source_path is not None:
            size = await _copy_local_to(dest, source_path)
        else:
            size = await _download_to(dest, source_url)
        file_path = rel

    row = await db_engine.execute_returning_one(
        "INSERT INTO public.generated_media "
        "(scope_id, creator_id, media_kind, mime, file_path, file_size_bytes, "
        " origin_kind, origin_run_id, agent_id, canvas_id, node_id, prompt, model, "
        " provider, params, cost_cents, parent_resource_id, derivation_kind, "
        " conversation_id, content_sha256) "
        "VALUES (:scope_id, :creator_id, :media_kind, :mime, :file_path, :file_size_bytes, "
        " :origin_kind, :origin_run_id, :agent_id, :canvas_id, :node_id, :prompt, :model, "
        " :provider, CAST(:params AS jsonb), :cost_cents, :parent_resource_id, :derivation_kind,"
        " :conversation_id, :content_sha256) "
        "RETURNING *",
        {
            "scope_id": scope_id,
            "creator_id": user_id,
            "media_kind": kind,
            "mime": mime,
            "file_path": file_path,
            "file_size_bytes": size,
            "content_sha256": content_sha256,
            "origin_kind": origin.kind,
            "origin_run_id": origin.run_id,
            "agent_id": origin.agent_id,
            "canvas_id": origin.canvas_id,
            "node_id": origin.node_id,
            "prompt": origin.prompt,
            "model": origin.model,
            "provider": origin.provider,
            "params": json.dumps(origin.params or {}),
            "cost_cents": origin.cost_cents,
            "parent_resource_id": origin.parent_resource_id,
            "derivation_kind": origin.derivation_kind,
            "conversation_id": origin.conversation_id,
        },
    )
    return row or {}


def _safe_filename(name: str) -> str:
    name = os.path.basename(name or "")
    name = re.sub(r"[^\w.\-]", "_", name)
    return name or "attachment"


async def _insert_uploaded_row(
    *,
    user_id: str,
    scope_id: int,
    kind: str,
    mime: str,
    file_path: str,
    file_size_bytes: int,
    origin: GenerationOrigin,
    content_sha256: Optional[str] = None,
) -> dict:
    """INSERT one generated_media row for an uploaded blob (path-agnostic)."""
    row = await db_engine.execute_returning_one(
        "INSERT INTO public.generated_media "
        "(scope_id, creator_id, media_kind, mime, file_path, file_size_bytes, "
        " origin_kind, conversation_id, content_sha256) "
        "VALUES (:scope_id, :creator_id, :media_kind, :mime, :file_path, "
        " :file_size_bytes, :origin_kind, :conversation_id, :content_sha256) "
        "RETURNING *",
        {
            "scope_id": scope_id,
            "creator_id": user_id,
            "media_kind": kind,
            "mime": mime,
            "file_path": file_path,
            "file_size_bytes": file_size_bytes,
            "origin_kind": origin.kind,
            "conversation_id": origin.conversation_id,
            "content_sha256": content_sha256,
        },
    )
    return row or {}


async def _register_uploaded_to_object_store(
    *,
    user_id: str,
    scope_id: int,
    file_bytes: bytes,
    filename: str,
    mime: str,
    kind: str,
    origin: GenerationOrigin,
) -> dict:
    """Upload bytes to the chat-media bucket (content-addressed) + insert row.

    Dedup: identical bytes hash to the same key, so a re-upload skips the PUT
    (the row still inserts, pointing at the shared object). Raises on any
    storage failure so the caller can fall back to the filesystem.
    """
    sha, key = content_key(
        scope_id=scope_id, data=file_bytes, mime=mime, filename=filename
    )
    store = chat_media_store()
    if not await store.exists(key):
        await store.put_bytes(key, file_bytes, mime)
    return await _insert_uploaded_row(
        user_id=user_id,
        scope_id=scope_id,
        kind=kind,
        mime=mime,
        file_path=to_file_path(CHAT_MEDIA_BUCKET, key),
        file_size_bytes=len(file_bytes),
        origin=origin,
        content_sha256=sha,
    )


async def register_uploaded_media(
    *,
    user_id: str,
    scope_id: int,
    file_bytes: bytes,
    filename: str,
    mime: str,
    origin: GenerationOrigin,
    subdir: str = "chat",
) -> dict:
    """Write uploaded bytes into the staged store and insert one row. Returns it.

    Object-store path (flag on + image): content-addressed upload to the
    chat-media bucket. Falls back to the filesystem on ANY storage error so an
    upload never hard-fails because storage-api is down. Videos and non-image
    uploads always stay on the filesystem (object store is for small images).
    """
    kind = media_kind_from_mime(mime)
    if settings.FEATURE_CHAT_MEDIA_OBJECT_STORE and kind == "image":
        try:
            return await _register_uploaded_to_object_store(
                user_id=user_id,
                scope_id=scope_id,
                file_bytes=file_bytes,
                filename=filename,
                mime=mime,
                kind=kind,
                origin=origin,
            )
        except Exception as exc:
            logger.warning(
                f"[register_uploaded_media] object-store upload failed, "
                f"falling back to filesystem: scope={scope_id} error={exc!r}"
            )
    gen_uuid = _uuid.uuid4().hex
    rel = f"teams/{scope_id}/{subdir}/{_date_bucket()}/{gen_uuid}/{_safe_filename(filename)}"
    dest = f"{settings.DOWNLOAD_PATH}/{rel}"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    try:
        with open(part, "wb") as fp:
            fp.write(file_bytes)
        os.replace(part, dest)
    except BaseException:
        Path(part).unlink(missing_ok=True)
        raise
    return await _insert_uploaded_row(
        user_id=user_id,
        scope_id=scope_id,
        kind=kind,
        mime=mime,
        file_path=rel,
        file_size_bytes=len(file_bytes),
        origin=origin,
    )


__all__ = [
    "GenerationOrigin",
    "media_kind_from_mime",
    "ext_for",
    "_download_to",
    "register_generated_media",
    "register_uploaded_media",
    "_safe_filename",
]


# ---------------------------------------------------------------------------
# Durable-URL → local-file bridge (shared by shot i2v + canvas video ops)
# ---------------------------------------------------------------------------

# Matches the same-origin serving endpoints (/cover image, /stream video,
# /file auth-gated) so an already-generated asset can seed a new generation
# (e.g. image2video) from its real file instead of a re-download.
GENERATED_MEDIA_URL_RE = re.compile(r"/generated-media/(\d+)/(?:cover|stream|file)$")


async def resolve_generated_media_local_path(
    url: str, *, media_kind: str = "image"
) -> Optional[str]:
    """Bridge a durable generated-media URL back to its local file path.

    Returns the real filesystem path only when the URL matches the serving
    pattern AND the row is of ``media_kind`` AND filesystem-backed AND the
    file exists inside DOWNLOAD_PATH (containment guard — a corrupt/hostile
    file_path must never escape). Object-store rows, foreign URLs, or any
    miss → None; callers fall back (e.g. image2video → text2video).
    """
    match = GENERATED_MEDIA_URL_RE.search(str(url or ""))
    if not match:
        return None
    gen_id = int(match.group(1))

    from app.repositories.generated_media_repository import GeneratedMediaRepository

    row = await GeneratedMediaRepository().get_by_id(gen_id)
    if not row or row.get("media_kind") != media_kind:
        return None

    loc = resolve_media_source(row["file_path"])
    if loc.is_object_store:
        return None
    base = os.path.realpath(settings.DOWNLOAD_PATH)
    real = os.path.realpath(os.path.join(settings.DOWNLOAD_PATH, loc.rel_path or ""))
    if not (real == base or real.startswith(base + os.sep)):
        return None
    return real if os.path.isfile(real) else None
