# app/tasks/download_tasks.py

"""
Download Tasks Module

Contains async download tasks for video, image sets, music, and covers.
Integrates with TaskManager for task status tracking and automatic retries.
"""

import asyncio

from celery import shared_task
from loguru import logger

from app.core.enums import DownloadStatus
from app.core.utils import Utils
from app.repositories.user_logs_repository import log_user_action
from app.services.downloader import DownloaderService
from app.tasks.utils import run_async


def _maybe_chain_transcode(platform_id: str, user_id: str):
    """Chain HLS transcoding after download if the resource is a video."""
    try:
        from app.repositories.resources_repository import ResourcesRepository

        repo = ResourcesRepository()
        resource = run_async(repo.get_resource_by_platform_id(platform_id))
        if not resource:
            logger.info(f"[Transcode/Chain] No resource found for platform_id={platform_id}")
            return

        mime = resource.get("mime_type", "")
        if not mime.startswith("video/"):
            logger.debug(f"[Transcode/Chain] Not a video ({mime}), skip: {platform_id}")
            return

        resource_id = str(resource["id"])
        versions = run_async(repo.get_versions(resource_id))
        if not versions:
            logger.info(f"[Transcode/Chain] No versions for resource {resource_id}, skip (download)")
            return

        # Transcode the latest version
        latest = versions[0]
        version_id = str(latest["id"])
        logger.info(
            f"[Transcode/Chain] Chaining transcode: resource={resource_id}, "
            f"version={version_id}, mime={mime}, platform_id={platform_id}"
        )

        from app.tasks.transcode_tasks import maybe_trigger_transcode
        maybe_trigger_transcode(resource_id, version_id, mime, user_id=user_id)
    except Exception as e:
        logger.error(f"[Transcode/Chain] Failed for {platform_id}: {e}", exc_info=True)


def _maybe_chain_ai_pipeline(platform_id: str, user_id: str):
    """Chain AI tasks after download if user has auto-transcribe/summarize enabled."""
    try:
        from app.repositories.user_settings_repository import UserSettingsRepository

        repo = UserSettingsRepository()
        settings = run_async(repo.get_by_user_id(user_id))

        ai_settings = {}
        if settings and settings.get("settings_json"):
            ai_settings = settings["settings_json"].get("ai_settings", {})

        transcript_bool = ai_settings.get("auto_transcribe", False)
        summary_bool = ai_settings.get("auto_summarize", False)

        if not transcript_bool and not summary_bool:
            logger.info(
                f"[AI] Auto-transcribe/summarize disabled for user {user_id}, skipping AI pipeline"
            )
            return

        # Look up resource_id from platform_id
        resource_id = None
        try:
            from app.repositories.resources_repository import ResourcesRepository

            res_repo = ResourcesRepository()
            resource = run_async(res_repo.get_resource_by_platform_id(platform_id))
            if resource:
                resource_id = str(resource["id"])
        except Exception as e:
            logger.debug(f"[AI] Could not resolve resource_id for {platform_id}: {e}")

        from app.tasks.ai_tasks import chain_ai_pipeline

        chain_ai_pipeline(
            platform_id=platform_id,
            user_id=user_id,
            resource_id=resource_id,
            transcript_bool=transcript_bool,
            summary_bool=summary_bool,
        )
        logger.info(
            f"[AI] Pipeline chained after download: {platform_id} "
            f"(transcribe={transcript_bool}, summarize={summary_bool}, resource={resource_id})"
        )

    except Exception as e:
        logger.warning(f"[AI] Failed to chain AI pipeline for {platform_id}: {e}")


