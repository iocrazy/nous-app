"""transcode DBOS workflow — port of `transcode_tasks.transcode_to_hls`.

The legacy file is 427 LOC but most of it is unified_task_manager
lifecycle glue + Celery retry boilerplate that DBOS handles natively:
    - workflow_id memoization replaces the orchestrator dedup lock
      (`acquire_or_subscribe` / `_dedup_key` plumbing)
    - DBOS @step retries replace `self.retry(countdown=60 * 2**n)`
    - workflow status events replace start/complete/fail glue

What we keep:
    - delegation to TranscodeService.transcode_version (the actual work)
    - Redis pub/sub progress callback (the WebSocket consumer
      subscribes to `task_progress:{user_id}` for real-time bar updates)
    - user_logs row on success/failure (audit trail)
    - resources.transcode_status = 'failed' on terminal failure

What's deferred:
    - unified_tasks bridge → D4 (TaskCenter UI subscribes to DBOS
      workflow status directly, not the legacy unified_tasks rows)
    - dispatch site (`maybe_trigger_transcode` in transcode_tasks.py)
      keeps its gating logic and just swaps the .delay() call for
      start_workflow_routed("transcode", ...) — that swap is a D4
      wiring change, not part of this port
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from dbos import DBOS
from loguru import logger


@DBOS.step()
def resolve_resource_title_step(resource_id: str, version_id: str) -> str:
    """Cheap lookup for log/audit messages. Falls back to first 8 chars
    of version_id when filename isn't available."""
    from app.repositories.resources_repository import ResourcesRepository

    async def _do() -> str:
        repo = ResourcesRepository()
        try:
            resource = await repo.get_resource_by_id(resource_id)
            if resource and resource.get("filename"):
                return resource["filename"]
        except Exception:
            pass
        return version_id[:8]

    return asyncio.run(_do())


@DBOS.step(retries_allowed=True, max_attempts=3)
def transcode_to_hls_step(
    resource_id: str,
    version_id: str,
    user_id: Optional[str],
) -> dict[str, Any]:
    """Run TranscodeService.transcode_version. Idempotent at the
    workflow_id level (same id replays cached result); per-attempt
    retry handles transient ffmpeg/IO errors. Publishes progress to
    `task_progress:{user_id}` for the WebSocket layer."""
    from app.services.transcode_service import TranscodeService

    async def _do() -> Optional[str]:
        svc = TranscodeService()

        # Build progress callback only if we have a user channel to publish to.
        on_progress = None
        if user_id:
            from app.core.redis import get_sync_redis

            _redis = get_sync_redis()
            _channel = f"task_progress:{user_id}"

            async def _report_progress(progress: int, subtitle: str = "") -> None:
                pct = min(max(int(progress), 0), 99)
                try:
                    _redis.publish(
                        _channel,
                        json.dumps(
                            {
                                "workflow_kind": "transcode",
                                "version_id": version_id,
                                "status": "transcoding",
                                "percent": pct,
                                "subtitle": subtitle,
                            }
                        ),
                    )
                except Exception:
                    pass

            on_progress = _report_progress

        return await svc.transcode_version(
            resource_id, version_id, on_progress=on_progress
        )

    hls_path = asyncio.run(_do())
    if hls_path:
        return {
            "status": "completed",
            "resource_id": resource_id,
            "version_id": version_id,
            "hls_path": hls_path,
        }
    return {
        "status": "failed",
        "resource_id": resource_id,
        "version_id": version_id,
        "error": "Transcode returned no output",
    }


@DBOS.step()
def log_transcode_outcome_step(
    *,
    user_id: Optional[str],
    resource_title: str,
    resource_id: str,
    version_id: str,
    outcome: dict[str, Any],
) -> None:
    """Audit row in user_logs + on terminal failure flip
    resources_versions.transcode_status='failed' so the UI surfaces a
    Retry button. Best-effort — never raises."""
    from app.repositories.resources_repository import ResourcesRepository
    from app.repositories.user_logs_repository import log_user_action

    async def _do() -> None:
        if outcome["status"] == "completed":
            if user_id:
                try:
                    await log_user_action(
                        user_id=user_id,
                        action="transcode",
                        message=f"Transcode completed: {resource_title[:50]}...",
                        status="success",
                        details={
                            "resource_id": resource_id,
                            "version_id": version_id,
                            "hls_path": outcome.get("hls_path"),
                        },
                    )
                except Exception as e:
                    logger.warning(f"[transcode] log_user_action: {e}")
            return

        # failed
        try:
            repo = ResourcesRepository()
            await repo.update_version(version_id, {"transcode_status": "failed"})
        except Exception as e:
            logger.warning(f"[transcode] update_version on failure: {e}")
        if user_id:
            try:
                await log_user_action(
                    user_id=user_id,
                    action="transcode",
                    message=f"Transcode failed: {resource_title[:50]}...",
                    status="error",
                    details={
                        "resource_id": resource_id,
                        "version_id": version_id,
                        "error": str(outcome.get("error", ""))[:200],
                    },
                )
            except Exception as e:
                logger.warning(f"[transcode] log_user_action on failure: {e}")

    asyncio.run(_do())


@DBOS.workflow()
def transcode_workflow(
    resource_id: str,
    version_id: str,
    *,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """DBOS port of transcode_to_hls.

    Recommended workflow_id: f"transcode-{version_id}" so re-dispatching
    the same version short-circuits to the cached output (replaces the
    legacy `acquire_or_subscribe` dedup pattern).
    """
    title = resolve_resource_title_step(resource_id, version_id)
    outcome = transcode_to_hls_step(resource_id, version_id, user_id)
    log_transcode_outcome_step(
        user_id=user_id,
        resource_title=title,
        resource_id=resource_id,
        version_id=version_id,
        outcome=outcome,
    )
    return outcome
