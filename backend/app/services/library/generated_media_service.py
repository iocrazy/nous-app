"""K — register AI-generated media into the Tier-1 generated_media store."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import tempfile
import uuid as _uuid
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Optional
from urllib.parse import urlsplit

import aiofiles
import httpx
from loguru import logger
from sqlalchemy import insert

from app.boundary import MaxBytesExceededError, cap_aiter
from app.core.config import settings
from app.db.session import write_scope
from app.models import GeneratedMedia
from app.services.deliverables.registry import register_deliverable_best_effort
from app.services.library.media_storage import (
    CHAT_MEDIA_BUCKET,
    chat_media_store,
    content_key,
    content_key_from_sha,
    materialize,
    resolve_media_source,
    sha256_file,
    to_file_path,
)
from app.services.library.storage_errors import object_store_write_failed

_DEFAULT_MAX_BYTES = 512 * 1024 * 1024  # 512 MiB ceiling per generation

# Column-level RETURNING (never entity-level select/returning fed to
# .mappings() — that maps each row to ONE entity-named key instead of one
# key per column; see tests/test_scheduled_master_row_shape_e2e.py).
_GENERATED_MEDIA_COLS = tuple(GeneratedMedia.__table__.columns)


def _generated_media_insert_stmt(**values: Any):
    """The one INSERT...RETURNING statement shape shared by
    register_generated_media and _insert_uploaded_row — factored out so
    tests can import and execute the REAL production statement (not a
    locally reconstructed one) against a real engine."""
    return insert(GeneratedMedia).values(**values).returning(*_GENERATED_MEDIA_COLS)


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


# Object-store buffering (NOT a filesystem gate — object store is the only
# write path once FEATURE_CHAT_MEDIA_OBJECT_STORE is on, see
# register_generated_media): small generated images buffer fully in memory
# (single put_bytes). Anything bigger — generated videos, or an image that
# overflows the memory cap — streams through a temp file and uploads from
# disk via put_file instead (no memory blowup).
_OBJECT_STORE_IMAGE_MAX_BYTES = 16 * 1024 * 1024
# NOTE: only gates VIDEO url-downloads. The oversized-image retry path
# re-downloads with _DEFAULT_MAX_BYTES (512 MiB), preserving the pre-refactor
# effective ceiling for big images.
_OBJECT_STORE_STREAM_MAX_BYTES = 256 * 1024 * 1024


async def _write_generation_to_object_store(
    *, scope_id: int, source_url: str, mime: str, kind: str
) -> tuple[str, int, str]:
    """Content-address a generated image/video into the chat-media bucket.

    Returns (sb:// file_path, size_bytes, sha256). Small images buffer in
    memory (single put_bytes). Videos, and images that overflow the memory
    cap, download to a temp file first and delegate to the local-file
    counterpart, which streams the upload (put_file) from disk instead.
    Dedup: an already-present key skips the upload. Raises on any failure —
    object store is the only write path while the flag is on, so the caller
    (register_generated_media) does not catch this.
    """
    if kind != "video":
        try:
            data = await _download_to_bytes(
                source_url, max_bytes=_OBJECT_STORE_IMAGE_MAX_BYTES
            )
        except MaxBytesExceededError:
            pass  # oversized image: fall through to the streamed temp-file path
        else:
            store = chat_media_store()
            sha, key = content_key(scope_id=scope_id, data=data, mime=mime)
            size = len(data)
            if not await store.exists(key):
                await store.put_bytes(key, data, mime)
            return to_file_path(CHAT_MEDIA_BUCKET, key), size, sha

    fd, tmp = tempfile.mkstemp(suffix=ext_for(mime, kind))
    os.close(fd)
    try:
        max_bytes = (
            _OBJECT_STORE_STREAM_MAX_BYTES if kind == "video" else _DEFAULT_MAX_BYTES
        )
        await _download_to(tmp, source_url, max_bytes=max_bytes)
        return await _write_local_generation_to_object_store(
            scope_id=scope_id, source_path=tmp, mime=mime, kind=kind
        )
    finally:
        Path(tmp).unlink(missing_ok=True)


async def _write_local_generation_to_object_store(
    *, scope_id: int, source_path: str, mime: str, kind: str
) -> tuple[str, int, str]:
    """Content-address a LOCAL generated file into the chat-media bucket.

    The local-file counterpart of ``_write_generation_to_object_store`` —
    reads the file instead of downloading a URL. Videos, and images over
    ``_OBJECT_STORE_IMAGE_MAX_BYTES``, stream-hash + put_file from disk (no
    memory blowup); smaller images read fully into memory + put_bytes. Same
    dedup + return contract; raises on any failure."""
    store = chat_media_store()
    size_on_disk = os.path.getsize(source_path)
    if kind == "video" or size_on_disk > _OBJECT_STORE_IMAGE_MAX_BYTES:
        sha = await asyncio.to_thread(sha256_file, source_path)
        key = content_key_from_sha(scope_id=scope_id, sha=sha, mime=mime)
        size = size_on_disk
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
    # 3a: the dsh coordinates of the step that produced this. They exist so the
    # deliverable card can hang off the right step in the transcript — a run id
    # alone puts every output of a 40-step run in one undifferentiated pile.
    # Only the agent lanes set them; every other caller leaves them None.
    turn: Optional[int] = None
    step: Optional[int] = None
    # The asset a run was launched FROM. Goes in the COLUMN, which is what
    # ``GET /generated?source_asset_id=`` filters on and what the asset sheet's
    # generation history reads. Writers that only stamp it into ``params``
    # produce a row no reader on that path can find — the params copy is
    # provenance for a human reading one row, the column is the index.
    #
    # ``generated_media.source_asset_id`` FK-references ``assets.id``, so a
    # value that does not exist is an IntegrityError that kills the whole
    # registration. Callers that take this from client-supplied data MUST
    # resolve it against a real asset first (see
    # ``canvas_generation._source_asset_id_for``).
    source_asset_id: Optional[int] = None


async def register_generated_media(
    *,
    user_id: str,
    scope_id: int,
    source_url: Optional[str] = None,
    source_path: Optional[str] = None,
    mime: str,
    origin: GenerationOrigin,
    recorder: Any = None,
) -> dict:
    """Ingest a generated media blob into Tier-1 and insert one row. Returns it.

    Exactly one of ``source_url`` (fetch a URL) or ``source_path`` (a local file
    a subprocess provider already wrote, e.g. dreamina/jimeng-cli) must be given.

    Object-store path (flag on + generated image/video): the blob is
    content-addressed into the chat-media bucket — small images buffer in
    memory, videos and oversized images stream via a temp file. Object store
    is the ONLY write path while the flag is on: any storage failure RAISES
    (fail loudly), there is no filesystem fallback. Flag off: pure
    filesystem path (dev environments without object storage configured).
    NOTE: this covers only AI *generations* (Tier-1 generated_media) — the
    media library's downloaded/uploaded videos live on their own path and
    stay on the filesystem+nginx.

    ``recorder`` is the LIVE run's recorder when the caller has one (the agent
    tool lane). It is forwarded to the deliverable registry so the event folds
    into the run's own views instead of a second ``RunEventWriter.for_run`` —
    whose ``view.outputs`` the live mirror then erased (3a T8c 缺陷 1). Every
    other caller leaves it None and the registry keeps its late-append path.
    """
    if bool(source_url) == bool(source_path):
        raise ValueError(
            "register_generated_media requires exactly one of source_url / source_path"
        )
    kind = media_kind_from_mime(mime)
    content_sha256: Optional[str] = None

    if settings.FEATURE_CHAT_MEDIA_OBJECT_STORE and kind in ("image", "video"):
        if source_path is not None:
            file_path, size, content_sha256 = (
                await _write_local_generation_to_object_store(
                    scope_id=scope_id, source_path=source_path, mime=mime, kind=kind
                )
            )
        else:
            file_path, size, content_sha256 = await _write_generation_to_object_store(
                scope_id=scope_id, source_url=source_url, mime=mime, kind=kind
            )
    else:
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

    stmt = _generated_media_insert_stmt(
        scope_id=scope_id,
        creator_id=user_id,
        media_kind=kind,
        mime=mime,
        file_path=file_path,
        file_size_bytes=size,
        content_sha256=content_sha256,
        origin_kind=origin.kind,
        origin_run_id=origin.run_id,
        agent_id=origin.agent_id,
        canvas_id=origin.canvas_id,
        node_id=origin.node_id,
        prompt=origin.prompt,
        model=origin.model,
        provider=origin.provider,
        params=origin.params or {},
        cost_cents=origin.cost_cents,
        parent_resource_id=origin.parent_resource_id,
        derivation_kind=origin.derivation_kind,
        conversation_id=origin.conversation_id,
        source_asset_id=origin.source_asset_id,
    )
    async with write_scope() as session:
        row = (await session.execute(stmt)).mappings().first()
    out = dict(row) if row is not None else {}
    if out.get("id") is not None:
        # 唯一入口在这里叠上去：11 个调用点一个都不用改，它们的区别只剩
        # origin.run_id 有没有值。判空的责任在登记口一处——每个调用点各自
        # 记得判，就是「没登记 = 不存在」被悄悄破掉的方式。
        await register_deliverable_best_effort(
            run_id=origin.run_id,
            kind="generated_media",
            ref_id=str(out["id"]),
            title=_first_line(origin.prompt),
            model=origin.model,
            # 今天没有任何调用点填 origin.cost_cents，所以媒体类产出的花费
            # 在血缘里是空的（UI 显 —）。不伪造一个数字（小票已记）。
            cost_cents=origin.cost_cents,
            turn=origin.turn,
            step=origin.step,
            recorder=recorder,
        )
    return out


def _first_line(prompt: Optional[str]) -> Optional[str]:
    """产出卡的标题取提示词首行。整段提示词当标题会把列表撑成一堵墙，
    而首行恰好是人写提示词时的主语句。"""
    if not prompt:
        return None
    lines = prompt.strip().splitlines()
    return lines[0] if lines else None


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
    stmt = _generated_media_insert_stmt(
        scope_id=scope_id,
        creator_id=user_id,
        media_kind=kind,
        mime=mime,
        file_path=file_path,
        file_size_bytes=file_size_bytes,
        origin_kind=origin.kind,
        conversation_id=origin.conversation_id,
        content_sha256=content_sha256,
    )
    async with write_scope() as session:
        row = (await session.execute(stmt)).mappings().first()
    return dict(row) if row is not None else {}


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
    storage failure; the caller turns that into the typed hard failure.
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

    Object-store path (flag on + image or video): content-addressed upload
    to the chat-media bucket, same as ``register_generated_media``. A storage
    error is a HARD, typed failure (ObjectStoreWriteFailed, 2026-09-07) — the
    filesystem under DOWNLOAD_PATH is a transit dir on local NVMe, wiped on
    deploy, not a place to keep a user's upload. Videos joined images on
    2026-09-10 for exactly that reason. Other kinds still land on the
    filesystem.
    """
    kind = media_kind_from_mime(mime)
    if settings.FEATURE_CHAT_MEDIA_OBJECT_STORE and kind in ("image", "video"):
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
            raise object_store_write_failed(
                exc,
                where="register_uploaded_media",
                scope_id=scope_id,
                filename=filename,
                mime=mime,
                size_bytes=len(file_bytes),
            ) from exc
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
#
# The lookahead, not `$`: the front end mints `/cover?v=2` (preview cache bust)
# and `/cover?v=2&full=1` (original tier), and a miss here is a silent
# `yield None` that degrades i2v to text2video with no log. `mediaUrl.COVER_RE`
# on the front end uses the same `(?=$|[?#])` — both sides of one contract.
GENERATED_MEDIA_URL_RE = re.compile(
    r"/generated-media/(\d+)/(?:cover|stream|file)(?=$|[?#])"
)


