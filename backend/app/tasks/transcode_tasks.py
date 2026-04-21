# app/tasks/transcode_tasks.py

"""
HLS Transcode Celery Tasks

Async task for transcoding video resources to multi-bitrate HLS.
Triggered after upload or parser download for video/* mime types.
"""

import json
import subprocess
from pathlib import Path
from typing import Optional

from celery import shared_task
from loguru import logger

from app.core.config import settings
from app.repositories.user_logs_repository import log_user_action
from app.tasks.utils import run_async


# ── Gating thresholds ──
MIN_SIZE_MB = None  # Read from settings.TRANSCODE_MIN_SIZE_MB at runtime
MIN_DURATION_SEC = 600  # ... or > 10 minutes


def _probe_codec_sync(filepath: str) -> Optional[str]:
    """Quick ffprobe for video codec name (sync, for gating context)."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-select_streams",
                "v:0",
                filepath,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            info = json.loads(result.stdout)
            for stream in info.get("streams", []):
                return stream.get("codec_name")
    except Exception as e:
        logger.debug(f"[Transcode] ffprobe codec failed for {filepath}: {e}")
    return None


def _probe_duration_sync(filepath: str) -> Optional[float]:
    """Quick ffprobe to get duration in seconds (sync, for Celery context)."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                filepath,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            info = json.loads(result.stdout)
            dur = info.get("format", {}).get("duration")
            return float(dur) if dur else None
    except Exception as e:
        logger.debug(f"[Transcode] ffprobe duration failed for {filepath}: {e}")
    return None


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=7200,
    time_limit=7500,
)
def transcode_to_hls(
    self,
    resource_id: str,
    version_id: str,
    user_id: str = None,
    _dedup_key: str = None,
    _unified_task_id: str = None,
):
    """
    Celery task: transcode a resource version to HLS multi-bitrate.

    Args:
        resource_id: Resource UUID
        version_id: ResourceVersion UUID
        user_id: Optional user ID for unified task tracking
        _dedup_key: Optional dedup key for task manager signal handlers.
        _unified_task_id: Optional existing unified_task ID (for retry, skip creating new).

    Returns:
        dict with status and hls_path
    """

    task_id = self.request.id
    logger.info(
        f"[Transcode] Starting HLS transcode: resource={resource_id}, version={version_id}"
    )

    # ── Resolve resource filename for task title ──
    resource_title = version_id[:8]
    try:
        from app.repositories.resources_repository import ResourcesRepository

        repo = ResourcesRepository()
        resource = run_async(repo.get_resource_by_id(resource_id))
        if resource and resource.get("filename"):
            resource_title = resource["filename"]
    except Exception:
        pass

    # ── Unified task tracking ──
    unified_task_id = _unified_task_id
    if user_id:
        try:
            from app.services.unified_task_manager import get_task_manager

            tracker = get_task_manager()
            if unified_task_id:
                # Retry: reuse existing task, just mark as started
                run_async(tracker.start(unified_task_id))
            else:
                # New task: create unified_task record
                unified_task_id = run_async(
                    tracker.create(
                        user_id=user_id,
                        task_type="transcode",
                        title=f"Transcode {resource_title}",
                        subtitle="Preparing...",
                        resource_id=resource_id,
                        celery_task_id=task_id,
                        metadata={"version_id": version_id},
                        dedup_key=_dedup_key,
                    )
                )
                run_async(tracker.start(unified_task_id))
        except Exception as e:
            logger.warning(f"[Transcode] Unified tracker create failed: {e}")

    try:
        from app.services.transcode_service import TranscodeService

        svc = TranscodeService()

        # Build progress callback — Redis pub/sub for real-time, NOT Supabase
        on_progress = None
        if unified_task_id and user_id:
            import json as _json
            import time as _time
            from app.core.redis import get_sync_redis

            _redis = get_sync_redis()
            _channel = f"task_progress:{user_id}"
            _last_pct = [0]

            async def _report_progress(progress: int, subtitle: str = ""):
                pct = min(max(int(progress), 0), 99)
                # Publish to Redis for WebSocket delivery (every update)
                try:
                    _redis.publish(
                        _channel,
                        _json.dumps(
                            {
                                "unified_task_id": unified_task_id,
                                "celery_task_id": task_id,
                                "status": "transcoding",
                                "percent": pct,
                                "speed": "",
                                "subtitle": subtitle,
                            }
                        ),
                    )
                except Exception:
                    pass
                _last_pct[0] = pct

            on_progress = _report_progress

        hls_path = run_async(
            svc.transcode_version(resource_id, version_id, on_progress=on_progress)
        )

        if hls_path:
            logger.success(f"[Transcode] Completed: {resource_id} → {hls_path}")
            if unified_task_id:
                try:
                    from app.services.unified_task_manager import get_task_manager

                    run_async(get_task_manager().complete(unified_task_id))
                except Exception:
                    pass
            # Log success
            if user_id:
                run_async(
                    log_user_action(
                        user_id=user_id,
                        action="transcode",
                        message=f"Transcode completed: {resource_title[:50]}...",
                        status="success",
                        details={
                            "resource_id": resource_id,
                            "version_id": version_id,
                            "hls_path": hls_path,
                        },
                    )
                )
            return {
                "status": "completed",
                "resource_id": resource_id,
                "version_id": version_id,
                "hls_path": hls_path,
            }
        else:
            logger.warning(
                f"[Transcode] Failed for resource={resource_id}, version={version_id}"
            )
            if unified_task_id:
                try:
                    from app.services.unified_task_manager import get_task_manager

                    run_async(
                        get_task_manager().fail(
                            unified_task_id, "Transcode returned no output"
                        )
                    )
                except Exception:
                    pass
            # Log failure
            if user_id:
                run_async(
                    log_user_action(
                        user_id=user_id,
                        action="transcode",
                        message=f"Transcode failed: {resource_title[:50]}...",
                        status="error",
                        details={
                            "resource_id": resource_id,
                            "version_id": version_id,
                            "error": "Transcode returned no output",
                        },
                    )
                )
            return {
                "status": "failed",
                "resource_id": resource_id,
                "version_id": version_id,
            }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Transcode] Error: resource={resource_id}, error={error_msg}")

        if self.request.retries < self.max_retries:
            countdown = 60 * (2**self.request.retries)
            logger.info(
                f"[Transcode] Retry {self.request.retries + 1}/{self.max_retries} "
                f"in {countdown}s for {resource_id}"
            )
            raise self.retry(
                exc=e,
                countdown=countdown,
                kwargs={
                    "resource_id": resource_id,
                    "version_id": version_id,
                    "user_id": user_id,
                    "_dedup_key": _dedup_key,
                    "_unified_task_id": unified_task_id,
                },
            )

        # Mark as failed after max retries
        if unified_task_id:
            try:
                from app.services.unified_task_manager import get_task_manager

                run_async(get_task_manager().fail(unified_task_id, error_msg[:500]))
            except Exception:
                pass

        try:
            from app.repositories.resources_repository import ResourcesRepository

            repo = ResourcesRepository()
            run_async(repo.update_version(version_id, {"transcode_status": "failed"}))
        except Exception:
            pass

        # Log failure after max retries
        if user_id:
            run_async(
                log_user_action(
                    user_id=user_id,
                    action="transcode",
                    message=f"Transcode failed: {resource_title[:50]}...",
                    status="error",
                    details={
                        "resource_id": resource_id,
                        "version_id": version_id,
                        "error": error_msg[:200],
                    },
                )
            )

        return {
            "status": "failed",
            "resource_id": resource_id,
            "version_id": version_id,
            "error": error_msg,
        }