class UnifiedProgressTracker:
    """Progress tracker that writes to Redis (real-time) + TaskTracker (Supabase lifecycle)."""

    def __init__(self, task_id: str, redis_client,
                 unified_tracker=None, unified_task_id=None):
        self.task_id = task_id
        self.redis = redis_client
        self.unified_tracker = unified_tracker
        self.unified_task_id = unified_task_id
        self.last_update = 0
        self._last_downloaded = 0
        self._last_time = 0
        self._speed = 0.0

    async def update(self, downloaded: int, total: int):
        """Update download progress.

        Writes to Redis (sync, always) and Supabase unified_tasks (async, throttled).
        Must be awaited from an async context (e.g. inside download_file streaming loop).
        """
        import json
        import time

        now = time.time()
        if now - self.last_update < 0.5:
            return
        self.last_update = now

        # Calculate speed
        if self._last_time > 0:
            time_diff = now - self._last_time
            if time_diff > 0:
                self._speed = (downloaded - self._last_downloaded) / time_diff
        self._last_downloaded = downloaded
        self._last_time = now

        percent = int((downloaded / total) * 100) if total > 0 else 0
        speed_str = self._format_speed(self._speed)

        # Write to Redis for real-time frontend polling
        progress_data = {
            "percent": percent,
            "downloaded": downloaded,
            "total": total,
            "speed": speed_str,
            "status": "downloading",
        }
        self.redis.setex(
            f"download_progress:{self.task_id}", 3600, json.dumps(progress_data)
        )

        # Directly await Supabase update (throttled at 1s internally by TaskTracker).
        # Previous create_task() approach was broken: asyncio.run() cancels all
        # pending tasks when the main coroutine finishes, so progress updates
        # were silently dropped.
        if self.unified_tracker and self.unified_task_id:
            try:
                await self.unified_tracker.update_progress(
                    self.unified_task_id,
                    percent,
                    speed=int(self._speed),
                )
            except Exception as e:
                logger.debug(f"[ProgressTracker] Supabase update failed: {e}")

    def _format_speed(self, bytes_per_sec: float) -> str:
        """Format speed as human readable string."""
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec:.0f} B/s"
        elif bytes_per_sec < 1024 * 1024:
            return f"{bytes_per_sec / 1024:.1f} KB/s"
        else:
            return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"

    def complete(self):
        """Mark download as complete in Redis."""
        import json

        progress_data = {
            "percent": 100,
            "downloaded": 0,
            "total": 0,
            "speed": "0 B/s",
            "status": "completed",
        }
        self.redis.setex(
            f"download_progress:{self.task_id}", 60, json.dumps(progress_data)
        )

    def failed(self, error: str):
        """Mark download as failed in Redis."""
        import json

        progress_data = {
            "percent": 0,
            "status": "failed",
            "error": error[:200] if error else "Unknown error",
        }
        self.redis.setex(
            f"download_progress:{self.task_id}", 300, json.dumps(progress_data)
        )


# ─── URL availability helpers ─────────────────────────────────────────


