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
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator, Callable, Optional

from app.services.library.media_keys import MediaKeyBuilder

if TYPE_CHECKING:
    # Only for the `materialize` forward-ref annotation below — pathlib is
    # stdlib (zero external deps), so this doesn't reintroduce the heavy
    # import-time dependency this module otherwise avoids.
    from pathlib import Path

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

    @property
    def is_prefix(self) -> bool:
        """key 以 / 结尾 = 前缀形态(一个资源对应该前缀下的多个对象,如图集)。"""
        return self.is_object_store and bool(self.key) and self.key.endswith("/")


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


# 键构造已抽到 MediaKeyBuilder(media_keys.py);以下均为薄转发,签名与原实现
# 逐字保持一致(含关键字专用 `*`),避免 12 个既有调用方受影响。
_KEYS = MediaKeyBuilder()


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
    return sha, _KEYS.content_key_from_sha(scope_id, sha, mime, filename)


def content_key_from_sha(
    *, scope_id: int, sha: str, mime: str, filename: Optional[str] = None
) -> str:
    """Same key scheme as ``content_key`` but from a precomputed sha — for
    large blobs (video) hashed by streaming a file instead of buffering bytes."""
    return _KEYS.content_key_from_sha(scope_id, sha, mime, filename)


def to_file_path(bucket: str, key: str) -> str:
    """Compose the ``sb://`` value stored in generated_media.file_path."""
    return _KEYS.to_file_path(bucket, key)


def hls_key_prefix(resource_id: str, version_id: str) -> str:
    """Key prefix owning one version's HLS output: ``hls/{rid}/{vid}``."""
    return _KEYS.hls_prefix(resource_id, version_id)


def album_key_prefix(scope_id: int, rid) -> str:
    """Prefix owning an album's flattened objects: ``t{scope}/album/{rid}/``.

    ``rid`` is caller-defined identity, not necessarily ``resources.id`` — see
    ``MediaKeyBuilder.album_prefix`` for why the ``downloads`` module passes a
    ``resource_versions.id`` here instead.
    """
    return _KEYS.album_prefix(scope_id, rid)


def derived_key_prefix(resource_id) -> str:
    """Prefix owning one resource's derived assets: ``derived/{rid}/``."""
    return _KEYS.derived_prefix(resource_id)


def hls_key(resource_id: str, version_id: str, rel_path: str) -> str:
    """Full key for one HLS artefact, e.g. ``hls/{rid}/{vid}/480p/stream.m3u8``.

    ``rel_path`` is the artefact's path relative to the playlist root — exactly
    what the m3u8 references — so relative links keep resolving once the tree
    lives in the object store.
    """
    return _KEYS.hls_key(resource_id, version_id, rel_path)


# ── Object store wrapper (Supabase Storage) ─────────────────────────────────


# Hard cap on every storage-api call. When storage is sick (2026-07-06: its
# deleted data dir made every upload hang until kong's 60s upstream timeout),
# an uncapped call turns "storage degraded" into "uploads hang a minute and
# the browser reports Failed to fetch". 15s → the filesystem fallback kicks
# in fast and the user barely notices.
_STORAGE_CALL_TIMEOUT_S = 15.0

# Chunk size for streamed GETs (get_stream). 64 KiB balances syscall overhead
# against per-chunk memory — a ranged video read never buffers the whole file.
_STREAM_CHUNK_BYTES = 64 * 1024

# Directory upload (put_dir) fan-out. An HLS version is ~300 segments and a
# full backfill is ~31k: serial would take hours, unbounded would exhaust the
# connection pool. 8 keeps the NAS-hosted store busy without starving the rest
# of the app of the shared async client.
_DIR_UPLOAD_CONCURRENCY = 8

# Objects per storage-api list page. The API caps what it returns; paginating
# explicitly means a tier with hundreds of segments is fully enumerated.
_LIST_PAGE = 100

