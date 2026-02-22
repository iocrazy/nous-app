# app/tasks/transcode_tasks.py

"""
HLS Transcode Celery Tasks

Async task for transcoding video resources to multi-bitrate HLS.
Triggered after upload or parser download for video/* mime types.
"""

from celery import shared_task
from loguru import logger

from app.tasks.utils import run_async


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def transcode_to_hls(self, resource_id: str, version_id: str, user_id: str = None, _dedup_key: str = None):
    """
    Celery task: transcode a resource version to HLS multi-bitrate.

    Args:
        resource_id: Resource UUID
        version_id: ResourceVersion UUID
        user_id: Optional user ID for unified task tracking
        _dedup_key: Optional dedup key for orchestrator signal handlers.

    Returns:
        dict with status and hls_path
    """
    # Store orchestrator keys in request for signal handlers
    self.request.kwargs = getattr(self.request, 'kwargs', {}) or {}
    self.request.kwargs['_dedup_key'] = _dedup_key

    task_id = self.request.id
    logger.info(f"[Transcode] Starting HLS transcode: resource={resource_id}, version={version_id}")

    # ── Unified task tracking ──
    unified_task_id = None
    if user_id:
        try:
            from app.services.task_tracker import get_task_tracker
            tracker = get_task_tracker()
            unified_task_id = run_async(tracker.create(
                user_id=user_id,
                task_type="transcode",
                title=f"HLS Transcode",
                subtitle=version_id[:8],
                resource_id=resource_id,
                celery_task_id=task_id,
            ))
            run_async(tracker.start(unified_task_id))
            self.request.kwargs['_unified_task_id'] = unified_task_id
        except Exception as e:
            logger.warning(f"[Transcode] Unified tracker create failed: {e}")

    try:
        from app.services.transcode_service import TranscodeService

        svc = TranscodeService()
        hls_path = run_async(svc.transcode_version(resource_id, version_id))

        if hls_path:
            logger.success(f"[Transcode] Completed: {resource_id} → {hls_path}")
            if unified_task_id:
                try:
                    from app.services.task_tracker import get_task_tracker
                    run_async(get_task_tracker().complete(unified_task_id))
                except Exception:
                    pass
            return {
                "status": "completed",
                "resource_id": resource_id,
                "version_id": version_id,
                "hls_path": hls_path,
            }
        else:
            logger.warning(f"[Transcode] Failed for resource={resource_id}, version={version_id}")
            if unified_task_id:
                try:
                    from app.services.task_tracker import get_task_tracker
                    run_async(get_task_tracker().fail(unified_task_id, "Transcode returned no output"))
                except Exception:
                    pass
            return {
                "status": "failed",
                "resource_id": resource_id,
                "version_id": version_id,
            }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Transcode] Error: resource={resource_id}, error={error_msg}")

        if self.request.retries < self.max_retries:
            countdown = 60 * (2 ** self.request.retries)
            logger.info(
                f"[Transcode] Retry {self.request.retries + 1}/{self.max_retries} "
                f"in {countdown}s for {resource_id}"
            )
            raise self.retry(exc=e, countdown=countdown)

        # Mark as failed after max retries
        if unified_task_id:
            try:
                from app.services.task_tracker import get_task_tracker
                run_async(get_task_tracker().fail(unified_task_id, error_msg[:500]))
            except Exception:
                pass

        try:
            from app.repositories.resources_repository import ResourcesRepository
            repo = ResourcesRepository()
            run_async(repo.update_version(version_id, {"transcode_status": "failed"}))
        except Exception:
            pass

        return {
            "status": "failed",
            "resource_id": resource_id,
            "version_id": version_id,
            "error": error_msg,
        }


def maybe_trigger_transcode(resource_id: str, version_id: str, mime_type: str, user_id: str = None):
    """
    Helper: trigger HLS transcoding if the file is a video.

    Call this after upload or download completion.
    """
    if not mime_type or not mime_type.startswith("video/"):
        return

    # ── Orchestrator dedup check ──
    dedup_key = None
    try:
        from app.services.task_orchestrator import get_orchestrator
        orchestrator = get_orchestrator()
        result = run_async(orchestrator.acquire_or_subscribe(
            task_type="transcode",
            dedup_identifier=version_id,
            user_id=user_id or "",
            resource_id=resource_id,
        ))
        dedup_key = result.get("dedup_key")

        if result["action"] in ("subscribed", "completed"):
            logger.info(f"[Transcode] Dedup hit for version {version_id}: {result['action']}")
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
            f"[Transcode] Queued HLS transcode: resource={resource_id}, version={version_id}"
        )
    except Exception as e:
        logger.warning(f"[Transcode] Failed to queue transcode for {resource_id}: {e}")
