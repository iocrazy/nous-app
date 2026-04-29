"""download DBOS workflow — port of `download_tasks.download_unified_task`.

The legacy file is 484 LOC; about 60% is unified_task_manager
lifecycle glue + Celery retry boilerplate that DBOS handles natively.
The actual download work delegates to:
    - download_strategies._do_douyin_download (no url; reads URLs from DB)
    - download_strategies._do_ytdlp_download (url given; yt-dlp pulls)

Both strategy functions accept a UnifiedProgressTracker. We pass one
with unified_tracker=None — the tracker degrades to pure Redis pub/sub
mode (channel `task_progress:{user_id}`), which is exactly what the
WebSocket layer consumes anyway. DBOS owns workflow lifecycle now;
the legacy unified_tasks bookkeeping is dropped here and rebuilt as
the DBOS-status bridge in D4.

Steps:
  - check_global_cache_step: short-circuit if all requested types are
    already on the shared cache (parsed_media has download_path +
    *_status='completed')
  - run_download_step: dispatch to strategy; retry-allowed for
    transient HTTP failures (max_attempts=3 mirrors the Celery
    max_retries=3 budget)
  - finalize_post_download_step: resource_version v1 creation,
    file_size_bytes mirror onto resources, parsed_media status
    fallback, audit log
  - chain_followups_step: thumbnail_workflow + transcode (TODO: rewire
    to DBOS once D4 swaps maybe_chain_* call sites) + AI pipeline

⚠️ Pending before flipping dbos_workflow_routing to 'shadow' then
'dbos': end-to-end test against (a) a fresh Douyin URL and (b) a
yt-dlp-supported URL. The dispatch site is parse_workflow (not yet
ported) — D4 wiring will swap parse → download chain.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from dbos import DBOS
from loguru import logger


@DBOS.step()
def check_global_cache_step(
    *,
    platform_id: str,
    media_type: int,
    download_video: bool,
    download_cover: bool,
) -> dict[str, Any]:
    """Return {cache_hit: True} when all requested asset types are
    already on shared parsed_media. Otherwise return {cache_hit: False}
    so the workflow proceeds to the strategy dispatch."""
    from app.repositories.media_repository import MediaRepository

    async def _do() -> dict[str, Any]:
        media_repo = MediaRepository()
        global_media = await media_repo.get_by_platform_id(platform_id)
        if not global_media:
            return {"cache_hit": False}

        has_video_path = bool(global_media.get("download_path"))
        has_cover_path = bool(global_media.get("cover_download_path"))

        all_cached = True
        if download_video:
            if int(media_type) in (2, 68):
                all_cached = (
                    all_cached
                    and global_media.get("image_download_status") == "completed"
                    and has_video_path
                )
            else:
                all_cached = (
                    all_cached
                    and global_media.get("video_download_status") == "completed"
                    and has_video_path
                )
        if download_cover:
            all_cached = (
                all_cached
                and global_media.get("cover_download_status") == "completed"
                and has_cover_path
            )
        return {"cache_hit": bool(all_cached)}

    return asyncio.run(_do())


@DBOS.step(retries_allowed=True, max_attempts=3)
def run_download_step(
    *,
    platform_id: str,
    user_id: str,
    url: Optional[str],
    download_video: bool,
    download_cover: bool,
    media_type: int,
    user_agent: Optional[str],
) -> dict[str, Any]:
    """Dispatch to the right strategy. Returns the per-asset
    {video, cover, music} → status dict from the strategy."""
    from app.celery_app import celery_app
    from app.tasks.download_progress import UnifiedProgressTracker
    from app.tasks.download_strategies import (
        _do_douyin_download,
        _do_ytdlp_download,
    )

    # Synthetic task_id for tracker bookkeeping. DBOS workflow_id would be
    # nicer but the tracker only uses this for the Redis payload metadata.
    fake_task_id = f"dbos-download-{platform_id}"

    tracker = UnifiedProgressTracker(
        task_id=fake_task_id,
        redis_client=celery_app.backend.client,
        unified_tracker=None,  # DBOS workflow status replaces this
        unified_task_id=None,
        user_id=user_id,
    )

    if url:
        results = _do_ytdlp_download(
            url=url,
            platform_id=platform_id,
            user_id=user_id,
            download_video=download_video,
            download_cover=download_cover,
            tracker=tracker,
            user_agent=user_agent,
        )
    else:
        results = _do_douyin_download(
            platform_id=platform_id,
            user_id=user_id,
            download_video=download_video,
            download_cover=download_cover,
            media_type=media_type,
            tracker=tracker,
            user_agent=user_agent,
        )

    completed = [k for k, v in results.items() if v == "completed"]
    failed = [k for k, v in results.items() if v not in (None, "completed")]
    return {
        "results": results,
        "completed_parts": completed,
        "failed_parts": failed,
    }


@DBOS.step()
def finalize_post_download_step(
    *,
    platform_id: str,
    user_id: str,
    resource_id: Optional[str],
    download_video: bool,
    download_cover: bool,
    media_type: int,
    results: dict[str, Any],
) -> dict[str, Any]:
    """Resource_version v1 creation, file_size_bytes mirror,
    parsed_media status fallback. Returns the canonical
    `fresh_download_path` so the chain step can dispatch thumbnail.

    Mirrors the legacy task's post-download bookkeeping verbatim
    except the unified_tasks lifecycle calls (DBOS owns those now)."""
    from app.repositories.media_repository import MediaRepository
    from app.repositories.resources_repository import ResourcesRepository

    async def _do() -> dict[str, Any]:
        media_repo = MediaRepository()
        fresh_download_path: Optional[str] = None
        actual_size = 0

        if resource_id:
            res_repo = ResourcesRepository()

            fresh_media = await media_repo.get_by_platform_id(platform_id)
            if fresh_media:
                fresh_download_path = fresh_media.get("download_path")
                actual_size = (
                    fresh_media.get("storage_size")
                    or fresh_media.get("datasize_bytes")
                    or 0
                )
                if actual_size > 0:
                    await res_repo.update_resource(
                        resource_id, {"file_size_bytes": actual_size}
                    )

            try:
                existing_versions = await res_repo.get_versions(resource_id)
                if not existing_versions and fresh_download_path:
                    file_path = fresh_download_path
                    filename = (
                        file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
                    )
                    mime_type = "video/mp4"
                    if filename.endswith(".webm"):
                        mime_type = "video/webm"
                    elif filename.endswith(".mkv"):
                        mime_type = "video/x-matroska"
                    version_data = {
                        "resource_id": resource_id,
                        "version_number": 1,
                        "filename": filename,
                        "file_path": file_path,
                        "file_size_bytes": actual_size if actual_size > 0 else None,
                        "mime_type": mime_type,
                        "uploaded_by": user_id,
                    }
                    await res_repo.create_version(version_data)
                elif existing_versions and actual_size > 0:
                    for ver in existing_versions:
                        if not ver.get("file_size_bytes"):
                            await res_repo.update_version(
                                ver["id"], {"file_size_bytes": actual_size}
                            )
            except Exception as ve:
                logger.warning(f"[download.finalize] resource_version backfill: {ve}")

            # parsed_media status fallback
            pm_status_updates: dict[str, str] = {}
            if download_video:
                v = results.get("video")
                if v == "completed":
                    if int(media_type) in (2, 68):
                        pm_status_updates["image_download_status"] = "completed"
                    else:
                        pm_status_updates["video_download_status"] = "completed"
                m = results.get("music")
                if m == "completed":
                    pm_status_updates["music_download_status"] = "completed"
            if download_cover and results.get("cover") == "completed":
                pm_status_updates["cover_download_status"] = "completed"

            if pm_status_updates:
                current_pm = fresh_media or await media_repo.get_by_platform_id(
                    platform_id
                )
                if current_pm:
                    needs_update = {
                        k: v
                        for k, v in pm_status_updates.items()
                        if current_pm.get(k) != "completed"
                    }
                    if needs_update:
                        await media_repo.update(platform_id, needs_update)

        return {
            "fresh_download_path": fresh_download_path,
            "actual_size": actual_size,
        }

    return asyncio.run(_do())


@DBOS.step()
def chain_followups_step(
    *,
    platform_id: str,
    user_id: str,
    resource_id: Optional[str],
    fresh_download_path: Optional[str],
) -> None:
    """Dispatch thumbnail + transcode + AI pipeline. Best-effort —
    failures here never fail the workflow.

    Today these still go through the legacy Celery dispatchers
    (`generate_thumbnail_task.delay`, `maybe_trigger_transcode`,
    `maybe_chain_ai_pipeline`). D4 will rewire these to
    start_workflow_routed once the dispatch site is touched anyway."""
    if not resource_id or not fresh_download_path:
        return

    mime_type = "video/mp4"
    if fresh_download_path.endswith(".webm"):
        mime_type = "video/webm"
    elif fresh_download_path.endswith(".mkv"):
        mime_type = "video/x-matroska"

    if mime_type.startswith("video/"):
        try:
            from app.tasks.thumbnail_tasks import generate_thumbnail_task

            generate_thumbnail_task.delay(resource_id, fresh_download_path, mime_type)
        except Exception as e:
            logger.warning(f"[download.chain] thumbnail: {e}")

    try:
        from app.tasks.download_helpers import (
            maybe_chain_ai_pipeline,
            maybe_chain_transcode,
        )

        maybe_chain_transcode(platform_id, user_id)
        maybe_chain_ai_pipeline(platform_id, user_id)
    except Exception as e:
        logger.warning(f"[download.chain] transcode/ai chain: {e}")


@DBOS.step()
def log_download_outcome_step(
    *,
    user_id: str,
    platform_id: str,
    video_title: str,
    strategy: str,
    media_type: int,
    outcome: str,
    error: Optional[str] = None,
) -> None:
    """Audit row in user_logs. Best-effort."""
    from app.repositories.user_logs_repository import log_user_action

    async def _do() -> None:
        try:
            if outcome == "success":
                await log_user_action(
                    user_id=user_id,
                    action="download",
                    message=(f"Download completed ({strategy}): {video_title[:30]}..."),
                    status="success",
                    aweme_id=platform_id,
                    details={"media_type": media_type},
                )
            else:
                await log_user_action(
                    user_id=user_id,
                    action="download",
                    message=f"Download failed ({strategy}): {video_title[:30]}...",
                    status="error",
                    aweme_id=platform_id,
                    details={"error": (error or "unknown")[:200]},
                )
        except Exception as e:
            logger.warning(f"[download.log] {e}")

    asyncio.run(_do())


@DBOS.workflow()
def download_workflow(
    platform_id: str,
    user_id: str,
    *,
    url: Optional[str] = None,
    download_video: bool = True,
    download_cover: bool = True,
    media_type: int = 0,
    video_title: str = "undefined",
    resource_id: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict[str, Any]:
    """DBOS port of download_unified_task.

    Recommended workflow_id pattern:
        f"download-{platform_id}-{int(time.time())}"
    so retries within the same minute share results, but a fresh user
    request bumps the suffix to bypass cache.
    """
    strategy = "yt-dlp" if url else "douyin"

    # 1. Cache short-circuit
    cache = check_global_cache_step(
        platform_id=platform_id,
        media_type=media_type,
        download_video=download_video,
        download_cover=download_cover,
    )
    if cache.get("cache_hit"):
        return {
            "status": "success",
            "platform_id": platform_id,
            "cache_hit": True,
        }

    # 2. Strategy dispatch
    try:
        download_result = run_download_step(
            platform_id=platform_id,
            user_id=user_id,
            url=url,
            download_video=download_video,
            download_cover=download_cover,
            media_type=media_type,
            user_agent=user_agent,
        )
    except Exception as e:
        # Mark parsed_media as failed before bailing — same fallback
        # the legacy task did inside its except branch.
        from app.repositories.media_repository import MediaRepository

        async def _mark_failed() -> None:
            try:
                repo = MediaRepository()
                fail_updates: dict[str, str] = {}
                if download_video:
                    if int(media_type) in (2, 68):
                        fail_updates["image_download_status"] = "failed"
                    else:
                        fail_updates["video_download_status"] = "failed"
                if download_cover:
                    fail_updates["cover_download_status"] = "failed"
                if fail_updates:
                    await repo.update(platform_id, fail_updates)
            except Exception:
                pass

        asyncio.run(_mark_failed())
        log_download_outcome_step(
            user_id=user_id,
            platform_id=platform_id,
            video_title=video_title,
            strategy=strategy,
            media_type=media_type,
            outcome="failed",
            error=str(e),
        )
        return {
            "status": "failed",
            "platform_id": platform_id,
            "error": str(e),
        }

    results = download_result["results"]
    has_failures = bool(download_result["failed_parts"])

    # 3. Post-download bookkeeping
    finalize = finalize_post_download_step(
        platform_id=platform_id,
        user_id=user_id,
        resource_id=resource_id,
        download_video=download_video,
        download_cover=download_cover,
        media_type=media_type,
        results=results,
    )

    # 4. Audit log
    log_download_outcome_step(
        user_id=user_id,
        platform_id=platform_id,
        video_title=video_title,
        strategy=strategy,
        media_type=media_type,
        outcome="success" if not has_failures else "failed",
        error=(
            "Partial failure: " + ", ".join(download_result["failed_parts"])
            if has_failures
            else None
        ),
    )

    # 5. Chain follow-ups (best-effort, never fails the workflow)
    chain_followups_step(
        platform_id=platform_id,
        user_id=user_id,
        resource_id=resource_id,
        fresh_download_path=finalize.get("fresh_download_path"),
    )

    return {
        "status": "success" if not has_failures else "partial",
        "platform_id": platform_id,
        "results": results,
    }
