"""Media storage abstraction — filesystem vs Supabase Storage object store.

Phase 1a foundation for the small-image object-store migration (plan:
docs/superpowers/plans/2026-07-05-chat-images-object-storage.md).

`generated_media.file_path` is the single source of location truth. Two shapes
coexist (dual-track, zero migration):

  legacy filesystem : "teams/42/chat/2026/07/05/{uuid}/shot.png"
  object store      : "sb://chat-media/t42/ab/cd/{sha256}.png"

`resolve_media_source` is the ONLY place that interprets the column — every
reader routes through it, so the scheme split never leaks into scattered
`startswith("sb://")` checks. `ObjectStore` isolates all Supabase-storage
calls behind put/get/signed-url/exists.

This module is INERT until FEATURE_CHAT_MEDIA_OBJECT_STORE flips on: nothing
writes `sb://` paths yet (Phase 1b), so `resolve_media_source` only ever sees
legacy rows and returns local locations identical to today's behavior.
"""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from typing import Optional

_SB_SCHEME = "sb://"


# ── Location value object ───────────────────────────────────────────────────


@dataclass(frozen=True)
class MediaLocation:
    """Where a media file lives. Exactly one of the two shapes is populated."""

    backend: str  # "filesystem" | "object_store"
    # filesystem: relative path under settings.DOWNLOAD_PATH
    rel_path: Optional[str] = None
    # object_store:
    bucket: Optional[str] = None
    key: Optional[str] = None

    @property
    def is_object_store(self) -> bool:
        return self.backend == "object_store"


def resolve_media_source(file_path: str) -> MediaLocation:
    """Parse a ``generated_media.file_path`` value into a MediaLocation.

    ``sb://<bucket>/<key...>`` → object store; anything else → filesystem
    relative path (the historical shape). Never raises on a malformed
    ``sb://`` value — a scheme with no bucket/key degrades to filesystem so a
    corrupt row can't 500 a reader (it just 404s at the file layer instead).
    """
    if file_path.startswith(_SB_SCHEME):
        rest = file_path[len(_SB_SCHEME) :]
        bucket, _, key = rest.partition("/")
        if bucket and key:
            return MediaLocation(backend="object_store", bucket=bucket, key=key)
        # Malformed sb:// → treat as filesystem (will 404, not crash).
    return MediaLocation(backend="filesystem", rel_path=file_path)


# ── Content-addressed key derivation ────────────────────────────────────────


def _ext_for(mime: str, filename: Optional[str]) -> str:
    """Pick a file extension: prefer the original filename's, else sniff mime."""
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        # keep it sane (letters/digits, ≤5 chars) so a weird name can't inject
        if ext.isalnum() and len(ext) <= 5:
            return f".{ext}"
    guessed = mimetypes.guess_extension((mime or "").split(";")[0].strip() or "")
    return guessed or ".bin"


def _object_key(scope_id: int, sha: str, ext: str) -> str:
    return f"t{scope_id}/{sha[:2]}/{sha[2:4]}/{sha}{ext}"


def content_key(
    *, scope_id: int, data: bytes, mime: str, filename: Optional[str] = None
) -> tuple[str, str]:
    """Return (sha256_hex, object_key) for an in-memory blob.

    Key = ``t{scope}/{sha[:2]}/{sha[2:4]}/{sha}{ext}`` — content-addressed, so
    the same bytes always produce the same key (dedup), the hash prefix
    fans out the flat key space, and the original filename never enters the
    key (privacy / injection / collision).
    """
    sha = hashlib.sha256(data).hexdigest()
    return sha, _object_key(scope_id, sha, _ext_for(mime, filename))


def content_key_from_sha(
    *, scope_id: int, sha: str, mime: str, filename: Optional[str] = None
) -> str:
    """Same key scheme as ``content_key`` but from a precomputed sha — for
    large blobs (video) hashed by streaming a file instead of buffering bytes."""
    return _object_key(scope_id, sha, _ext_for(mime, filename))