# Keys per remove() call — the request body is a JSON list, so cap it rather
# than posting an unbounded array.
_REMOVE_BATCH = 100


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

    @property
    def bucket(self) -> str:
        return self._bucket

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
        """True if an object already lives at ``key`` (dedup skip-PUT check).

        Probes with the HEAD-shaped ``get_size`` rather than ``download``: the
        old implementation pulled the ENTIRE object just to learn whether it
        was there, so a skip-PUT check on a 2 MB HLS segment cost 2 MB of
        transfer, and an HLS migration pass (31k segments, ~58 GB) would have
        moved that volume twice.
        """
        try:
            await self.get_size(key)  # raises on 404 / missing content-length
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

    def _object_target(self, proxy, key: str) -> tuple[str, dict]:
        """Build the (absolute URL, auth headers) for a raw object GET/HEAD.

        Reaches under the storage3 proxy to hit the object endpoint directly
        (``.../object/{bucket}/{key}``) with httpx — the SDK's ``download`` only
        returns fully-buffered bytes and can't carry a ``Range`` header, so
        streaming and ranged reads have to go one level down. Key parts are
        content-addressed (alnum/dot only), so a plain ``/`` split is safe.
        """
        url = str(proxy._base_url.joinpath("object", self._bucket, *key.split("/")))
        return url, dict(proxy._headers)

    async def get_size(self, key: str) -> int:
        """Object size in bytes via a HEAD to storage-api (Content-Length).

        Used by ranged serving to validate the requested range and to fill the
        ``Content-Range`` total without buffering the object. Raises on a
        missing object (HEAD 404 → raise_for_status) or an absent header.
        """
        proxy = await self._proxy()
        url, headers = self._object_target(proxy, key)
        resp = await self._capped(proxy._client.head(url, headers=headers))
        resp.raise_for_status()
        length = resp.headers.get("content-length")
        if length is None:
            raise RuntimeError(f"no content-length for key={key!r}")
        return int(length)

    async def get_stream(
        self,
        key: str,
        *,
        start: Optional[int] = None,
        end: Optional[int] = None,
        chunk_size: int = _STREAM_CHUNK_BYTES,
    ) -> AsyncIterator[bytes]:
        """Async-iterate an object's bytes, optionally a ``[start, end]`` slice.

        When ``start`` is given a ``Range: bytes=start-end`` header is passed
        straight through to storage-api, so only the requested slice crosses the
        wire — no full-file buffering (the whole point of this path for video).
        ``end=None`` means "to the last byte". The caller owns range validation
        (see the router); this method just relays the bytes.
        """
        proxy = await self._proxy()
        url, headers = self._object_target(proxy, key)
        if start is not None:
            end_part = "" if end is None else str(end)
            headers["Range"] = f"bytes={start}-{end_part}"
        async with proxy._client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size):
                yield chunk

    async def remove(self, key: str) -> None:
        proxy = await self._proxy()
        await self._capped(proxy.remove([key]))

    async def remove_many(self, keys: list[str]) -> None:
        """Delete a batch of keys in one call (storage-api takes a list).

        Chunked because the request is a JSON body of key strings — an
        unbounded list (a 1080p tier can be 300+ segments) risks the server's
        body limit.
        """
        if not keys:
            return
        proxy = await self._proxy()
        for i in range(0, len(keys), _REMOVE_BATCH):
            await self._capped(proxy.remove(keys[i : i + _REMOVE_BATCH]))

    async def list_prefix(self, prefix: str) -> list[str]:
        """Every key under ``prefix``, recursively.

        storage-api's ``list`` is one directory level at a time and returns
        folders as entries with a null ``id`` — so this walks breadth-first
        and paginates, rather than assuming one flat call sees everything.
        Needed to clear a version's old HLS output before a re-transcode:
        ``shutil.rmtree`` has no object-store equivalent.
        """
        proxy = await self._proxy()
        out: list[str] = []
        pending = [prefix.strip("/")]
        while pending:
            base = pending.pop()
            offset = 0
            while True:
                page = await self._capped(
                    proxy.list(base, {"limit": _LIST_PAGE, "offset": offset})
                )
                if not page:
                    break
                for entry in page:
                    name = entry.get("name")
                    if not name:
                        continue
                    child = f"{base}/{name}" if base else name
                    # Null id marks a folder placeholder, not an object.
                    if entry.get("id") is None:
                        pending.append(child)
                    else:
                        out.append(child)
                if len(page) < _LIST_PAGE:
                    break
                offset += _LIST_PAGE
        return out

    async def remove_prefix(self, prefix: str) -> int:
        """Delete everything under ``prefix``. Returns the object count."""
        keys = await self.list_prefix(prefix)
        await self.remove_many(keys)
        return len(keys)

    async def put_dir(
        self,
        local_dir: str,
        key_for: "Callable[[str], str]",
        *,
        concurrency: int = _DIR_UPLOAD_CONCURRENCY,
        skip_existing: bool = False,
    ) -> int:
        """Upload a whole local directory tree, concurrently. Returns file count.

        ``key_for`` maps a POSIX path relative to ``local_dir`` onto the object
        key, so the caller owns the key scheme (HLS keeps its tree; other
        callers could content-address).

        Concurrency is bounded by a semaphore: an HLS version is ~300 segments
        and a full migration is ~31k, so firing every upload at once would
        exhaust connections, while going strictly serial would take hours.

        ``skip_existing`` makes a re-run cheap after a partial failure. It
        costs one HEAD per file, so it is opt-in — a fresh transcode knows the
        prefix is empty and should not pay for it.
        """
        import asyncio
        from pathlib import Path

        root = Path(local_dir)
        files = sorted(p for p in root.rglob("*") if p.is_file())
        sem = asyncio.Semaphore(max(1, concurrency))

        # First error wins and is re-raised; the rest are cancelled by
        # gather(return_exceptions=False).
        async def _one(path: Path) -> None:
            rel = path.relative_to(root).as_posix()
            key = key_for(rel)
            async with sem:
                if skip_existing and await self.exists(key):
                    return
                mime = mimetypes.guess_type(rel)[0] or "application/octet-stream"
                await self.put_file(key, str(path), mime)

        await asyncio.gather(*(_one(p) for p in files))
        return len(files)

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


