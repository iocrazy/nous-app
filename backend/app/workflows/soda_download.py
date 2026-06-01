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

from pathlib import Path
from typing import Any, Optional

from dbos import DBOS

from app.core.utils import Utils
from app.repositories.media_repository import MediaRepository
from app.repositories.resources_repository import ResourcesRepository
from app.services.infra.unified_task_manager import get_task_manager
from app.services.media.parsers.soda_music.cookie_source import get_soda_cookie
from app.services.media.parsers.soda_music.soda_downloader import download_and_decrypt

MIME_BY_EXT = {"flac": "audio/flac", "m4a": "audio/mp4", "mp3": "audio/mpeg"}


def already_downloaded(media_row: dict, base_dir: str) -> bool:
    """True if the track's audio file is already on disk (skip re-download)."""
    rel = (media_row or {}).get("music_download_path")
    if not rel:
        return False
    return (Path(base_dir) / rel).exists()


def build_audio_dest(*, media_id: str, ext: str, base_dir: str) -> tuple[Path, str]:
    """Return ``(full_path, relative_path)`` for the decrypted audio file."""
    rel = f"global/resources/web/qishui/{media_id}/audio.{ext}"
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
    await manager.start(wf_id)

    # 1. Resolve the media row (always fetch so the skip-guard can inspect it).
    row = await MediaRepository().get_by_platform_id(platform_id)
    if media_id is None:
        if not row:
            raise RuntimeError(
                f"soda_download: no parsed_media for platform_id={platform_id}"
            )
        media_id = row["id"]

    # 1b. Skip re-download when the audio file is already on disk.
    base_dir = Utils.get_download_base_path()
    if already_downloaded(row, base_dir):
        await manager.complete(wf_id, subtitle=f"Already downloaded {title}")
        return {"platform_id": platform_id, "media_id": media_id, "skipped": True}

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

    # 4. Download + decrypt to disk.
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
    )

    await manager.update_progress(wf_id, 80, subtitle="Saving to library")

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

    await manager.complete(wf_id, subtitle=f"Downloaded {title}")
    return {"platform_id": platform_id, "media_id": media_id, "size": size}
