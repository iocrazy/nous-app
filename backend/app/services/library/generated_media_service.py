"""K — register AI-generated media into the Tier-1 generated_media store."""

from __future__ import annotations

import json
import mimetypes
import os
import re
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
    destined for the object store — videos keep the streamed-to-disk path."""
    chunks: list[bytes] = []
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        async with client.stream("GET", source_url) as resp:
            resp.raise_for_status()
            async for chunk in cap_aiter(resp.aiter_bytes(), max_bytes):
                chunks.append(chunk)
    return b"".join(chunks)


# Generated images destined for the object store are small; cap the in-memory
# buffer well below the 512 MiB filesystem ceiling. Over-cap → filesystem.
_OBJECT_STORE_IMAGE_MAX_BYTES = 16 * 1024 * 1024


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
    source_url: str,
    mime: str,
    origin: GenerationOrigin,
) -> dict:
    """Download a generated media URL into Tier-1 and insert one row. Returns it.

    Object-store path (flag on + image + within the in-memory cap): the blob is
    buffered and content-addressed into the chat-media bucket. Any failure —
    storage error OR over-cap — falls back to the streamed-to-disk filesystem
    path, so a generation never fails to persist. Videos always stream to disk.
    """
    kind = media_kind_from_mime(mime)
    file_path: Optional[str] = None
    size: int = 0
    content_sha256: Optional[str] = None

    if settings.FEATURE_CHAT_MEDIA_OBJECT_STORE and kind == "image":
        try:
            data = await _download_to_bytes(
                source_url, max_bytes=_OBJECT_STORE_IMAGE_MAX_BYTES
            )
            sha, key = content_key(scope_id=scope_id, data=data, mime=mime)
            store = chat_media_store()
            if not await store.exists(key):
                await store.put_bytes(key, data, mime)
            file_path = to_file_path(CHAT_MEDIA_BUCKET, key)
            size = len(data)
            content_sha256 = sha
        except Exception as exc:
            logger.warning(
                f"[register_generated_media] object-store write failed, "
                f"falling back to filesystem: scope={scope_id} error={exc!r}"
            )
            file_path = None  # fall through

    if file_path is None:
        gen_uuid = _uuid.uuid4().hex
        rel = (
            f"teams/{scope_id}/generations/{_date_bucket()}/"
            f"{gen_uuid}/media{ext_for(mime, kind)}"
        )
        dest = f"{settings.DOWNLOAD_PATH}/{rel}"
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
