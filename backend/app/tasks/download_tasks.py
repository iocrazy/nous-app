# app/tasks/download_tasks.py

"""
Download Tasks Module

Contains async download tasks for video, image sets, music, and covers.
Integrates with TaskManager for task status tracking and automatic retries.
"""

import asyncio
import os

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

        mime = resource.get("mime_type") or ""
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
    """Progress tracker that writes to Redis (real-time) + UnifiedTaskManager (Supabase lifecycle).

    Real-time progress is published via Redis pub/sub to channel
    ``task_progress:{user_id}`` so the WebSocket endpoint can push it
    to connected clients without polling.
    """

    def __init__(self, task_id: str, redis_client,
                 unified_tracker=None, unified_task_id=None,
                 user_id: str | None = None):
        self.task_id = task_id
        self.redis = redis_client
        self.unified_tracker = unified_tracker
        self.unified_task_id = unified_task_id
        self.user_id = user_id
        self.last_update = 0
        self._last_downloaded = 0
        self._last_time = 0
        self._speed = 0.0
        # Stage-based progress mapping: maps raw download % to overall task %
        self._stage_offset = 0    # Start percentage for current stage
        self._stage_weight = 100  # Weight of current stage (percentage points)

    def set_stage(self, offset: int, weight: int):
        """Set stage boundaries for overall progress calculation.

        Args:
            offset: Start percentage for current stage (0-95)
            weight: Weight of current stage in percentage points
        """
        self._stage_offset = offset
        self._stage_weight = weight

    def _publish(self, payload: dict):
        """Publish a progress message to Redis pub/sub for WebSocket delivery."""
        if not self.user_id:
            return
        import json

        channel = f"task_progress:{self.user_id}"
        try:
            self.redis.publish(channel, json.dumps(payload))
        except Exception as e:
            logger.debug(f"[ProgressTracker] Redis publish failed: {e}")

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

        if total > 0:
            raw_percent = int((downloaded / total) * 100)
        else:
            # Content-Length unknown: simulate progress using downloaded bytes
            # Ramp up quickly then slow down (asymptotic approach to 90%)
            # e.g. 1MB→18%, 5MB→55%, 10MB→72%, 20MB→84%, 50MB→90%
            mb = downloaded / (1024 * 1024)
            raw_percent = min(int(90 * mb / (mb + 5)), 90) if mb > 0 else 0
        speed_str = self._format_speed(self._speed)

        # Write raw progress to Redis for legacy polling
        progress_data = {
            "percent": raw_percent,
            "downloaded": downloaded,
            "total": total,
            "speed": speed_str,
            "status": "downloading",
        }
        self.redis.setex(
            f"download_progress:{self.task_id}", 3600, json.dumps(progress_data)
        )

        # Map raw download percent to overall task progress using stage boundaries
        overall_percent = self._stage_offset + int(raw_percent * self._stage_weight / 100)
        overall_percent = min(max(overall_percent, 0), 99)  # Reserve 100 for explicit completion

        # Publish real-time progress via Redis pub/sub → WebSocket
        self._publish({
            "unified_task_id": self.unified_task_id,
            "celery_task_id": self.task_id,
            "status": "downloading",
            "percent": overall_percent,
            "speed": speed_str,
            "downloaded": downloaded,
            "total": total,
        })


    def _format_speed(self, bytes_per_sec: float) -> str:
        """Format speed as human readable string."""
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec:.0f} B/s"
        elif bytes_per_sec < 1024 * 1024:
            return f"{bytes_per_sec / 1024:.1f} KB/s"
        else:
            return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"

    def complete(self):
        """Mark download as complete in Redis and publish via pub/sub."""
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
        self._publish({
            "unified_task_id": self.unified_task_id,
            "celery_task_id": self.task_id,
            "status": "completed",
            "percent": 100,
            "speed": "0 B/s",
            "downloaded": 0,
            "total": 0,
        })

    def failed(self, error: str):
        """Mark download as failed in Redis and publish via pub/sub."""
        import json

        error_msg = error[:200] if error else "Unknown error"
        progress_data = {
            "percent": 0,
            "status": "failed",
            "error": error_msg,
        }
        self.redis.setex(
            f"download_progress:{self.task_id}", 300, json.dumps(progress_data)
        )
        self._publish({
            "unified_task_id": self.unified_task_id,
            "celery_task_id": self.task_id,
            "status": "failed",
            "percent": 0,
            "speed": "0 B/s",
            "downloaded": 0,
            "total": 0,
            "error": error_msg,
        })