# Canonical bucket for the unified library (spec 2026-07-12): resource
# uploads, project_files, storyboard originals. chat-media stays separate.
LIBRARY_BUCKET = "library"


def library_store() -> ObjectStore:
    return ObjectStore(LIBRARY_BUCKET)


def sha256_file(path: str) -> str:
    """Streaming sha256 of a local file (sync — wrap in asyncio.to_thread)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class StoredObject:
    """Result of a unified-storage write: the sb:// value to persist + facts."""

    file_path: str
    size_bytes: int
    sha256: str


async def store_local_file(
    *,
    scope_id: int,
    source_path: str,
    mime: str,
    filename: Optional[str] = None,
    sha256: Optional[str] = None,
    store: Optional[ObjectStore] = None,
) -> StoredObject:
    """Content-address a local file into the library bucket (dedup PUT).

    The ONE write entrypoint for unified storage: sha256 (reuse the caller's
    precomputed hash when given — uploads already hash while streaming) →
    content key → skip-PUT if present → return the sb:// file_path to persist.
    Raises on storage failure; the CALLER owns the filesystem fallback.
    """
    import asyncio
    import os

    target = store or library_store()
    sha = sha256 or await asyncio.to_thread(sha256_file, source_path)
    key = content_key_from_sha(scope_id=scope_id, sha=sha, mime=mime, filename=filename)
    size = os.path.getsize(source_path)
    if not await target.exists(key):
        await target.put_file(key, source_path, mime)
    return StoredObject(
        file_path=to_file_path(target.bucket, key), size_bytes=size, sha256=sha
    )


