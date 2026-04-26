# app/tasks/download_tasks.py

"""
Download Tasks Module

Unified Celery task for all platforms.
Implementation is split across:
- download_progress.py  — UnifiedProgressTracker + stage helpers
- download_helpers.py   — URL validation, audio extraction, pipeline chaining
- download_strategies.py — _do_douyin_download, _do_ytdlp_download
"""

from celery import shared_task
from loguru import logger

from app.repositories.user_logs_repository import log_user_action
from app.tasks.download_helpers import maybe_chain_ai_pipeline, maybe_chain_transcode
from app.tasks.download_progress import UnifiedProgressTracker
from app.tasks.download_strategies import _do_douyin_download, _do_ytdlp_download
from app.tasks.utils import run_async


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def download_unified_task(
    self,
    platform_id: str,
    user_id: str,
    url: str = None,
    download_video: bool = True,
    download_cover: bool = True,
    media_type: int = 0,
    video_title: str = "undefined",
    resource_id: str = None,
    _dedup_key: str = None,  # Orchestrator dedup key
    _unified_task_id: str = None,  # Orchestrator task ID (for signals)
    download_music: bool = False,  # Deprecated, kept for backward compat with queued tasks
    user_agent: str = None,  # Douyin UA chosen during parse; threaded to yt-dlp / direct HTTP
):
    """
    Unified download task for all platforms.

    Routing:
      - url=None  → Douyin path (reads download URLs from DB, downloads via httpx)
      - url given → yt-dlp path (downloads directly from URL)

    Audio is automatically extracted from video after download (ffmpeg -c:a copy).

    Args:
        platform_id: Media platform ID
        user_id: User ID
        url: Original URL (only for yt-dlp platforms; None for Douyin)
        download_video: Whether to download video
        download_cover: Whether to download cover/thumbnail
        media_type: Media type (0=video, 2/68=images). Only used in Douyin path.
        video_title: Title for logging and task tracker display
        resource_id: User's resource record ID (for per-user status updates)
        _dedup_key: Orchestrator dedup key (for Redis lock management)
        _unified_task_id: Orchestrator task ID (for Celery signal hooks)
        download_music: Deprecated, ignored. Audio is auto-extracted from video.
    """
    task_id = self.request.id
    strategy = "yt-dlp" if url else "douyin"
    requested_types = [
        t for t, f in [("video", download_video), ("cover", download_cover)] if f
    ]
    logger.info(
        f"[Download/Init] {platform_id}: strategy={strategy}, "
        f"types=[{','.join(requested_types)}], task_id={task_id}"
    )

    # ── UnifiedTaskManager setup (Supabase lifecycle) ──
    from app.services.unified_task_manager import get_task_manager

    manager = get_task_manager()
    unified_task_id = _unified_task_id  # Use pre-created task from HTTP handler

    if not unified_task_id:
        # Fallback: no pre-created task (retry / legacy callers) → create one
        dl_parts = []
        if download_video:
            dl_parts.append("Video")
        if download_cover:
            dl_parts.append("Cover")
        dl_subtitle = " + ".join(dl_parts) if dl_parts else None
        try:
            unified_task_id = run_async(
                manager.create(
                    user_id=user_id,
                    task_type="download",
                    title=f"Download {video_title or platform_id}",
                    subtitle=dl_subtitle,
                    media_id=platform_id,
                    celery_task_id=task_id,
                    dedup_key=_dedup_key,
                )
            )
        except Exception as e:
            logger.warning(f"[TaskManager] Failed to create unified task: {e}")

    if unified_task_id:
        try:
            run_async(manager.start(unified_task_id))
        except Exception as e:
            logger.warning(f"[TaskManager] Failed to start unified task: {e}")

    # Make unified_task_id available to signals via kwargs
    if unified_task_id:
        self.request.kwargs = self.request.kwargs or {}
        self.request.kwargs["_unified_task_id"] = unified_task_id
        if _dedup_key:
            self.request.kwargs["_dedup_key"] = _dedup_key

    try:
        # ── Progress tracker setup (Redis real-time) ──
        from app.celery_app import celery_app

        redis_client = celery_app.backend.client

        tracker = UnifiedProgressTracker(
            task_id=task_id,
            redis_client=redis_client,
            unified_tracker=manager,
            unified_task_id=unified_task_id,
            user_id=user_id,
        )

        # ── Check global cache: skip download if file already on server ──
        if resource_id:
            from app.repositories.media_repository import MediaRepository as _MR

            _media_repo = _MR()
            global_media = run_async(_media_repo.get_by_platform_id(platform_id))

            if global_media:
                # PR-B: download statuses live on parsed_media; the
                # global_media row already carries them. Don't mirror to
                # resources. The all-cached short-circuit below still
                # decides whether to skip the download.
                has_video_path = bool(global_media.get("download_path"))
                has_cover_path = bool(global_media.get("cover_download_path"))

                # If ALL requested types are cached (status + path), skip download entirely
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

                if all_cached:
                    logger.info(
                        f"[Download/Done] All requested types cached for {platform_id}, skipping download"
                    )
                    tracker.complete()
                    if unified_task_id:
                        try:
                            run_async(manager.complete(unified_task_id))
                        except Exception:
                            pass
                    # Shared download assets (file_path, cover, statuses)
                    # all live on parsed_media. Don't mirror to resources.
                    return {
                        "status": "success",
                        "platform_id": platform_id,
                        "cache_hit": True,
                    }

        # ── Dispatch to strategy ──
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

        # ── Common post-download: check partial failures ──
        completed_parts = [k for k, v in results.items() if v == "completed"]
        failed_parts = [k for k, v in results.items() if v not in (None, "completed")]
        skipped_parts = [k for k, v in results.items() if v is None]
        logger.info(
            f"[Download/Done] {platform_id}: "
            f"completed={','.join(completed_parts) or '-'} / "
            f"failed={','.join(failed_parts) or '-'} / "
            f"skipped={','.join(skipped_parts) or '-'}"
        )

        has_failures = len(failed_parts) > 0
        if has_failures:
            warn_msg = f"Partial failure: {', '.join(failed_parts)} did not complete"
            logger.warning(f"[Download/Done] {warn_msg}: {platform_id}")
            tracker.complete()
            if unified_task_id:
                try:
                    run_async(manager.fail(unified_task_id, warn_msg))
                except Exception:
                    pass
        else:
            tracker.complete()
            if unified_task_id:
                try:
                    run_async(manager.complete(unified_task_id))
                except Exception:
                    pass
            logger.success(f"[Download/Done] All types completed: {platform_id}")

        # Log success
        run_async(
            log_user_action(
                user_id=user_id,
                action="download",
                message=f"Download completed ({strategy}): {video_title[:30]}...",
                status="success",
                aweme_id=platform_id,
                details={"media_type": media_type, "task_id": task_id},
            )
        )

        # ── Mirror file_size_bytes onto resources only ──
        # All shared assets — file_path, cover, *_download_status — are
        # the parsed_media's job (that table got the writes during the
        # download itself). Resources only takes per-user fields here.
        # file_size_bytes stays because the resource_version row needs
        # the byte count and parsed_media uses different columns
        # (storage_size / datasize_bytes) with different semantics.
        path_updates: dict = {}
        fresh_download_path: str | None = None
        if resource_id:
            try:
                from app.repositories.media_repository import MediaRepository as _MR2
                from app.repositories.resources_repository import (
                    ResourcesRepository as _RR2,
                )

                _res_repo2 = _RR2()

                actual_size = 0
                fresh_media = run_async(_MR2().get_by_platform_id(platform_id))
                if fresh_media:
                    fresh_download_path = fresh_media.get("download_path")
                    actual_size = (
                        fresh_media.get("storage_size")
                        or fresh_media.get("datasize_bytes")
                        or 0
                    )
                    if actual_size > 0:
                        path_updates["file_size_bytes"] = actual_size

                logger.info(
                    f"[Download/DB] resource={resource_id}: "
                    f"paths={list(path_updates.keys())}"
                )
                if path_updates:
                    run_async(_res_repo2.update_resource(resource_id, path_updates))

                # Ensure resource_version v1 exists (downloads don't create it).
                # Version's file_path mirrors parsed_media.download_path —
                # the per-user version row owns the history, the actual
                # file lives at the shared path.
                try:
                    existing_versions = run_async(_res_repo2.get_versions(resource_id))
                    if not existing_versions and fresh_download_path:
                        file_path = fresh_download_path
                        filename = (
                            file_path.rsplit("/", 1)[-1]
                            if "/" in file_path
                            else file_path
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
                        run_async(_res_repo2.create_version(version_data))
                        logger.info(
                            f"[Download/DB] Created resource_version v1 for resource={resource_id}"
                        )
                    elif existing_versions and actual_size > 0:
                        # Backfill file_size_bytes on existing versions
                        for ver in existing_versions:
                            if not ver.get("file_size_bytes"):
                                run_async(
                                    _res_repo2.update_version(
                                        ver["id"], {"file_size_bytes": actual_size}
                                    )
                                )
                except Exception as ve:
                    logger.warning(
                        f"[Download/DB] Failed to ensure resource_version for {resource_id}: {ve}"
                    )

                # Ensure parsed_media has the completed statuses (this
                # is the canonical source of truth post-PR-B). Build the
                # update set from ``results`` directly since we no
                # longer mirror via status_updates.
                pm_status_updates: dict = {}
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
                    _mr2 = _MR2()
                    current_pm = fresh_media or run_async(
                        _mr2.get_by_platform_id(platform_id)
                    )
                    if current_pm:
                        needs_update = {
                            k: v
                            for k, v in pm_status_updates.items()
                            if current_pm.get(k) != "completed"
                        }
                        if needs_update:
                            logger.info(
                                f"[Download/DB] parsed_media fallback update for {platform_id}: {needs_update}"
                            )
                            run_async(_mr2.update(platform_id, needs_update))
            except Exception as e:
                logger.warning(
                    f"[Download/DB] Failed to update resource status for {platform_id}: {e}"
                )

        # Queue thumbnail + preview sprite generation out-of-band.
        # Reuses already-known download path (resolved earlier from
        # parsed_media) and avoids blocking the download queue on a slow
        # ffmpeg pass.
        file_path_for_thumb = fresh_download_path if resource_id else None
        if resource_id and file_path_for_thumb:
            mime_type_for_thumb = "video/mp4"
            if file_path_for_thumb.endswith(".webm"):
                mime_type_for_thumb = "video/webm"
            elif file_path_for_thumb.endswith(".mkv"):
                mime_type_for_thumb = "video/x-matroska"
            if mime_type_for_thumb.startswith("video/"):
                try:
                    from app.tasks.thumbnail_tasks import generate_thumbnail_task

                    generate_thumbnail_task.delay(
                        resource_id, file_path_for_thumb, mime_type_for_thumb
                    )
                    logger.info(
                        f"[Download/Post] Thumbnail queued for resource={resource_id}"
                    )
                except Exception as e:
                    logger.warning(
                        f"[Download/Post] Failed to queue thumbnail for resource={resource_id}: {e}"
                    )

        # Chain HLS transcode for video files
        maybe_chain_transcode(platform_id, user_id)

        # Chain AI pipeline if user has auto-transcribe enabled
        maybe_chain_ai_pipeline(platform_id, user_id)

        return {
            "status": "success",
            "platform_id": platform_id,
            "results": results,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Download/{strategy}] Failed: {platform_id}, error: {error_msg}")

        # Mark parsed_media (the canonical source of truth) as failed.
        # Resources no longer mirrors download status, so write straight
        # to parsed_media here.
        try:
            from app.repositories.media_repository import MediaRepository as _MR3

            _media_repo3 = _MR3()
            fail_updates: dict = {}
            if download_video:
                if int(media_type) in (2, 68):
                    fail_updates["image_download_status"] = "failed"
                else:
                    fail_updates["video_download_status"] = "failed"
            if download_cover:
                fail_updates["cover_download_status"] = "failed"
            if fail_updates:
                run_async(_media_repo3.update(platform_id, fail_updates))
        except Exception:
            pass

        # Celery retry with exponential backoff — do NOT mark as failed yet
        if self.request.retries < self.max_retries:
            retry_num = self.request.retries + 1
            countdown = 30 * (2**self.request.retries)
            logger.info(
                f"[Download/{strategy}] Retry {retry_num}/{self.max_retries} "
                f"for {platform_id} in {countdown}s"
            )
            # Update unified task subtitle to show retry status (not failed)
            if unified_task_id:
                try:
                    run_async(
                        manager.update_progress(
                            unified_task_id,
                            progress=0,
                            subtitle=f"Retrying ({retry_num}/{self.max_retries})...",
                        )
                    )
                except Exception:
                    pass
            raise self.retry(exc=e, countdown=countdown)

        # ── Max retries exhausted — NOW mark as permanently failed ──
        try:
            tracker.failed(error_msg)
        except Exception:
            pass
        if unified_task_id:
            try:
                run_async(manager.fail(unified_task_id, error_msg))
            except Exception:
                pass

        # Log failure after max retries
        run_async(
            log_user_action(
                user_id=user_id,
                action="download",
                message=f"Download failed ({strategy}): {video_title[:30]}...",
                status="error",
                aweme_id=platform_id,
                details={"error": error_msg[:200], "retry_count": self.request.retries},
            )
        )

        logger.error(f"[Download/{strategy}] Max retries reached for {platform_id}")
        return {
            "status": "failed",
            "platform_id": platform_id,
            "error": error_msg,
            "retry_count": self.request.retries,
        }


# Backward-compatible aliases for existing call sites during migration
download_media_task = download_unified_task
download_ytdlp_task = download_unified_task