# ─── Stage-based progress helper ──────────────────────────────────────


def _force_progress(tracker: UnifiedProgressTracker, progress: int, subtitle: str = None):
    """Force a progress update via Redis pub/sub at download stage boundaries.

    Used before/after video, music, cover downloads to ensure the user sees
    meaningful progress even when fine-grained streaming progress isn't
    available (e.g. music/cover downloads, or content-length=0).
    """
    clamped = min(max(progress, 0), 99)
    speed_str = tracker._format_speed(tracker._speed)
    tracker._publish({
        "unified_task_id": tracker.unified_task_id,
        "celery_task_id": tracker.task_id,
        "status": "downloading",
        "percent": clamped,
        "speed": speed_str,
        "downloaded": 0,
        "total": 0,
    })
    logger.debug(f"[Download/Progress] Stage update: {progress}% subtitle={subtitle}")


def _calc_stage_ranges(download_video: bool, download_cover: bool) -> dict:
    """Calculate progress ranges for each download stage.

    Returns dict mapping stage name to (offset, weight) tuple.
    Ranges span 0% to 95% (5% reserved for finalization).
    """
    raw_weights = {'video': 85, 'cover': 15}
    parts = []
    if download_video:
        parts.append('video')
    if download_cover:
        parts.append('cover')

    if not parts:
        return {}

    total_w = sum(raw_weights[p] for p in parts)
    ranges = {}
    offset = 0
    for p in parts:
        weight = int(raw_weights[p] * 95 / total_w)  # Scale to 95 points (0% to 95%)
        ranges[p] = (offset, weight)
        offset += weight

    return ranges


# ─── URL availability helpers ─────────────────────────────────────────