@asynccontextmanager
async def generated_media_local_path(
    url: str, *, media_kind: str = "image"
) -> AsyncIterator[Optional[str]]:
    """Yield a readable local path for a durable generated-media URL.

    filesystem row → the real path (no cleanup); sb:// row → materialize()
    temp file (deleted on exit); any miss → None. Replaces
    resolve_generated_media_local_path, whose object-store rows returned
    None and silently degraded i2v to text2video — now every shape of
    already-generated asset can seed a new generation.
    """
    match = GENERATED_MEDIA_URL_RE.search(str(url or ""))
    if not match:
        yield None
        return
    gen_id = int(match.group(1))

    from app.repositories.generated_media_repository import GeneratedMediaRepository

    row = await GeneratedMediaRepository().get_by_id(gen_id)
    file_path = row.get("file_path") if row else None
    if not row or row.get("media_kind") != media_kind or not file_path:
        yield None
        return

    loc = resolve_media_source(file_path)
    if not loc.is_object_store:
        # Containment guard: a corrupt/hostile rel_path with ".." segments
        # or symlink tricks must never escape DOWNLOAD_PATH.
        base = os.path.realpath(settings.DOWNLOAD_PATH)
        real = os.path.realpath(os.path.join(base, loc.rel_path or ""))
        if not (real == base or real.startswith(base + os.sep)):
            yield None
            return
        if not os.path.isfile(real):
            yield None
            return
        yield real
        return

    # Object-store row: stream it to a temp file via the shared materialize()
    # helper. Only entry failures (bad key, storage-api down) degrade to
    # None — once materialize() has handed us a path, any exception raised
    # by the caller's own code (e.g. the video provider) must propagate
    # untouched. Driving materialize() through an AsyncExitStack (rather
    # than wrapping `async with materialize(...) as p: yield p` in a
    # blanket try/except) keeps the try/except scoped to entry only — a
    # downstream exception thrown back into this generator at the `yield`
    # below never re-enters the except clause.
    async with AsyncExitStack() as stack:
        try:
            tmp_path = await stack.enter_async_context(materialize(file_path))
        except Exception as exc:
            logger.warning(
                f"[generated_media_local_path] materialize failed for "
                f"gen_id={gen_id} file_path={file_path!r}: {exc!r}"
            )
            yield None
            return
        yield str(tmp_path)