def _ensure_download_urls(platform_id: str, media: dict, needed_types: list[str]) -> dict:
    """Check if download URLs are available for needed types. Re-parse if missing.

    Mutates and returns the media dict with refreshed URLs if re-parsed.
    """
    url_fields = {
        "video": "video_download_urls",
        "music": "music_download_urls",
        "cover": "cover_urls",
        "image": "image_download_urls",
    }

    missing_types = []
    for t in needed_types:
        field = url_fields.get(t)
        val = media.get(field)
        has_urls = bool(val) and (isinstance(val, list) and len(val) > 0 if isinstance(val, list) else True)
        logger.info(f"[Download/URL] Check {t}: field={field}, has_urls={has_urls}, type={type(val).__name__}")
        if field and not has_urls:
            missing_types.append(t)

    if not missing_types:
        logger.info(f"[Download/URL] All needed URLs available for {platform_id}")
        return media

    logger.info(
        f"[Download/URL] Missing URLs for {missing_types} on {platform_id}, "
        f"re-parsing original_url..."
    )

    original_url = media.get("original_url")
    if not original_url:
        logger.warning(f"[Download/URL] No original_url for {platform_id}, cannot re-parse")
        return media

    try:
        from app.services.lightweight_parser import LightweightParser
        from app.services.douyin_parser import DouyinParser

        # --- Attempt 1: LightweightParser (fast HTTP, no browser) ---
        aweme_detail = run_async(LightweightParser.parse(original_url))
        parse_method = "LightHTTP"

        if aweme_detail:
            new_parsed = run_async(DouyinParser.parse_aweme_detail(
                aweme_detail=aweme_detail,
                valid_url=original_url,
                download_video=True,
                download_music=True,
                download_cover=True,
            ))
        else:
            new_parsed = None

        # Check which types are still missing after LightHTTP
        still_missing = []
        if new_parsed:
            for t in missing_types:
                field = url_fields.get(t)
                if field and not new_parsed.get(field):
                    still_missing.append(t)
        else:
            still_missing = list(missing_types)

        # --- Attempt 2: DouyinAnalysis browser fallback (if still missing) ---
        if still_missing:
            logger.info(
                f"[Download/URL] LightHTTP still missing {still_missing}, "
                f"falling back to BrowserAuto for {platform_id}"
            )
            try:
                from app.services.douyin_analysis import DouyinAnalysis

                browser_detail = run_async(DouyinAnalysis.fetch_one_video(original_url))
                if browser_detail:
                    parse_method = "BrowserAuto"
                    browser_parsed = run_async(DouyinParser.parse_aweme_detail(
                        aweme_detail=browser_detail,
                        valid_url=original_url,
                        download_video=True,
                        download_music=True,
                        download_cover=True,
                    ))
                    if browser_parsed:
                        # Merge browser results into new_parsed (browser data wins)
                        if new_parsed:
                            for t in still_missing:
                                field = url_fields.get(t)
                                if field and browser_parsed.get(field):
                                    new_parsed[field] = browser_parsed[field]
                        else:
                            new_parsed = browser_parsed
                        logger.info(f"[Download/URL] BrowserAuto re-parse succeeded for {platform_id}")
                    else:
                        logger.warning(f"[Download/URL] BrowserAuto parse yielded no data for {platform_id}")
                else:
                    logger.warning(f"[Download/URL] BrowserAuto returned empty for {platform_id}")
            except Exception as e:
                logger.warning(f"[Download/URL] BrowserAuto fallback failed for {platform_id}: {e}")

        if not new_parsed:
            logger.warning(f"[Download/URL] All re-parse attempts failed for {platform_id}")
            return media

        # Update DB with refreshed URLs
        from app.repositories.media_repository import MediaRepository as _MR
        update_fields = {}
        for t in missing_types:
            field = url_fields.get(t)
            if field and new_parsed.get(field):
                update_fields[field] = new_parsed[field]
                media[field] = new_parsed[field]
                logger.info(
                    f"[Download/URL] Refreshed {field} for {platform_id} "
                    f"({len(new_parsed[field])} URLs, via {parse_method})"
                )

        if update_fields:
            run_async(_MR().update(platform_id, update_fields))
        else:
            logger.warning(f"[Download/URL] Re-parse found no new URLs for {missing_types}")

    except Exception as e:
        logger.error(f"[Download/URL] Re-parse failed for {platform_id}: {e}")

    return media


# ─── URL validation helpers ───────────────────────────────────────────