def to_file_path(bucket: str, key: str) -> str:
    """Compose the ``sb://`` value stored in generated_media.file_path."""
    return f"{_SB_SCHEME}{bucket}/{key}"


# ── Object store wrapper (Supabase Storage) ─────────────────────────────────


# Hard cap on every storage-api call. When storage is sick (2026-07-06: its
# deleted data dir made every upload hang until kong's 60s upstream timeout),
# an uncapped call turns "storage degraded" into "uploads hang a minute and
# the browser reports Failed to fetch". 15s → the filesystem fallback kicks
# in fast and the user barely notices.
_STORAGE_CALL_TIMEOUT_S = 15.0


class ObjectStore:
    """Thin async wrapper over the Supabase service-role storage client.

    All Supabase-storage SDK calls live here so callers deal in bytes/keys and
    mocked tests pin OUR logic, not the SDK. The backend (file vs s3) is a
    storage-api server config detail and is fully transparent to this layer —
    we only ever PUT/GET objects by (bucket, key). Every call is capped at
    ``_STORAGE_CALL_TIMEOUT_S`` — see the constant's comment.
    """

    def __init__(self, bucket: str) -> None:
        self._bucket = bucket

    async def _proxy(self):
        # Imported lazily so this module has no import-time Supabase dependency
        # (keeps it importable in pure-logic unit tests).
        from app.db.supabase_client import get_async_supabase_admin

        client = await get_async_supabase_admin()
        return client.storage.from_(self._bucket)

    @staticmethod
    async def _capped(awaitable):
        import asyncio

        return await asyncio.wait_for(awaitable, timeout=_STORAGE_CALL_TIMEOUT_S)

    async def exists(self, key: str) -> bool:
        """True if an object already lives at ``key`` (dedup skip-PUT check)."""
        proxy = await self._proxy()
        try:
            await self._capped(proxy.download(key))
            return True
        except Exception:
            return False

    async def put_bytes(
        self, key: str, data: bytes, mime: str, *, upsert: bool = True
    ) -> None:
        proxy = await self._proxy()
        await self._capped(
            proxy.upload(
                key,
                data,
                {
                    "content-type": mime or "application/octet-stream",
                    "upsert": "true" if upsert else "false",
                },
            )
        )

    async def put_file(
        self, key: str, file_path: str, mime: str, *, upsert: bool = True
    ) -> None:
        """Upload from a local file (streamed by the SDK) — for blobs too large
        to buffer in memory (generated video). Larger cap: 4× the standard one
        (a multi-hundred-MB video legitimately takes longer than 15s)."""
        import asyncio
        from pathlib import Path

        proxy = await self._proxy()
        await asyncio.wait_for(
            proxy.upload(
                key,
                Path(file_path),
                {
                    "content-type": mime or "application/octet-stream",
                    "upsert": "true" if upsert else "false",
                },
            ),
            timeout=_STORAGE_CALL_TIMEOUT_S * 4,
        )

    async def get_bytes(self, key: str) -> bytes:
        proxy = await self._proxy()
        return await self._capped(proxy.download(key))

    async def remove(self, key: str) -> None:
        proxy = await self._proxy()
        await self._capped(proxy.remove([key]))

    async def signed_url(self, key: str, *, ttl_seconds: int = 300) -> str:
        """Short-TTL signed URL for a private object (default 5 min)."""
        proxy = await self._proxy()
        resp = await self._capped(proxy.create_signed_url(key, ttl_seconds))
        # storage3 returns both signedURL / signedUrl keys across versions.
        url = resp.get("signedURL") or resp.get("signedUrl")
        if not url:
            raise RuntimeError(f"signed url missing in response for key={key!r}")
        return url


# Canonical bucket name for chat + AI-generated small images.
CHAT_MEDIA_BUCKET = "chat-media"


def chat_media_store() -> ObjectStore:
    return ObjectStore(CHAT_MEDIA_BUCKET)