def _ensure_download_urls(platform_id: str, media: dict, needed_types: list[str]) -> dict:
    """Check if download URLs are available for needed types. Re-parse if missing.

    Mutates and returns the media dict with refreshed URLs if re-parsed.
    """
    url_fields = {
        "video": "video_download_urls",
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
        from app.services.ies_douyin_parser import IesDouyinParser
        from app.services.douyin_formatter import DouyinFormatter

        # --- Attempt 1: IesDouyinParser (fast HTTP, no browser) ---
        aweme_detail = run_async(IesDouyinParser.parse(original_url))
        parse_method = "LightHTTP"

        # If short URL failed, try directly with platform_id (bypass URL redirect)
        if not aweme_detail and platform_id:
            logger.info(f"[Download/URL] Short URL failed, trying platform_id directly: {platform_id}")
            aweme_detail = run_async(IesDouyinParser._fetch_share_page(platform_id))
            if aweme_detail:
                IesDouyinParser._process_video_urls(aweme_detail)
                parse_method = "LightHTTP-directID"

        if aweme_detail:
            new_parsed = run_async(DouyinFormatter.parse_aweme_detail(
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

        # --- Attempt 2: DrissionPageParser browser fallback (if still missing) ---
        if still_missing:
            logger.info(
                f"[Download/URL] LightHTTP still missing {still_missing}, "
                f"falling back to BrowserAuto for {platform_id}"
            )
            try:
                from app.services.drissionpage_parser import DrissionPageParser

                browser_detail = run_async(DrissionPageParser.fetch_one_video(original_url))
                if browser_detail:
                    parse_method = "BrowserAuto"
                    browser_parsed = run_async(DouyinFormatter.parse_aweme_detail(
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

        # Also update music_play_urls if re-parsed (carousel needs standalone music)
        if new_parsed.get("music_play_urls"):
            update_fields["music_play_urls"] = new_parsed["music_play_urls"]
            media["music_play_urls"] = new_parsed["music_play_urls"]
            logger.info(
                f"[Download/URL] Refreshed music_play_urls for {platform_id} "
                f"({len(new_parsed['music_play_urls'])} URLs, via {parse_method})"
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

    # Test current URLs (handle nested lists: [[url1, url2], [url3, url4]])
    fail_reason = ""
    for item in urls:
        # Nested list: item is [url1, url2, ...] — test first URL
        url = item[0] if isinstance(item, list) and item else item
        if not isinstance(url, str):
            continue
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

    for item in fresh_urls:
        url = item[0] if isinstance(item, list) and item else item
        if not isinstance(url, str):
            continue
        ok, reason = run_async(_check_url_accessible(url))
        if ok:
            logger.info(f"[Download/Validate] Fresh {type_key} URLs accessible for {platform_id}")
            return media, True, "ok"
        fail_reason = reason

    logger.warning(
        f"[Download/Validate] Fresh {type_key} URLs also inaccessible for {platform_id}: {fail_reason}"
    )
    return media, False, fail_reason


# ─── Audio extraction helper ─────────────────────────────────────────


def _extract_audio_from_video(platform_id: str) -> bool:
    """Extract audio from downloaded video using ffmpeg stream copy (zero-transcode).

    Looks up the video file path from DB, extracts audio to audio.m4a
    in the same directory, and updates music_download_path in DB.

    This is ~100x faster than downloading music separately via URL
    because it's a pure I/O operation with no network or re-encoding.

    Returns True on success, False on failure.
    """
    import subprocess

    from app.repositories.media_repository import MediaRepository as _MR_extract

    repo = _MR_extract()
    media = run_async(repo.get_by_platform_id(platform_id))
    if not media:
        logger.warning(f"[Audio/Extract] No media record for {platform_id}")
        return False

    video_rel_path = media.get("download_path")
    if not video_rel_path:
        logger.warning(f"[Audio/Extract] No download_path for {platform_id}")
        return False

    base_path = Utils.get_download_base_path()
    video_full_path = os.path.join(base_path, video_rel_path)

    if not os.path.exists(video_full_path):
        logger.warning(f"[Audio/Extract] Video file not found: {video_full_path}")
        return False

    output_dir = os.path.dirname(video_full_path)
    audio_full_path = os.path.join(output_dir, "audio.m4a")

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", video_full_path,
                "-vn",            # No video
                "-c:a", "copy",   # Copy audio codec (no re-encoding)
                audio_full_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,  # Should be < 1s for stream copy
        )

        if result.returncode != 0:
            logger.warning(
                f"[Audio/Extract] ffmpeg failed (rc={result.returncode}): "
                f"{result.stderr[:300]}"
            )
            return False

        if not os.path.exists(audio_full_path) or os.path.getsize(audio_full_path) == 0:
            logger.warning(f"[Audio/Extract] Output file missing or empty: {audio_full_path}")
            if os.path.exists(audio_full_path):
                os.remove(audio_full_path)
            return False

        file_size = os.path.getsize(audio_full_path)
        logger.info(
            f"[Audio/Extract] Success for {platform_id}: "
            f"{Utils.format_file_size(file_size)}"
        )

        # Calculate relative path for DB
        audio_rel_path = os.path.relpath(audio_full_path, base_path)

        # Update DB
        run_async(repo.update(platform_id, {
            "music_download_status": DownloadStatus.COMPLETED.value,
            "music_download_path": audio_rel_path,
        }))

        return True

    except subprocess.TimeoutExpired:
        logger.warning(f"[Audio/Extract] ffmpeg timed out for {platform_id}")
        return False
    except FileNotFoundError:
        logger.warning("[Audio/Extract] ffmpeg not found in PATH")
        return False
    except Exception as e:
        logger.warning(f"[Audio/Extract] Error for {platform_id}: {e}")
        return False


# ─── Internal download strategies ─────────────────────────────────────

def _do_douyin_download(
    platform_id: str,
    user_id: str,
    download_video: bool,
    download_cover: bool,
    media_type: int,
    tracker: UnifiedProgressTracker,
) -> dict:
    """Douyin download strategy: reads URLs from DB, downloads via httpx."""
    results = {"video": None, "music": None, "cover": None}

    # Calculate stage progress ranges
    stages = _calc_stage_ranges(download_video, download_cover)

    # Ensure download URLs are available (re-parse if missing)
    from app.repositories.media_repository import MediaRepository as _MR_urls
    media = run_async(_MR_urls().get_by_platform_id(platform_id))
    if media:
        needed = []
        if download_video:
            needed.append("image" if int(media_type) in (2, 68) else "video")
        if download_cover:
            needed.append("cover")
        media = _ensure_download_urls(platform_id, media, needed)
        # Diagnostic: log URL availability after ensure
        for t in needed:
            url_field = {"video": "video_download_urls",
                         "cover": "cover_urls", "image": "image_download_urls"}.get(t)
            urls = media.get(url_field) if url_field else None
            url_count = len(urls) if urls else 0
            logger.info(f"[Download/Diag] {t}: {url_count} URLs available for {platform_id} (field={url_field})")

    # ── Pre-download: validate URL accessibility, refresh if expired ──
    if int(media_type) in (0, 4, 61):  # Video types
        if download_video:
            # Set stage boundaries for fine-grained video progress
            if 'video' in stages:
                offset, weight = stages['video']
                tracker.set_stage(offset, weight)
                _force_progress(tracker, offset, subtitle="Downloading video...")

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

                # ── BrowserAuto fallback: re-parse via DrissionPage when httpx+yt-dlp both fail ──
                if results["video"] != "completed" and original_url:
                    logger.info(
                        f"[Download/Exec] video: httpx+yt-dlp both failed, trying BrowserAuto "
                        f"(DrissionPage) to get fresh URLs for {platform_id}"
                    )
                    try:
                        from app.services.drissionpage_parser import DrissionPageParser
                        from app.services.douyin_formatter import DouyinFormatter
                        from app.repositories.media_repository import (
                            MediaRepository as _MR_browser,
                        )

                        browser_detail = run_async(
                            DrissionPageParser.fetch_one_video(original_url)
                        )
                        if browser_detail:
                            browser_parsed = run_async(
                                DouyinFormatter.parse_aweme_detail(
                                    aweme_detail=browser_detail,
                                    valid_url=original_url,
                                    download_video=True,
                                    download_music=False,
                                    download_cover=False,
                                )
                            )
                            fresh_urls = (
                                browser_parsed.get("video_download_urls")
                                if browser_parsed
                                else None
                            )
                            if fresh_urls:
                                run_async(
                                    _MR_browser().update(
                                        platform_id,
                                        {"video_download_urls": fresh_urls},
                                    )
                                )
                                logger.info(
                                    f"[Download/Exec] video: BrowserAuto got {len(fresh_urls)} "
                                    f"fresh URLs, retrying httpx for {platform_id}"
                                )
                                video_result = run_async(
                                    DownloaderService.download_video_by_platform_id(
                                        platform_id,
                                        user_id=user_id,
                                        progress_tracker=tracker,
                                    )
                                )
                                results["video"] = (
                                    video_result.video_download_status.value
                                    if hasattr(video_result, "video_download_status")
                                    else "unknown"
                                )
                                if results["video"] == "completed":
                                    logger.success(
                                        f"[Download/Exec] video: BrowserAuto fallback "
                                        f"succeeded for {platform_id}"
                                    )
                                else:
                                    logger.warning(
                                        f"[Download/Exec] video: BrowserAuto fallback "
                                        f"download also failed for {platform_id}"
                                    )
                            else:
                                logger.warning(
                                    f"[Download/Exec] video: BrowserAuto parse yielded no "
                                    f"video URLs for {platform_id}"
                                )
                        else:
                            logger.warning(
                                f"[Download/Exec] video: BrowserAuto returned empty "
                                f"for {platform_id}"
                            )
                    except Exception as browser_err:
                        logger.warning(
                            f"[Download/Exec] video: BrowserAuto fallback error for "
                            f"{platform_id}: {type(browser_err).__name__}: {browser_err}"
                        )

            # Mark video stage complete
            if 'video' in stages:
                video_end = stages['video'][0] + stages['video'][1]
                _force_progress(tracker, video_end)

            # Auto-extract audio from downloaded video via ffmpeg (instant, no network)
            if results.get("video") == "completed":
                logger.info(f"[Download/Exec] music: extracting from video for {platform_id}...")
                if _extract_audio_from_video(platform_id):
                    results["music"] = DownloadStatus.COMPLETED.value
                    logger.info(f"[Download/Exec] music: extracted successfully for {platform_id}")
                else:
                    logger.warning(f"[Download/Exec] music: extraction failed for {platform_id}")
                    results["music"] = DownloadStatus.FAILED.value

    elif int(media_type) in (2, 68):  # Image types
        if download_video:  # "video" flag used for images too
            # Mark image stage start
            if 'video' in stages:
                offset, weight = stages['video']
                tracker.set_stage(offset, weight)
                _force_progress(tracker, offset, subtitle="Downloading images...")

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

                # Fallback: re-parse to get fresh image URLs and retry
                try:
                    from app.services.douyin_formatter import DouyinFormatter
                    from app.services.ies_douyin_parser import IesDouyinParser

                    logger.info(
                        f"[Download/Exec] image: re-parsing for fresh URLs {platform_id}"
                    )
                    aweme_detail = run_async(
                        IesDouyinParser._fetch_share_page(platform_id)
                    )
                    if aweme_detail:
                        IesDouyinParser._process_video_urls(aweme_detail)
                        new_parsed = run_async(
                            DouyinFormatter.parse_aweme_detail(
                                aweme_detail=aweme_detail,
                                valid_url=media.get("original_url", ""),
                                download_video=True,
                                download_music=False,
                                download_cover=False,
                            )
                        )
                        if new_parsed:
                            # Update DB with fresh URLs
                            update_fields = {}
                            for field in ("image_download_urls", "video_download_urls"):
                                if new_parsed.get(field):
                                    update_fields[field] = new_parsed[field]
                            if update_fields:
                                repo = MediaRepository()
                                run_async(repo.update(platform_id, update_fields))
                                logger.info(
                                    f"[Download/Exec] image: re-parsed {platform_id}, "
                                    f"updated {list(update_fields.keys())}"
                                )

                            # Retry download with fresh URLs
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
                            logger.info(
                                f"[Download/Exec] image retry: "
                                f"{results['video']} for {platform_id}"
                            )
                except Exception as reparse_err:
                    logger.warning(
                        f"[Download/Exec] image: re-parse fallback failed "
                        f"for {platform_id}: {reparse_err}"
                    )

            # Mark image stage complete
            if 'video' in stages:
                video_end = stages['video'][0] + stages['video'][1]
                _force_progress(tracker, video_end)

    if download_cover:
        # Mark cover stage start
        if 'cover' in stages:
            offset, weight = stages['cover']
            tracker.set_stage(offset, weight)
            _force_progress(tracker, offset, subtitle="Downloading cover...")

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

        # Mark cover stage complete
        if 'cover' in stages:
            cover_end = stages['cover'][0] + stages['cover'][1]
            _force_progress(tracker, cover_end)

    return results


def _do_ytdlp_download(
    url: str,
    platform_id: str,
    user_id: str,
    download_video: bool,
    download_cover: bool,
    tracker: UnifiedProgressTracker,
) -> dict:
    """yt-dlp download strategy: downloads via yt-dlp using original URL."""
    from app.repositories.media_repository import MediaRepository
    from app.services.url_router import URLRouter
    from app.services.ytdlp_service import YtdlpService

    results = {"video": None, "music": None, "cover": None}

    # Calculate stage progress ranges
    stages = _calc_stage_ranges(download_video, download_cover)

    detected_platform, _ = URLRouter.detect_platform(url)
    repo = MediaRepository()
    media = run_async(repo.get_by_platform_id(platform_id))
    media_id = str(media["id"]) if media else platform_id  # fallback to platform_id
    storage_dir, relative_prefix = Utils.create_web_resource_path(
        detected_platform, media_id
    )

    if download_video:
        # Mark video stage start
        if 'video' in stages:
            offset, weight = stages['video']
            tracker.set_stage(offset, weight)
            _force_progress(tracker, offset, subtitle="Downloading video...")

        logger.info(f"[Download/Exec] video: downloading via yt-dlp {platform_id}...")

        async def on_progress(downloaded: int, total: int, speed: str):
            await tracker.update(downloaded, total)

        result = run_async(
            YtdlpService.download_video(
                url, str(storage_dir), platform_id, progress_callback=on_progress
            )
        )
        if result.get("file_path"):
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

        # Mark video stage complete
        if 'video' in stages:
            video_end = stages['video'][0] + stages['video'][1]
            _force_progress(tracker, video_end)

        # Auto-extract audio from downloaded video via ffmpeg (instant, no network)
        if results.get("video") == DownloadStatus.COMPLETED.value:
            logger.info(f"[Download/Exec] music: extracting from video for {platform_id}...")
            if _extract_audio_from_video(platform_id):
                results["music"] = DownloadStatus.COMPLETED.value
                logger.info(f"[Download/Exec] music: extracted successfully for {platform_id}")
            else:
                logger.warning(f"[Download/Exec] music: extraction failed for {platform_id}")
                results["music"] = DownloadStatus.FAILED.value

    if download_cover:
        # Mark cover stage start
        if 'cover' in stages:
            offset, weight = stages['cover']
            tracker.set_stage(offset, weight)
            _force_progress(tracker, offset, subtitle="Downloading cover...")

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

        # Mark cover stage complete
        if 'cover' in stages:
            cover_end = stages['cover'][0] + stages['cover'][1]
            _force_progress(tracker, cover_end)

    return results


# ─── Unified download task ─────────────────────────────────────────────

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
    _dedup_key: str = None,       # Orchestrator dedup key
    _unified_task_id: str = None,  # Orchestrator task ID (for signals)
    download_music: bool = False,  # Deprecated, kept for backward compat with queued tasks
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
    requested_types = [t for t, f in [("video", download_video), ("cover", download_cover)] if f]
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
            unified_task_id = run_async(manager.create(
                user_id=user_id,
                task_type="download",
                title=f"Download {video_title or platform_id}",
                subtitle=dl_subtitle,
                media_id=platform_id,
                celery_task_id=task_id,
                dedup_key=_dedup_key,
            ))
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
            from app.repositories.resources_repository import ResourcesRepository as _RR
            _res_repo = _RR()
            _media_repo = _MR()
            global_media = run_async(_media_repo.get_by_platform_id(platform_id))

            if global_media:
                cache_updates = {}
                # Only mark cached "completed" if both status AND file path exist
                has_video_path = bool(global_media.get("download_path"))
                has_cover_path = bool(global_media.get("cover_download_path"))
                if download_video and global_media.get("video_download_status") == "completed" and has_video_path:
                    cache_updates["video_download_status"] = "completed"
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
                if download_cover:
                    all_cached = all_cached and global_media.get("cover_download_status") == "completed" and has_cover_path

                if all_cached:
                    logger.info(f"[Download/Done] All requested types cached for {platform_id}, skipping download")
                    tracker.complete()
                    if unified_task_id:
                        try:
                            run_async(manager.complete(unified_task_id))
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
                download_cover=download_cover,
                tracker=tracker,
            )
        else:
            results = _do_douyin_download(
                platform_id=platform_id,
                user_id=user_id,
                download_video=download_video,
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
                    # Audio extraction result (auto-extracted from video)
                    music_result = results.get("music")
                    if music_result:
                        status_updates["music_download_status"] = music_result
                if download_cover:
                    cover_result = results.get("cover")
                    status_updates["cover_download_status"] = cover_result if cover_result == "completed" else "failed"

                # Also update file paths and file size on resource
                actual_size = 0
                fresh_media = run_async(_MR2().get_by_platform_id(platform_id))
                if fresh_media:
                    if fresh_media.get("download_path"):
                        path_updates["file_path"] = fresh_media["download_path"]
                    if fresh_media.get("cover_download_path"):
                        path_updates["cover_image_path"] = fresh_media["cover_download_path"]
                    # Backfill file_size_bytes from actual downloaded size
                    actual_size = fresh_media.get("storage_size") or fresh_media.get("datasize_bytes") or 0
                    if actual_size > 0:
                        path_updates["file_size_bytes"] = actual_size

                logger.info(
                    f"[Download/DB] resource={resource_id}: "
                    f"status={status_updates}, paths={list(path_updates.keys())}"
                )
                run_async(_res_repo2.update_resource(resource_id, {**status_updates, **path_updates}))

                # Ensure resource_version v1 exists (downloads don't create it)
                try:
                    existing_versions = run_async(_res_repo2.get_versions(resource_id))
                    if not existing_versions and path_updates.get("file_path"):
                        file_path = path_updates["file_path"]
                        filename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
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
                        logger.info(f"[Download/DB] Created resource_version v1 for resource={resource_id}")
                    elif existing_versions and actual_size > 0:
                        # Backfill file_size_bytes on existing versions
                        for ver in existing_versions:
                            if not ver.get("file_size_bytes"):
                                run_async(_res_repo2.update_version(
                                    ver["id"], {"file_size_bytes": actual_size}
                                ))
                except Exception as ve:
                    logger.warning(f"[Download/DB] Failed to ensure resource_version for {resource_id}: {ve}")

                # Fallback: also ensure parsed_media status is in sync
                # (mark_media_as_downloaded inside DownloaderService may have silently failed)
                pm_status_updates = {
                    k: v for k, v in status_updates.items()
                    if v == "completed"
                }
                if pm_status_updates:
                    _mr2 = _MR2()
                    current_pm = fresh_media or run_async(_mr2.get_by_platform_id(platform_id))
                    if current_pm:
                        needs_update = {
                            k: v for k, v in pm_status_updates.items()
                            if current_pm.get(k) != "completed"
                        }
                        if needs_update:
                            logger.info(
                                f"[Download/DB] parsed_media fallback update for {platform_id}: {needs_update}"
                            )
                            run_async(_mr2.update(platform_id, needs_update))
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
                    run_async(manager.update_progress(
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