# ---------------------------------------------------------------------------
# Resource-URL → local-file bridge (canvas generation, asset-library P4)
# ---------------------------------------------------------------------------

# The asset library's reference channel is ``resources``, not
# ``generated_media``: ``asset_files.resource_id`` points at a resource row and
# the bundle protocol hands out ``reference_resource_ids``. Until this bridge
# existed the canvas generation chain accepted ONLY
# ``/api/v1/generated-media/…`` URLs, so an asset reference reached
# ``eff.refs`` and then vanished — not in the picture, not in ``dropped_knobs``,
# not anywhere. Both serving endpoints are accepted: ``/cover`` (the derived
# image) and ``/file`` (the original).
RESOURCE_URL_RE = re.compile(r"/resources/(\d+)/(?:cover|file)$")

# The audit line every ``Resources`` read below is filed under. Distinct from
# ``AssetsService._REFERENCE_READ_REASON`` on purpose: that one justifies the
# generate-slot run, this one justifies the canvas workflow, and an audit log
# that files the second under the first is a true-looking line about the wrong
# access.
CANVAS_REFERENCE_READ_REASON = "canvas-generation: resolve resource reference"

ReferenceUrlKind = Literal["genmedia", "resource", "unknown"]


@dataclass(frozen=True)
class ResourceRefResolution:
    """What came of trying to turn one resource URL into a local file.

    Exactly one half is ever set. ``path`` is a readable local file; ``reason``
    is a machine-readable code the caller reports to the user. There is no
    third state where both are None — "it did not work and we cannot say why"
    is the silent drop this type exists to make impossible.
    """

    path: Optional[str] = None
    reason: Optional[str] = None