@asynccontextmanager
async def materialize(file_path: str) -> AsyncIterator["Path"]:
    """Yield a REAL local Path for any file_path shape (the ffmpeg adapter).

    filesystem row → the actual path under DOWNLOAD_PATH (not cleaned up);
    sb:// row → streamed to a temp file, deleted on exit. Tooling (ffprobe,
    HLS transcode, thumbnails, promote) uses this instead of touching
    DOWNLOAD_PATH directly, so it works for both shapes.
    """
    import os
    import tempfile
    from pathlib import Path

    from app.core.config import settings

    loc = resolve_media_source(file_path)
    if not loc.is_object_store:
        # Containment guard (mirrors generated_media_local_path): a
        # corrupt/hostile rel_path with ".." segments or symlink tricks must
        # never escape DOWNLOAD_PATH.
        base = os.path.realpath(settings.DOWNLOAD_PATH)
        real = os.path.realpath(os.path.join(base, loc.rel_path or ""))
        if not (real == base or real.startswith(base + os.sep)):
            raise ValueError(f"file_path escapes DOWNLOAD_PATH: {file_path!r}")
        yield Path(real)
        return
    store = ObjectStore(loc.bucket)

    # ── 读通磁盘缓存(2026-08-02,spec 2026-08-02-s3-materialize-disk-cache)──
    # followup 链(缩略图/抽音频/转码/AI)各自独立 materialize 同一个对象,
    # 无缓存时同链重复全量拉 3-4 次。缓存在部署机本地 NVMe(compose bind);
    # 文件名 = sha256(key)——对 key 哈希而非假设 key 含内容 sha,因为
    # derived/album/hls 的 key 不是内容寻址的。命中刷 mtime(LRU 信号,
    # atime 受 relatime 不可靠),退出不删;禁用(默认)走下方 temp 旧行为。
    cache_dir = settings.MEDIA_S3_CACHE_DIR
    if cache_dir and os.path.isdir(cache_dir):
        import hashlib
        import uuid as _uuid

        name = hashlib.sha256(loc.key.encode()).hexdigest()[:32] + Path(loc.key).suffix
        cached = Path(cache_dir) / name
        if cached.is_file():
            os.utime(cached, None)
            yield cached
            return
        tmp_cache = Path(cache_dir) / f".tmp-{_uuid.uuid4().hex}"
        try:
            import aiofiles

            async with aiofiles.open(tmp_cache, "wb") as out:
                async for chunk in store.get_stream(loc.key):
                    await out.write(chunk)
            # 并发同 key 竞态安全:各写各的 tmp,replace 原子幂等入位。
            os.replace(tmp_cache, cached)
            # 只读保护:调用方(ffmpeg/whisper/缩略图)均只读源文件;0444 把
            # 未来某个误写调用方变成显式报错而非静默污染缓存。
            os.chmod(cached, 0o444)
        except BaseException:
            tmp_cache.unlink(missing_ok=True)
            raise
        _evict_s3_cache(cache_dir, settings.MEDIA_S3_CACHE_MAX_GB)
        yield cached
        return

    fd, tmp = tempfile.mkstemp(suffix=Path(loc.key).suffix)
    os.close(fd)
    try:
        import aiofiles

        async with aiofiles.open(tmp, "wb") as out:
            async for chunk in store.get_stream(loc.key):
                await out.write(chunk)
        yield Path(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _evict_s3_cache(cache_dir: str, max_gb: float) -> None:
    """按 mtime 从旧到新删,直到目录总量落回上限内。best-effort。

    正在被 ffmpeg 读的文件被删也安全(POSIX unlink:已打开的 fd 不受影响,
    inode 到关闭才回收),所以不需要任何"在用"协调——这正是选 LRU 缓存而
    不是跨 workflow 显式引用计数的原因(计数泄漏 = FS 债务回潮)。
    残留的老 .tmp-*(下载中途崩溃遗尸)同样按 mtime 参与淘汰被清走。
    30 分钟内的新文件不淘汰:刚 replace 入位、调用方尚未 open 的窗口里,
    并发淘汰把它删掉会让调用方拿到 FileNotFoundError——热文件本来也不该
    是 LRU 受害者。
    """
    import os as _os
    import time as _time
    from pathlib import Path as _Path

    _FRESH_S = 30 * 60

    try:
        now = _time.time()
        entries = []
        total = 0
        for p in _Path(cache_dir).iterdir():
            if not p.is_file():
                continue
            st = p.stat()
            total += st.st_size
            if now - st.st_mtime < _FRESH_S:
                continue  # 新鲜文件计入总量但不做淘汰候选
            entries.append((st.st_mtime, st.st_size, p))
        cap = int(max_gb * 1024**3)
        if total <= cap:
            return
        for _mtime, size, p in sorted(entries):
            try:
                _os.chmod(p, 0o644)
                p.unlink()
                total -= size
            except OSError:
                continue
            if total <= cap:
                break
    except OSError:
        return
