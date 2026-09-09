"""DBOS workflow for Soda Music (qishui) single-track download.

Re-resolves the track fresh via ``parse_track`` (avoids stale PlayAuth),
downloads + decrypts the audio, persists ``music_download_*`` on parsed_media
and creates a resource row.

Route C discipline (CLAUDE.md):
- Only use the task-manager API (start / update_progress / complete) — never
  PATCH the phase columns directly.
- The task_tracking row is PRE-CREATED by the caller (Task 8); this workflow
  only transitions it.
- Failures must ``raise`` (never return a failed dict) so DBOS marks the
  workflow FAILED and the mirror trigger writes phase=failed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import aiofiles
from dbos import DBOS, Queue
from loguru import logger

from app.boundary import safe_async_client
from app.core.utils import Utils
from app.db.scope import Scope, request_scope
from app.repositories.media_repository import MediaRepository
from app.repositories.resources_repository import ResourcesRepository
from app.services.infra.unified_task_manager import get_task_manager
from app.services.library.transit_upload import upload_transit_file
from app.services.media.parsers.soda_music.cookie_source import get_soda_cookie
from app.services.media.parsers.soda_music.soda_downloader import download_and_decrypt

MIME_BY_EXT = {"flac": "audio/flac", "m4a": "audio/mp4", "mp3": "audio/mpeg"}

# Per-user concurrency cap for qishui downloads (audio + UGC share it). A
# partitioned queue keyed on user_id runs at most SODA_DOWNLOAD_CONCURRENCY
# workflows PER USER at once; the rest queue durably. Bounds the qishui API
# hit-rate for big playlist batches (ban-avoidance). Fits the future
# per-user proxy/fingerprint model (each user = own egress = own cap).
#
# NOTE: defined at module level (imported pre-launch via _dispatch_bundle) so
# the queue + its poller are registered BEFORE DBOS.launch().
SODA_DOWNLOAD_CONCURRENCY = int(os.environ.get("SODA_DOWNLOAD_CONCURRENCY", "3"))
soda_download_queue = Queue(
    "soda_download",
    worker_concurrency=SODA_DOWNLOAD_CONCURRENCY,
    partition_queue=True,
)


def set_soda_concurrency(n: int) -> None:
    """Set the per-user soda queue concurrency live (clamped 1..20). Driven by
    the `config.parse_concurrency` lifecycle subscriber on Settings save."""
    try:
        n = max(1, min(20, int(n)))
        soda_download_queue.worker_concurrency = n
        logger.info(f"[soda_queue] per-user concurrency set to {n}")
    except Exception as e:
        logger.warning(f"[soda_queue] set concurrency failed: {e}")


def already_downloaded(media_row: dict, base_dir: str) -> bool:
    """True if the track's audio file is already on disk (skip re-download)."""
    rel = (media_row or {}).get("music_download_path")
    if not rel:
        return False
    return (Path(base_dir) / rel).exists()


def needs_cover_topup(media_row: dict) -> bool:
    """True when the cover is still missing/failed but a cover URL exists.

    Per-asset model: when the audio is already downloaded we must NOT skip the
    whole chain — a one-off cover failure would otherwise be unrecoverable. A
    re-fetch should top up just the cover (fetch what's missing). Returns False
    when the cover is done OR there's no url_cover to fetch.
    """
    row = media_row or {}
    cover_done = bool(row.get("cover_download_path")) or (
        row.get("cover_download_status") == "completed"
    )
    has_url_cover = bool(
        ((row.get("metadata") or {}).get("album") or {}).get("url_cover")
    )
    return has_url_cover and not cover_done


def build_audio_dest(*, media_id: str, ext: str, base_dir: str) -> tuple[Path, str]:
    """Return ``(full_path, relative_path)`` for the decrypted audio file."""
    rel = f"global/resources/web/qishui/{media_id}/audio.{ext}"
    return Path(base_dir) / rel, rel


def build_cover_dest(*, media_id: str, base_dir: str) -> tuple[Path, str]:
    """Return ``(full_path, relative_path)`` for the downloaded cover image.

    Mirrors :func:`build_audio_dest` — covers are downloaded server-side because
    douyinpic.com blocks browser hot-linking, so the assembled remote URL 404s
    in the UI. A local cover served via ``/media/{id}/cover`` avoids that.
    """
    rel = f"global/resources/web/qishui/{media_id}/cover.jpg"
    return Path(base_dir) / rel, rel


