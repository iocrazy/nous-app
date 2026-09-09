"""DBOS workflow for Soda Music (qishui) UGC short-video download.

A qishui UGC video is a **plain unencrypted MP4** (no PlayAuth, no CENC) — it
is resolved by scraping the share page (``SodaApiClient.get_ugc_video``) for a
``videoOptions`` dict carrying a ``*.douyinvod.com`` direct URL. That URL
expires, so — exactly like the audio workflow re-resolves the track for fresh
PlayAuth — this workflow re-resolves the share page at download time for a
fresh URL, then streams the MP4 to disk (no decryption).

Persisted with the generic video columns (``download_path`` /
``video_download_status``) on parsed_media and ``file_type="video"`` on the
resource. Mirrors ``soda_download.py`` closely.

Route C discipline (CLAUDE.md):
- Only use the task-manager API (start / update_progress / complete) — never
  PATCH the phase columns directly.
- The task_tracking row is PRE-CREATED by the caller; this workflow only
  transitions it.
- Failures must ``raise`` (never return a failed dict) so DBOS marks the
  workflow FAILED and the mirror trigger writes phase=failed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Optional

import aiofiles
from dbos import DBOS
from loguru import logger

from app.boundary import cap_aiter, safe_async_client
from app.core.utils import Utils
from app.db.scope import Scope, request_scope
from app.repositories.media_repository import MediaRepository
from app.repositories.resources_repository import ResourcesRepository
from app.services.infra.unified_task_manager import get_task_manager
from app.services.library.transit_upload import upload_transit_file
from app.services.media.parsers.soda_music.cookie_source import get_soda_cookie
from app.services.media.parsers.soda_music.soda_api import _share_page_headers

# Byte ceiling for a single UGC MP4. The host comes from the scraped
# ``videoOptions.url`` (``*.douyinvod.com``), which is attacker-influenceable;
# without a cap a malicious/huge stream could fill the disk. UGC clips are
# short, so 2 GiB is a generous ceiling that real content never reaches.
MAX_UGC_VIDEO_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


def already_downloaded(media_row: dict, base_dir: str) -> bool:
    """True if the video is already stored (skip re-download): an sb:// path
    means it was uploaded to the object store — there is no local file to
    find, and re-downloading it would be the bug; a relative path means the
    legacy filesystem world, where the file must still be on disk."""
    rel = (media_row or {}).get("download_path")
    if not rel:
        return False
    from app.services.library.media_storage import resolve_media_source

    if resolve_media_source(rel).is_object_store:
        return True
    return (Path(base_dir) / rel).exists()


def build_video_dest(*, media_id: str, base_dir: str) -> tuple[Path, str]:
    """Return ``(full_path, relative_path)`` for the downloaded MP4."""
    rel = f"global/resources/web/qishui/{media_id}/video.mp4"
    return Path(base_dir) / rel, rel


def build_cover_dest(*, media_id: str, base_dir: str) -> tuple[Path, str]:
    """Return ``(full_path, relative_path)`` for the downloaded cover image."""
    rel = f"global/resources/web/qishui/{media_id}/cover.jpg"
    return Path(base_dir) / rel, rel


def build_ugc_resource_fields(
    *, file_path: str, size_bytes: int, title: str
) -> dict[str, Any]:
    """Resource columns written when a UGC video download completes.

    Applied to the resource row PARSE already created (file_path NULL). Every
    key is a real ``resources`` column — there is **no** download-status column
    on ``resources`` (per-format status lives only on ``parsed_media``); sending
    one triggers PostgREST PGRST204 and the row never persists.
    """
    return {
        "file_type": "video",
        "mime_type": "video/mp4",
        "filename": f"{title}.mp4",
        "file_path": file_path,
        "file_size_bytes": size_bytes,
    }


async def download_video_file(
    *,
    url: str,
    dest_path: str,
    cookie: str,
    client_factory: Callable[[], Any] | None = None,
) -> int:
    """Stream the (unencrypted) MP4 to ``dest_path``. Returns bytes written.

    The qishui UGC MP4 is a plain ``douyinvod.com`` file — no decryption. We
    stream rather than buffer the whole file (videos are larger than tracks).

    Safety:
        - The byte stream is wrapped with ``cap_aiter(..., MAX_UGC_VIDEO_BYTES)``
          so a malicious/huge upstream cannot fill the disk; on overrun the cap
          raises ``MaxBytesExceededError`` (→ DBOS FAILED).
        - The download is written to a ``.part`` temp file and atomically
          ``os.replace``-d into place only on success, so a failed/aborted
          download never leaves a truncated ``video.mp4`` that the
          ``already_downloaded`` skip-guard would treat as complete.

    HTTP errors propagate (the workflow's failure = DBOS FAILED).

    Raises:
        ValueError: if url is empty.
        httpx.HTTPError: on a non-2xx response (via ``raise_for_status``).
        MaxBytesExceededError: if the stream exceeds ``MAX_UGC_VIDEO_BYTES``.
    """
    if not url:
        raise ValueError("soda ugc download url is empty")

    factory = client_factory or (lambda: safe_async_client())
    headers = _share_page_headers(cookie)

    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    total = 0
    try:
        async with factory() as client:
            async with client.stream(
                "GET", url, headers=headers, timeout=120.0
            ) as resp:
                resp.raise_for_status()
                async with aiofiles.open(part, "wb") as fp:
                    async for chunk in cap_aiter(
                        resp.aiter_bytes(), max_bytes=MAX_UGC_VIDEO_BYTES
                    ):
                        await fp.write(chunk)
                        total += len(chunk)
        os.replace(part, dest)
    except BaseException:
        # Best-effort cleanup so a failed/aborted/over-cap download never
        # leaves a partial file the skip-guard would treat as complete.
        Path(part).unlink(missing_ok=True)
        raise

    logger.info("soda ugc: wrote {} bytes to {}", total, dest_path)
    return total


async def _download_cover(
    *,
    cover_url: str,
    media_id: str,
    platform_id: str,
    user_id: str,
    base_dir: str,
    res_repo: ResourcesRepository,
    existing: Optional[dict[str, Any]],
) -> bool:
    """Download the UGC cover server-side and persist its local path.

    Best-effort: the caller wraps this in try/except so a cover failure never
    fails the (already-successful) video download. Unlike the album path, the
    UGC ``coverURL`` is already a fetchable URL — no assembly needed.

    Returns True on success, False when skipped (no ``cover_url``); on a
    download error it raises. Either non-success path lets the caller resolve
    cover_download_status to a terminal state (no perpetual spinner).
    """
    from app.services.media.parsers.soda_music.soda_api import SHARE_PAGE_USER_AGENT

    if not cover_url:
        return False

    full, rel = build_cover_dest(media_id=media_id, base_dir=base_dir)

    async with safe_async_client() as client:
        resp = await client.get(
            cover_url, headers={"User-Agent": SHARE_PAGE_USER_AGENT}, timeout=30
        )
        resp.raise_for_status()
        data = resp.content

    full.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(full, mode="wb") as f:
        await f.write(data)
    # The transit dir is not durable (2026-09-07): the cover leaves for the
    # object store and ``rel`` becomes its sb:// value (flag off: unchanged).
    rel = await upload_transit_file(
        user_id=user_id, local_path=str(full), relative_path=rel, mime="image/jpeg"
    )

    await MediaRepository().update(
        platform_id,
        {"cover_download_path": rel, "cover_download_status": "completed"},
    )

    # A2 pass 2: ambient USER tenant scope for the resources-repo access — this
    # cover persist acts ON BEHALF OF `user_id` (a required uuid-string arg here,
    # always present). INERT until SCOPE_ENFORCE_RESOURCES flips.
    async with request_scope(Scope(user_id=user_id)):
        res_row = existing or await res_repo.get_resource_by_media_id_and_creator(
            media_id, user_id
        )
        if res_row:
            await res_repo.update_resource(res_row["id"], {"cover_image_path": rel})
    return True


@DBOS.workflow()
async def soda_ugc_download_workflow(
    platform_id: str,
    user_id: str,
    *,
    media_id: Optional[str] = None,
    title: str = "untitled",
    resource_id: Optional[str] = None,
    user_agent: Optional[str] = None,
    flow_id: Optional[str] = None,
) -> dict[str, Any]:
    """Download a single qishui UGC short-video (stream MP4, no decrypt).

    Re-resolves the share page fresh (``platform_id`` == ``ugc_video_id``)
    rather than receiving a pre-made plan, so retries never trip over an
    expired ``douyinvod.com`` URL.
    """
    manager = get_task_manager()
    wf_id = DBOS.workflow_id
    await manager.start(wf_id, user_id=user_id)

    # 1. Resolve the media row (always fetch so the skip-guard can inspect it).
    row = await MediaRepository().get_by_platform_id(platform_id)
    if media_id is None:
        if not row:
            raise RuntimeError(
                f"soda_ugc_download: no parsed_media for platform_id={platform_id}"
            )
        media_id = row["id"]

    # 1b. Skip re-download when the video file is already on disk.
    base_dir = Utils.get_download_base_path()
    if already_downloaded(row, base_dir):
        await manager.complete(wf_id, subtitle=f"Already downloaded {title}")
        return {"platform_id": platform_id, "media_id": media_id, "skipped": True}

    # 2. Fresh cookie for this user.
    cookie = await get_soda_cookie(user_id)

    # 3. Re-resolve the share page fresh (avoids stale douyinvod URL).
    from app.services.media.parsers.soda_music.soda_api import SodaApiClient

    vo = await SodaApiClient(cookie=cookie).get_ugc_video(platform_id)
    url = vo.get("url")
    if not url:
        raise RuntimeError(f"ugc_video {platform_id}: no playable url")

    await manager.update_progress(wf_id, 20, subtitle=f"Downloading {title}")

    # 4. Stream the MP4 to disk (no decryption).
    full, rel = build_video_dest(media_id=str(media_id), base_dir=base_dir)
    size = await download_video_file(
        url=url,
        dest_path=str(full),
        cookie=cookie,
    )

    await manager.update_progress(wf_id, 80, subtitle="Saving to library")

    # The MP4 leaves the transit dir for the object store; ``rel`` becomes the
    # sb:// value every row below persists (flag off: unchanged).
    rel = await upload_transit_file(
        user_id=user_id, local_path=str(full), relative_path=rel, mime="video/mp4"
    )

    # 5. Persist on parsed_media + link the file to the resource row.
    #    PARSE already created the resource row (file_path NULL); the download
    #    fills in file_path/size/mime. Update that row — do NOT create a second
    #    one. Fall back to create only if parse somehow didn't.
    await MediaRepository().update(
        platform_id,
        {"video_download_status": "completed", "download_path": rel},
    )
    res_repo = ResourcesRepository()
    fields = build_ugc_resource_fields(file_path=rel, size_bytes=size, title=title)
    # A2 pass 2: ambient USER scope for the resources read/upsert acting on
    # behalf of `user_id` (required arg, always present). INERT until
    # SCOPE_ENFORCE_RESOURCES flips.
    async with request_scope(Scope(user_id=user_id)):
        existing = await res_repo.get_resource_by_media_id_and_creator(
            str(media_id), user_id
        )
        if existing:
            await res_repo.update_resource(existing["id"], fields)
        else:
            await res_repo.create_resource(
                {
                    "creator_id": user_id,
                    "media_id": str(media_id),
                    "source_type": "web",
                    **fields,
                }
            )

    # 6. Best-effort cover download. The video already succeeded — a cover
    #    failure must NEVER fail the workflow.
    cover_ok = False
    try:
        cover_ok = await _download_cover(
            cover_url=vo.get("coverURL") or "",
            media_id=str(media_id),
            platform_id=platform_id,
            user_id=user_id,
            base_dir=base_dir,
            res_repo=res_repo,
            existing=existing,
        )
    except Exception as exc:  # noqa: BLE001 — cover is non-critical
        logger.warning("soda_ugc_download: cover download failed (non-fatal): {}", exc)

    # Resolve cover_download_status to a TERMINAL state so the UI never shows a
    # perpetual "Cover Downloading...".
    if not cover_ok:
        try:
            await MediaRepository().update(
                platform_id, {"cover_download_status": "failed"}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("soda_ugc_download: could not mark cover failed: {}", exc)

    await manager.complete(wf_id, subtitle=f"Downloaded {title}")
    return {"platform_id": platform_id, "media_id": media_id, "size": size}