# The host ``canvas_generation._absolute_media_url`` falls back to when
# ``PUBLIC_API_BASE`` is unset — which is the default, since that setting is not
# declared in ``core/config.py``. Spelled once, here, and read by the minter
# through this module so the two cannot drift apart.
_DEFAULT_PUBLIC_API_BASE = "https://api.nous.ink"


def _own_hosts() -> frozenset[str]:
    """The hosts whose absolute URLs are OURS to resolve.

    A reference URL is only ever a key into our own database, but the DAEMON
    branch of ``canvas_generation`` passes it through to the user's machine to
    FETCH — so an absolute URL pointing somewhere else must never be classified
    as one of ours. Derived from the same settings that MINT these URLs:
    ``MEDIA_PUBLIC_URL`` for ``_reference_url``, and for ``_absolute_media_url``
    both ``PUBLIC_API_BASE`` *and* the literal it falls back to when that
    setting is absent.

    ⚠️ That fallback is the reason ``_DEFAULT_PUBLIC_API_BASE`` is listed here.
    ``PUBLIC_API_BASE`` is not declared in ``core/config.py`` at all, so in the
    default configuration ``_absolute_media_url`` mints URLs on
    ``https://api.nous.ink`` — and without this entry, a URL we minted
    ourselves would come back from ``classify_reference_url`` as ``unknown``.
    Latent rather than live (no producer feeds an absolutised URL back into
    ``eff.refs``, and the failure would be a reported drop rather than a silent
    one), but "the minter and the recogniser disagree about our own host" is
    not a difference to leave standing.

    A host configured nowhere still contributes nothing, so relative paths —
    what every producer in this repo emits — remain the only shape that
    qualifies out of the box beyond our own two names.
    """
    hosts: set[str] = set()
    for value in (
        getattr(settings, "MEDIA_PUBLIC_URL", "") or "",
        getattr(settings, "PUBLIC_API_BASE", "") or _DEFAULT_PUBLIC_API_BASE,
    ):
        netloc = urlsplit(str(value)).netloc.lower()
        if netloc:
            hosts.add(netloc)
    return frozenset(hosts)