async def _check_url_accessible(url: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Quick HEAD request to verify a download URL is reachable (not expired/blocked).

    Returns (accessible, reason) tuple.
    """
    import httpx

    headers = Utils.get_headers()
    try:
        async with httpx.AsyncClient(http2=True) as client:
            resp = await client.head(url, headers=headers, follow_redirects=True, timeout=timeout)
            if resp.status_code == 200:
                return True, "ok"
            reason = f"HTTP {resp.status_code}"
            logger.debug(f"[Download/Validate] HEAD {reason} for {url[:80]}...")
            return False, reason
    except Exception as e:
        reason = str(e)[:100]
        logger.debug(f"[Download/Validate] HEAD failed for {url[:80]}...: {reason}")
        return False, reason


def _validate_and_refresh_urls(
    platform_id: str, media: dict, url_field: str, type_key: str
) -> tuple[dict, bool, str]:
    """Validate URLs via HEAD, refresh if expired, return (media, urls_valid, reason).

    Flow:
    1. Check current URLs accessibility via HEAD
    2. If all inaccessible → clear URLs in DB → re-parse → check new URLs
    3. Return whether URLs are valid for download
    """
    from app.repositories.media_repository import MediaRepository as _MR_val

    urls = media.get(url_field) or []
    if not urls:
        return media, False, "no URLs available"

    # Test current URLs
    fail_reason = ""
    for url in urls:
        ok, reason = run_async(_check_url_accessible(url))
        if ok:
            return media, True, "ok"
        fail_reason = reason

    # All URLs inaccessible → clear and re-parse
    logger.info(
        f"[Download/Validate] All {len(urls)} {type_key} URLs inaccessible for {platform_id} "
        f"({fail_reason}), clearing and re-parsing..."
    )
    media[url_field] = []
    run_async(_MR_val().update(platform_id, {url_field: None}))
    media = _ensure_download_urls(platform_id, media, [type_key])

    # Test fresh URLs
    fresh_urls = media.get(url_field) or []
    if not fresh_urls:
        return media, False, "re-parse returned no URLs"

    for url in fresh_urls:
        ok, reason = run_async(_check_url_accessible(url))
        if ok:
            logger.info(f"[Download/Validate] Fresh {type_key} URLs accessible for {platform_id}")
            return media, True, "ok"
        fail_reason = reason

    logger.warning(
        f"[Download/Validate] Fresh {type_key} URLs also inaccessible for {platform_id}: {fail_reason}"
    )
    return media, False, fail_reason


# ─── Internal download strategies ─────────────────────────────────────

def _do_douyin_download(
    platform_id: str,
    user_id: str,
    download_video: bool,
    download_music: bool,
    download_cover: bool,
    media_type: int,
    tracker: UnifiedProgressTracker,
) -> dict:
    """Douyin download strategy: reads URLs from DB, downloads via httpx."""
    results = {"video": None, "music": None, "cover": None}

    # Ensure download URLs are available (re-parse if missing)
    from app.repositories.media_repository import MediaRepository as _MR_urls
    media = run_async(_MR_urls().get_by_platform_id(platform_id))
    if media:
        needed = []
        if download_video:
            needed.append("image" if int(media_type) in (2, 68) else "video")
        if download_music:
            needed.append("music")
        if download_cover:
            needed.append("cover")
        media = _ensure_download_urls(platform_id, media, needed)
        # Diagnostic: log URL availability after ensure
        for t in needed:
            url_field = {"video": "video_download_urls", "music": "music_download_urls",
                         "cover": "cover_urls", "image": "image_download_urls"}.get(t)
            urls = media.get(url_field) if url_field else None
            url_count = len(urls) if urls else 0
            logger.info(f"[Download/Diag] {t}: {url_count} URLs available for {platform_id} (field={url_field})")

    # ── Pre-download: validate URL accessibility, refresh if expired ──
    if int(media_type) in (0, 4, 61):  # Video types
        if download_video:
            media, video_ok, reason = _validate_and_refresh_urls(
                platform_id, media, "video_download_urls", "video"
            )
            if not video_ok:
                logger.warning(
                    f"[Download/Exec] video: HEAD check failed for {platform_id} ({reason}), "
                    f"attempting GET download anyway (HEAD/GET may differ)"
                )
            logger.info(f"[Download/Exec] video: downloading {platform_id}...")
            video_result = run_async(
                DownloaderService.download_video_by_platform_id(
                    platform_id, user_id=user_id, progress_tracker=tracker
                )
            )
            results["video"] = (
                video_result.video_download_status.value
                if hasattr(video_result, "video_download_status")
                else "unknown"
            )
            logger.info(f"[Download/Exec] video: {results['video']} for {platform_id}")
            if results["video"] != "completed":
                error_msg = getattr(video_result, "error", None) or "Download failed"
                logger.warning(f"[Download/Exec] video failed for {platform_id}: {error_msg}")

                # ── yt-dlp fallback: try downloading via yt-dlp if httpx failed ──
                original_url = media.get("original_url") if media else None
                if original_url:
                    logger.info(
                        f"[Download/Exec] video: httpx failed, trying yt-dlp fallback "
                        f"with original URL for {platform_id}"
                    )
                    try:
                        from app.services.ytdlp_service import YtdlpService

                        source_platform = media.get("source_platform", "douyin")
                        media_id = str(media["id"])
                        storage_dir, relative_prefix = Utils.create_web_resource_path(
                            source_platform, media_id
                        )

                        async def on_progress(downloaded: int, total: int, speed: str):
                            await tracker.update(downloaded, total)

                        ytdlp_result = run_async(
                            YtdlpService.download_video(
                                original_url, str(storage_dir), platform_id,
                                progress_callback=on_progress,
                            )
                        )
                        if ytdlp_result.get("file_path"):
                            import os
                            file_name = os.path.basename(ytdlp_result["file_path"])
                            relative_path = f"{relative_prefix}/{file_name}"
                            from app.repositories.media_repository import MediaRepository as _MR_yt
                            run_async(
                                _MR_yt().mark_media_as_downloaded(
                                    platform_id=platform_id,
                                    download_path=relative_path,
                                    duration=0,
                                    storage_size=ytdlp_result.get("file_size", 0),
                                )
                            )
                            run_async(
                                DownloaderService.optimize_video_for_streaming(
                                    ytdlp_result["file_path"]
                                )
                            )
                            results["video"] = DownloadStatus.COMPLETED.value
                            logger.success(
                                f"[Download/Exec] video: yt-dlp fallback succeeded for {platform_id}"
                            )
                        else:
                            logger.warning(
                                f"[Download/Exec] video: yt-dlp fallback also failed for {platform_id}"
                            )
                    except Exception as ytdlp_err:
                        logger.warning(
                            f"[Download/Exec] video: yt-dlp fallback error for {platform_id}: "
                            f"{type(ytdlp_err).__name__}: {ytdlp_err}"
                        )
                else:
                    logger.warning(
                        f"[Download/Exec] video: no original_url available for yt-dlp fallback: {platform_id}"
                    )

        if download_music:
            media, music_ok, reason = _validate_and_refresh_urls(
                platform_id, media, "music_download_urls", "music"
            )
            if not music_ok:
                logger.warning(
                    f"[Download/Exec] music: HEAD check failed for {platform_id} ({reason}), "
                    f"attempting GET download anyway"
                )
            logger.info(f"[Download/Exec] music: downloading {platform_id}...")
            result = run_async(
                DownloaderService.download_music_by_platform_id(
                    platform_id=platform_id, user_id=user_id
                )
            )
            results["music"] = (
                result.music_download_status.value
                if hasattr(result, "music_download_status")
                else "unknown"
            )
            if results["music"] != "completed":
                error_msg = getattr(result, "error", None) or "Music download failed"
                logger.warning(f"[Download/Exec] music failed for {platform_id}: {error_msg}")
            else:
                logger.info(f"[Download/Exec] music: {results['music']} for {platform_id}")

    elif int(media_type) in (2, 68):  # Image types
        if download_video:  # "video" flag used for images too
            media, img_ok, reason = _validate_and_refresh_urls(
                platform_id, media, "image_download_urls", "image"
            )
            if not img_ok:
                logger.warning(
                    f"[Download/Exec] image: HEAD check failed for {platform_id} ({reason}), "
                    f"attempting GET download anyway"
                )
            logger.info(f"[Download/Exec] image: downloading {platform_id}...")
            video_result = run_async(
                DownloaderService.download_images_by_platform_id(
                    platform_id, user_id=user_id
                )
            )
            results["video"] = (
                video_result.video_download_status.value
                if hasattr(video_result, "video_download_status")
                else "unknown"
            )
            logger.info(f"[Download/Exec] image: {results['video']} for {platform_id}")
            if results["video"] != "completed":
                error_msg = getattr(video_result, "error", None) or "Image download failed"
                logger.warning(f"[Download/Exec] image failed for {platform_id}: {error_msg}")

        if download_music:
            logger.info(f"[Download/Exec] music: downloading {platform_id}...")
            result = run_async(
                DownloaderService.download_music_by_platform_id(
                    platform_id=platform_id, user_id=user_id
                )
            )
            results["music"] = (
                result.music_download_status.value
                if hasattr(result, "music_download_status")
                else "unknown"
            )
            logger.info(f"[Download/Exec] music: {results['music']} for {platform_id}")

    if download_cover:
        logger.info(f"[Download/Exec] cover: downloading {platform_id}...")
        result = run_async(
            DownloaderService.download_cover_by_platform_id(
                platform_id, user_id=user_id
            )
        )
        results["cover"] = (
            result.cover_download_status.value
            if hasattr(result, "cover_download_status")
            else "unknown"
        )
        logger.info(f"[Download/Exec] cover: {results['cover']} for {platform_id}")

    return results


def _do_ytdlp_download(
    url: str,
    platform_id: str,
    user_id: str,
    download_video: bool,
    download_music: bool,
    download_cover: bool,
    tracker: UnifiedProgressTracker,
) -> dict:
    """yt-dlp download strategy: downloads via yt-dlp using original URL."""
    from app.repositories.media_repository import MediaRepository
    from app.services.url_router import URLRouter
    from app.services.ytdlp_service import YtdlpService

    results = {"video": None, "music": None, "cover": None}

    detected_platform, _ = URLRouter.detect_platform(url)
    repo = MediaRepository()
    media = run_async(repo.get_by_platform_id(platform_id))
    media_id = str(media["id"]) if media else platform_id  # fallback to platform_id
    storage_dir, relative_prefix = Utils.create_web_resource_path(
        detected_platform, media_id
    )

    if download_video:
        logger.info(f"[Download/Exec] video: downloading via yt-dlp {platform_id}...")

        async def on_progress(downloaded: int, total: int, speed: str):
            await tracker.update(downloaded, total)

        result = run_async(
            YtdlpService.download_video(
                url, str(storage_dir), platform_id, progress_callback=on_progress
            )
        )
        if result.get("file_path"):
            import os

            file_name = os.path.basename(result["file_path"])
            relative_path = f"{relative_prefix}/{file_name}"
            repo = MediaRepository()
            run_async(
                repo.mark_media_as_downloaded(
                    platform_id=platform_id,
                    download_path=relative_path,
                    duration=0,
                    storage_size=result.get("file_size", 0),
                )
            )
            run_async(
                DownloaderService.optimize_video_for_streaming(result["file_path"])
            )
            results["video"] = DownloadStatus.COMPLETED.value
            logger.info(f"[Download/Exec] video: completed for {platform_id}")
        else:
            results["video"] = DownloadStatus.FAILED.value
            logger.warning(f"[Download/Exec] video failed via yt-dlp for {platform_id}: no output file")

    if download_music:
        logger.info(f"[Download/Exec] music: extracting via yt-dlp {platform_id}...")
        result = run_async(
            YtdlpService.download_audio(url, str(storage_dir), platform_id)
        )
        if result.get("file_path"):
            import os
            file_name = os.path.basename(result["file_path"])
            audio_relative_path = f"{relative_prefix}/{file_name}"
            repo = MediaRepository()
            run_async(repo.update(platform_id, {
                "music_download_status": DownloadStatus.COMPLETED.value,
                "music_download_path": audio_relative_path,
            }))
            results["music"] = DownloadStatus.COMPLETED.value
            logger.info(f"[Download/Exec] music: completed for {platform_id}")
        else:
            results["music"] = DownloadStatus.FAILED.value
            logger.warning(f"[Download/Exec] music failed via yt-dlp for {platform_id}")

    if download_cover:
        logger.info(f"[Download/Exec] cover: downloading {platform_id}...")
        cover_result = run_async(
            DownloaderService.download_cover_by_platform_id(
                platform_id, user_id=user_id
            )
        )
        if cover_result and cover_result.cover_download_status == DownloadStatus.COMPLETED:
            results["cover"] = DownloadStatus.COMPLETED.value
            logger.info(f"[Download/Exec] cover: completed for {platform_id}")
        else:
            error = cover_result.error if cover_result else "Unknown error"
            logger.warning(f"[Download/Exec] cover failed for {platform_id}: {error}")
            results["cover"] = DownloadStatus.FAILED.value

    return results


# ─── Unified download task ─────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def download_unified_task(
    self,
    platform_id: str,
    user_id: str,
    url: str = None,
    download_video: bool = True,
    download_music: bool = False,
    download_cover: bool = True,
    media_type: int = 0,
    video_title: str = "undefined",
    resource_id: str = None,
    _dedup_key: str = None,       # Orchestrator dedup key
    _unified_task_id: str = None,  # Orchestrator task ID (for signals)
):
    """
    Unified download task for all platforms.

    Routing:
      - url=None  → Douyin path (reads download URLs from DB, downloads via httpx)
      - url given → yt-dlp path (downloads directly from URL)

    Args:
        platform_id: Media platform ID
        user_id: User ID
        url: Original URL (only for yt-dlp platforms; None for Douyin)
        download_video: Whether to download video
        download_music: Whether to download audio
        download_cover: Whether to download cover/thumbnail
        media_type: Media type (0=video, 2/68=images). Only used in Douyin path.
        video_title: Title for logging and task tracker display
        resource_id: User's resource record ID (for per-user status updates)
        _dedup_key: Orchestrator dedup key (for Redis lock management)
        _unified_task_id: Orchestrator task ID (for Celery signal hooks)
    """
    task_id = self.request.id
    strategy = "yt-dlp" if url else "douyin"
    requested_types = [t for t, f in [("video", download_video), ("music", download_music), ("cover", download_cover)] if f]
    logger.info(
        f"[Download/Init] {platform_id}: strategy={strategy}, "
        f"types=[{','.join(requested_types)}], task_id={task_id}"
    )

    # ── TaskTracker setup (Supabase lifecycle) ──
    from app.services.task_tracker import get_task_tracker
    tracker_unified = get_task_tracker()
    unified_task_id = None
    dl_parts = []
    if download_video:
        dl_parts.append("Video")
    if download_music:
        dl_parts.append("Audio")
    if download_cover:
        dl_parts.append("Cover")
    dl_subtitle = " + ".join(dl_parts) if dl_parts else None

    try:
        unified_task_id = run_async(tracker_unified.create(
            user_id=user_id,
            task_type="download",
            title=video_title or platform_id,
            subtitle=dl_subtitle,
            media_id=platform_id,
            celery_task_id=task_id,
        ))
        run_async(tracker_unified.start(unified_task_id))
    except Exception as e:
        logger.warning(f"[TaskTracker] Failed to create unified task: {e}")

    # Store orchestrator metadata for Celery signals
    if unified_task_id and _dedup_key:
        try:
            from app.services.task_orchestrator import get_orchestrator, TaskPhase
            orchestrator = get_orchestrator()
            # Update the unified_task row with dedup_key and phase
            client = run_async(orchestrator._get_client())
            run_async(
                client.table("unified_tasks").update({
                    "dedup_key": _dedup_key,
                    "phase": TaskPhase.DEDUP_CHECK.value,
                }).eq("id", unified_task_id).execute()
            )
        except Exception as e:
            logger.warning(f"[Orchestrator] Failed to set dedup_key: {e}")

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
            unified_tracker=tracker_unified,
            unified_task_id=unified_task_id,
        )

        # ── Check global cache: skip download if file already on server ──
        if resource_id:
            from app.repositories.media_repository import MediaRepository as _MR
            from app.repositories.resources_repository import ResourcesRepository as _RR
            _res_repo = _RR()
            _media_repo = _MR()
            global_media = run_async(_media_repo.get_by_platform_id(platform_id))

            if global_media:
                cache_updates = {}
                # Only mark cached "completed" if both status AND file path exist
                has_video_path = bool(global_media.get("download_path"))
                has_cover_path = bool(global_media.get("cover_download_path"))
                has_music_path = bool(global_media.get("music_download_path"))
                if download_video and global_media.get("video_download_status") == "completed" and has_video_path:
                    cache_updates["video_download_status"] = "completed"
                if download_music and global_media.get("music_download_status") == "completed" and has_music_path:
                    cache_updates["music_download_status"] = "completed"
                if download_cover and global_media.get("cover_download_status") == "completed" and has_cover_path:
                    cache_updates["cover_download_status"] = "completed"
                if download_video and int(media_type) in (2, 68) and global_media.get("image_download_status") == "completed" and has_video_path:
                    cache_updates["image_download_status"] = "completed"

                if cache_updates:
                    run_async(_res_repo.update_download_status(resource_id, cache_updates))

                # If ALL requested types are cached (status + path), skip download entirely
                all_cached = True
                if download_video:
                    if int(media_type) in (2, 68):
                        all_cached = all_cached and global_media.get("image_download_status") == "completed" and has_video_path
                    else:
                        all_cached = all_cached and global_media.get("video_download_status") == "completed" and has_video_path
                if download_music:
                    all_cached = all_cached and global_media.get("music_download_status") == "completed" and has_music_path
                if download_cover:
                    all_cached = all_cached and global_media.get("cover_download_status") == "completed" and has_cover_path

                if all_cached:
                    logger.info(f"[Download/Done] All requested types cached for {platform_id}, skipping download")
                    tracker.complete()
                    if unified_task_id:
                        try:
                            run_async(tracker_unified.complete(unified_task_id))
                        except Exception:
                            pass
                    # Update resource file paths from global media
                    path_updates = {}
                    if global_media.get("download_path"):
                        path_updates["file_path"] = global_media["download_path"]
                    if global_media.get("cover_download_path"):
                        path_updates["cover_image_path"] = global_media["cover_download_path"]
                    if path_updates:
                        run_async(_res_repo.update_resource(resource_id, path_updates))
                    return {"status": "success", "platform_id": platform_id, "cache_hit": True}

        # ── Dispatch to strategy ──
        if url:
            results = _do_ytdlp_download(
                url=url,
                platform_id=platform_id,
                user_id=user_id,
                download_video=download_video,
                download_music=download_music,
                download_cover=download_cover,
                tracker=tracker,
            )
        else:
            results = _do_douyin_download(
                platform_id=platform_id,
                user_id=user_id,
                download_video=download_video,
                download_music=download_music,
                download_cover=download_cover,
                media_type=media_type,
                tracker=tracker,
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
                    run_async(tracker_unified.fail(unified_task_id, warn_msg))
                except Exception:
                    pass
        else:
            tracker.complete()
            if unified_task_id:
                try:
                    run_async(tracker_unified.complete(unified_task_id))
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

        # ── Update user resource download statuses based on actual results ──
        if resource_id:
            try:
                from app.repositories.media_repository import MediaRepository as _MR2
                from app.repositories.resources_repository import ResourcesRepository as _RR2
                _res_repo2 = _RR2()
                status_updates = {}
                path_updates = {}

                if download_video:
                    video_result = results.get("video")
                    if int(media_type) in (2, 68):
                        status_updates["image_download_status"] = video_result if video_result == "completed" else "failed"
                    else:
                        status_updates["video_download_status"] = video_result if video_result == "completed" else "failed"
                if download_music:
                    music_result = results.get("music")
                    status_updates["music_download_status"] = music_result if music_result == "completed" else "failed"
                if download_cover:
                    cover_result = results.get("cover")
                    status_updates["cover_download_status"] = cover_result if cover_result == "completed" else "failed"

                # Also update file paths on resource
                fresh_media = run_async(_MR2().get_by_platform_id(platform_id))
                if fresh_media:
                    if fresh_media.get("download_path"):
                        path_updates["file_path"] = fresh_media["download_path"]
                    if fresh_media.get("cover_download_path"):
                        path_updates["cover_image_path"] = fresh_media["cover_download_path"]

                logger.info(
                    f"[Download/DB] resource={resource_id}: "
                    f"status={status_updates}, paths={list(path_updates.keys())}"
                )
                run_async(_res_repo2.update_resource(resource_id, {**status_updates, **path_updates}))
            except Exception as e:
                logger.warning(f"[Download/DB] Failed to update resource status for {platform_id}: {e}")

        # Chain HLS transcode for video files
        _maybe_chain_transcode(platform_id, user_id)

        # Chain AI pipeline if user has auto-transcribe enabled
        _maybe_chain_ai_pipeline(platform_id, user_id)

        return {
            "status": "success",
            "platform_id": platform_id,
            "results": results,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(
            f"[Download/{strategy}] Failed: {platform_id}, error: {error_msg}"
        )

        # Update user resource status to failed
        if resource_id:
            try:
                from app.repositories.resources_repository import ResourcesRepository as _RR3
                _res_repo3 = _RR3()
                fail_updates = {}
                if download_video:
                    if int(media_type) in (2, 68):
                        fail_updates["image_download_status"] = "failed"
                    else:
                        fail_updates["video_download_status"] = "failed"
                if download_music:
                    fail_updates["music_download_status"] = "failed"
                if download_cover:
                    fail_updates["cover_download_status"] = "failed"
                run_async(_res_repo3.update_download_status(resource_id, fail_updates))
            except Exception:
                pass

        # Celery retry with exponential backoff — do NOT mark as failed yet
        if self.request.retries < self.max_retries:
            retry_num = self.request.retries + 1
            countdown = 30 * (2 ** self.request.retries)
            logger.info(
                f"[Download/{strategy}] Retry {retry_num}/{self.max_retries} "
                f"for {platform_id} in {countdown}s"
            )
            # Update unified task subtitle to show retry status (not failed)
            if unified_task_id:
                try:
                    run_async(tracker_unified.update_progress(
                        unified_task_id,
                        progress=0,
                        subtitle=f"Retrying ({retry_num}/{self.max_retries})...",
                    ))
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
                run_async(tracker_unified.fail(unified_task_id, error_msg))
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