def maybe_trigger_transcode(
    resource_id: str,
    version_id: str,
    mime_type: str,
    user_id: str = None,
    force: bool = False,
):
    """
    Helper: trigger HLS transcoding if the file is a qualifying video.

    Args:
        force: Skip size/duration gating and dedup (for manual retry / batch).
    """
    if not mime_type or not mime_type.startswith("video/"):
        return

    # ── Size / duration gating (skip when force=True) ──
    if not force:
        try:
            from app.repositories.resources_repository import ResourcesRepository

            repo = ResourcesRepository()
            version = run_async(repo.get_version_by_id(version_id))
            if not version or not version.get("file_path"):
                logger.info(f"[Transcode] Skip: no file_path for version {version_id}")
                return

            file_path = Path(settings.DOWNLOAD_PATH) / version["file_path"]
            if not file_path.exists():
                logger.info(f"[Transcode] Skip: file not found {file_path}")
                return

            file_size_mb = file_path.stat().st_size / (1024 * 1024)

            # Check minimum file size (applies to ALL codecs, including H.264)
            video_codec = _probe_codec_sync(str(file_path))
            is_h264 = video_codec in ("h264",)
            min_size = settings.TRANSCODE_MIN_SIZE_MB

            if file_size_mb < min_size:
                logger.info(
                    f"[Transcode] Skip: {video_codec} too small "
                    f"({file_size_mb:.0f}MB < {min_size}MB) "
                    f"for version {version_id}"
                )
                return

            if is_h264:
                logger.info(
                    f"[Transcode] H.264 detected — fast segment mode "
                    f"({file_size_mb:.0f}MB) for version {version_id}"
                )

                logger.info(
                    f"[Transcode] Gating passed: {file_size_mb:.0f}MB, "
                    f"{duration_sec or '?'}s — version {version_id}"
                )
        except Exception as e:
            logger.warning(f"[Transcode] Gating check failed, proceeding: {e}")

    # ── Orchestrator dedup check (skip when force=True) ──
    dedup_key = None
    if not force:
        try:
            from app.services.unified_task_manager import get_task_manager

            mgr = get_task_manager()
            result = run_async(
                mgr.acquire_or_subscribe(
                    task_type="transcode",
                    dedup_identifier=version_id,
                    user_id=user_id or "",
                    resource_id=resource_id,
                )
            )
            dedup_key = result.get("dedup_key")

            if result["action"] in ("subscribed", "completed"):
                logger.info(
                    f"[Transcode] Dedup hit for version {version_id}: {result['action']}"
                )
                return
        except Exception as e:
            logger.warning(f"[Transcode] Dedup check failed, proceeding normally: {e}")

    try:
        # Mark as pending first
        from app.repositories.resources_repository import ResourcesRepository

        repo = ResourcesRepository()
        run_async(repo.update_version(version_id, {"transcode_status": "pending"}))

        # Dispatch Celery task
        transcode_to_hls.delay(resource_id, version_id, user_id, _dedup_key=dedup_key)
        logger.info(
            f"[Transcode] Queued HLS transcode: resource={resource_id}, "
            f"version={version_id}, force={force}"
        )
    except Exception as e:
        logger.warning(f"[Transcode] Failed to queue transcode for {resource_id}: {e}")