def _same_origin_path(url: str) -> Optional[str]:
    """The PATH component of a URL we serve, or None for a foreign one.

    ``//evil.example/resources/1/cover`` is protocol-relative — it looks like a
    path and is not one, so the leading-slash test explicitly excludes it. The
    scheme test is case-insensitive by construction (``urlsplit`` lowercases
    it), which is what refuses ``HTTPS://cdn.example/…``.
    """
    text = str(url or "").strip()
    if not text:
        return None
    if text.startswith("/") and not text.startswith("//"):
        return text
    parsed = urlsplit(text)
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.netloc.lower() not in _own_hosts():
        return None
    return parsed.path or None


def classify_reference_url(url: str) -> tuple[ReferenceUrlKind, Optional[int]]:
    """Which durable-reference shape this URL is, and the row id in it.

    One place branches on the shape so every caller branches the same way.
    Stricter than ``GENERATED_MEDIA_URL_RE.search`` alone: an absolute URL is
    only recognised when its host is ours (see ``_own_hosts``). Every producer
    in this repo emits a relative path, so nothing legitimate is refused — and
    a URL this returns ``unknown`` for is REPORTED by the caller, never
    dropped in silence.
    """
    path = _same_origin_path(url)
    if not path:
        return ("unknown", None)
    match = GENERATED_MEDIA_URL_RE.search(path)
    if match:
        return ("genmedia", int(match.group(1)))
    match = RESOURCE_URL_RE.search(path)
    if match:
        return ("resource", int(match.group(1)))
    return ("unknown", None)