def build_resource_fields(
    *, file_path: str, ext: str, size_bytes: int, title: str
) -> dict[str, Any]:
    """Resource columns written when a soda download completes.

    These file-completion fields are applied to the resource row that PARSE
    already created (with file_path NULL). Every key is a real ``resources``
    column — in particular there is **no** ``music_download_status`` column on
    ``resources`` (per-format download status lives only on ``parsed_media``);
    sending it triggers PostgREST PGRST204 and the row never persists.
    """
    return {
        "file_type": "audio",
        "mime_type": MIME_BY_EXT.get(ext, "audio/mpeg"),
        "filename": f"{title}.{ext}",
        "file_path": file_path,
        "file_size_bytes": size_bytes,
    }


async def _download_cover(
    *,
    parsed: dict[str, Any],
    media_id: str,
    platform_id: str,
    user_id: str,
    base_dir: str,
    res_repo: ResourcesRepository,
    existing: Optional[dict[str, Any]],
) -> bool:
    """Download the album cover server-side and persist its local path.

    Best-effort: the caller wraps this in try/except so a cover failure never
    fails the (already-successful) audio download. ``album.url_cover`` is the
    raw dict the soda API returns; ``cover_url`` assembles the fetchable URL.

    Returns True when a cover was downloaded + persisted, False when skipped
    (no ``url_cover``). On a download error it raises (the caller marks the
    status failed) — either non-success path resolves cover_download_status to
    a terminal state so the UI never shows a perpetual "Cover Downloading...".
    """
    from app.services.media.parsers.soda_music.soda_api import USER_AGENT, cover_url

    album = (parsed.get("metadata") or {}).get("album") or {}
    url_cover = album.get("url_cover")
    if not url_cover:
        return False

    curl_url = cover_url(url_cover)
    full, rel = build_cover_dest(media_id=media_id, base_dir=base_dir)

    async with safe_async_client() as client:
        resp = await client.get(
            curl_url, headers={"User-Agent": USER_AGENT}, timeout=30
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
async def soda_download_workflow(
    platform_id: str,
    user_id: str,
    *,
    media_id: Optional[str] = None,
    title: str = "untitled",
    resource_id: Optional[str] = None,
    user_agent: Optional[str] = None,
    flow_id: Optional[str] = None,
) -> dict[str, Any]:
    """Download + decrypt a single soda (qishui) track and persist it.

    Re-resolves the track fresh (fresh PlayAuth) rather than receiving a
    pre-made plan, so retries never trip over an expired PlayAuth.
    """
    manager = get_task_manager()
    wf_id = DBOS.workflow_id
    await manager.start(wf_id, user_id=user_id)

    # 1. Resolve the media row (always fetch so the skip-guard can inspect it).
    row = await MediaRepository().get_by_platform_id(platform_id)
    if media_id is None:
        if not row:
            raise RuntimeError(
                f"soda_download: no parsed_media for platform_id={platform_id}"
            )
        media_id = row["id"]

    # 1b. Audio already on disk: don't re-download it. But stay per-asset — if
    #     the cover is still missing/failed, top up JUST the cover (a one-off
    #     cover timeout would otherwise be stuck forever, since this guard used
    #     to skip the whole chain). The cover only needs the stored
    #     metadata.album.url_cover, so no fresh parse/PlayAuth is required.
    base_dir = Utils.get_download_base_path()
    if already_downloaded(row, base_dir):
        if not needs_cover_topup(row):
            await manager.complete(wf_id, subtitle=f"Already downloaded {title}")
            return {"platform_id": platform_id, "media_id": media_id, "skipped": True}

        await manager.update_progress(wf_id, 50, subtitle="Fetching cover")
        res_repo = ResourcesRepository()
        # A2 pass 2: ambient USER scope for the resources read acting on behalf
        # of `user_id` (required arg, always present). _download_cover below
        # self-wraps. INERT until SCOPE_ENFORCE_RESOURCES flips.
        async with request_scope(Scope(user_id=user_id)):
            existing = await res_repo.get_resource_by_media_id_and_creator(
                str(media_id), user_id
            )
        cover_ok = False
        try:
            cover_ok = await _download_cover(
                parsed=row,
                media_id=str(media_id),
                platform_id=platform_id,
                user_id=user_id,
                base_dir=base_dir,
                res_repo=res_repo,
                existing=existing,
            )
        except Exception as exc:  # noqa: BLE001 — cover is non-critical
            logger.warning("soda_download: cover top-up failed (non-fatal): {}", exc)
        if not cover_ok:
            try:
                await MediaRepository().update(
                    platform_id, {"cover_download_status": "failed"}
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("soda_download: could not mark cover failed: {}", exc)
        await manager.complete(
            wf_id,
            subtitle=(
                f"Cover updated {title}" if cover_ok else f"Already downloaded {title}"
            ),
        )
        return {
            "platform_id": platform_id,
            "media_id": media_id,
            "skipped_audio": True,
            "cover_ok": cover_ok,
        }

    # 2. Fresh cookie for this user.
    cookie = await get_soda_cookie(user_id)

    # 3. Re-resolve the track fresh (avoids stale PlayAuth).
    from app.services.media.parsers.soda_music.soda_api import SodaApiClient
    from app.services.media.parsers.soda_music.soda_parser import parse_track

    _pd, plan = await parse_track(
        SodaApiClient(cookie=cookie),
        track_id=platform_id,
        want_quality="lossless",
        original_url="",
    )

    await manager.update_progress(wf_id, 20, subtitle=f"Downloading {title}")

    # 4. Download + decrypt to disk. Stream-progress so the UI doesn't freeze at
    #    20% during the byte fetch: map the download's 0-100% into the 20-75 band
    #    (75-80 covers decrypt+save). Best-effort — progress never blocks DL.
    async def _on_download_progress(pct: int) -> None:
        await manager.update_progress(
            wf_id, 20 + (pct * 55) // 100, subtitle=f"Downloading {title}"
        )

    full, rel = build_audio_dest(
        media_id=str(media_id),
        ext=plan.ext,
        base_dir=base_dir,
    )
    size = await download_and_decrypt(
        url=plan.url,
        play_auth=plan.play_auth,
        dest_path=str(full),
        cookie=cookie,
        progress_cb=_on_download_progress,
    )

    await manager.update_progress(wf_id, 80, subtitle="Saving to library")

    # The decrypted track leaves the transit dir for the object store; ``rel``
    # becomes the sb:// value every row below persists (flag off: unchanged).
    rel = await upload_transit_file(
        user_id=user_id,
        local_path=str(full),
        relative_path=rel,
        mime=MIME_BY_EXT.get(plan.ext, "audio/mpeg"),
    )

    # 5. Persist on parsed_media + link the file to the resource row.
    #    PARSE already created the resource row (with file_path NULL); the
    #    download fills in file_path/size/mime. Update that row — do NOT create
    #    a second one. Fall back to create only if parse somehow didn't.
    await MediaRepository().update(
        platform_id,
        {"music_download_status": "completed", "music_download_path": rel},
    )
    res_repo = ResourcesRepository()
    fields = build_resource_fields(
        file_path=rel, ext=plan.ext, size_bytes=size, title=title
    )
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

    # 6. Best-effort cover download (douyinpic.com blocks browser hot-linking,
    #    so the assembled remote URL 404s in the UI). The audio already
    #    succeeded — a cover failure must NEVER fail the workflow.
    cover_ok = False
    try:
        cover_ok = await _download_cover(
            parsed=_pd,
            media_id=str(media_id),
            platform_id=platform_id,
            user_id=user_id,
            base_dir=base_dir,
            res_repo=res_repo,
            existing=existing,
        )
    except Exception as exc:  # noqa: BLE001 — cover is non-critical
        logger.warning("soda_download: cover download failed (non-fatal): {}", exc)

    # Resolve cover_download_status to a TERMINAL state. If the cover was
    # skipped (no url_cover) or errored, mark it failed — otherwise it stays
    # 'pending' forever and the UI shows a perpetual "Cover Downloading...".
    if not cover_ok:
        try:
            await MediaRepository().update(
                platform_id, {"cover_download_status": "failed"}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("soda_download: could not mark cover failed: {}", exc)

    await manager.complete(wf_id, subtitle=f"Downloaded {title}")
    return {"platform_id": platform_id, "media_id": media_id, "size": size}