async def _resource_reference_stored_path(
    url: str, *, scope_id: int, media_kind: str = "image"
) -> tuple[Optional[str], Optional[str]]:
    """``(stored_path, reason)`` for one resource reference URL.

    Reuses the assets side's bridge rather than forking it — the scope test is
    ``AssetRelationsRepository.resource_in_scope`` and the column read is
    ``resource_media_rows`` (which carries the ``system_request_scope`` wrap
    ``Resources`` requires under ``SCOPE_ENFORCE_RESOURCES``), and the
    original → thumbnail → cover ladder is ``_reference_stored_path``, the same
    one ``AssetsService._materialize_references`` walks. Two ladders that have
    to agree about which file is "the image" is how the preview and the run
    start showing different pictures.

    The scope test runs OUTSIDE the SYSTEM wrap, and must: that wrap exists so
    a cross-user read passes the choke point at all, and asking "is this in the
    caller's scope" from inside it would ask the question with the answer
    already suppressed.

    Imported lazily because ``assets_service`` imports THIS module at module
    level — a top-level import here would be a cycle.
    """
    kind, resource_id = classify_reference_url(url)
    if kind != "resource" or resource_id is None:
        return (None, "unknown_shape")
    if media_kind != "image":
        # The ladder resolves IMAGE bytes only; a resource is never a video
        # reference on this path. Reported, not silently skipped.
        return (None, "no_image_file")

    from app.repositories.asset_relations_repository import AssetRelationsRepository
    from app.services.assets.assets_service import _reference_stored_path

    relations = AssetRelationsRepository()
    if not await relations.resource_in_scope(int(resource_id), int(scope_id)):
        return (None, "not_in_scope")
    rows = await relations.resource_media_rows(
        [int(resource_id)], system_reason=CANVAS_REFERENCE_READ_REASON
    )
    row = rows.get(int(resource_id))
    if not row:
        # In scope per ``resource_items`` but the ``resources`` row is gone.
        return (None, "no_image_file")
    stored = _reference_stored_path(dict(row))
    if not stored:
        return (None, "no_image_file")
    return (str(stored), None)


async def resource_reference_reason(
    url: str, *, scope_id: int, media_kind: str = "image"
) -> Optional[str]:
    """None when this resource URL is servable to the caller, else the code.

    For the branch that needs the URL rather than the bytes (the codex/dreamina
    daemon fetches refs over the public API): it must still refuse a reference
    that is out of scope or has no image behind it, and it must say which.
    """
    _stored, reason = await _resource_reference_stored_path(
        url, scope_id=scope_id, media_kind=media_kind
    )
    return reason


@asynccontextmanager
async def resource_local_path(
    url: str, *, scope_id: int, media_kind: str = "image"
) -> AsyncIterator[ResourceRefResolution]:
    """Yield a readable local path for a resource-library reference URL.

    The resource twin of ``generated_media_local_path``, with two differences
    the shapes genuinely have: a resource row is SCOPED (a generated-media row
    reached through a durable URL is not), and a miss here yields a REASON
    rather than a bare ``None`` — this bridge is the one whose failures the
    user is shown, so collapsing "not yours" / "no image" / "unreadable" into
    one blank would be the silent drop with extra steps.

    ``materialize()`` carries the containment guard (a ``..`` rel_path can
    never escape ``DOWNLOAD_PATH``) and the object-store streaming, so this
    does not re-implement either. The temp file it may create lives until this
    block exits.
    """
    stored, reason = await _resource_reference_stored_path(
        url, scope_id=scope_id, media_kind=media_kind
    )
    if reason or not stored:
        yield ResourceRefResolution(reason=reason or "no_image_file")
        return

    # Same AsyncExitStack shape as generated_media_local_path: the try/except
    # is scoped to ENTRY alone, so an exception thrown back in at the yield
    # below (the provider blowing up while holding the file) propagates
    # untouched instead of being reported as a materialize failure.
    async with AsyncExitStack() as stack:
        try:
            tmp_path = await stack.enter_async_context(materialize(stored))
        except Exception as exc:
            logger.warning(
                f"[resource_local_path] materialize failed for "
                f"url={url!r} stored={stored!r}: {exc!r}"
            )
            yield ResourceRefResolution(reason="materialize_failed")
            return
        if not os.path.isfile(str(tmp_path)):
            yield ResourceRefResolution(reason="materialize_failed")
            return
        yield ResourceRefResolution(path=str(tmp_path))
