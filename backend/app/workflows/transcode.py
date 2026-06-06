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
    - task_tracking bridge → D4 (TaskCenter UI subscribes to DBOS
      workflow status directly, not the legacy task_tracking rows)
    - dispatch site (`maybe_trigger_transcode` in transcode_tasks.py)
      keeps its gating logic and just swaps the .delay() call for
      start_workflow_routed("transcode", ...) — that swap is a D4
      wiring change, not part of this port
"""

from __future__ import annotations

import json
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

from app.db.scope import Scope, request_scope, system_request_scope


@DBOS.step()
async def resolve_resource_title_step(resource_id: str, version_id: str) -> str:
    """Cheap lookup for log/audit messages. Falls back to first 8 chars
    of version_id when filename isn't available."""
    from app.repositories.resources_repository import get_resources_repository

    repo = get_resources_repository()
    try:
        resource = await repo.get_resource_by_id(resource_id)
        if resource and resource.get("filename"):
            return resource["filename"]
    except Exception:
        pass
    return version_id[:8]


@DBOS.step(retries_allowed=True, max_attempts=3)
async def transcode_to_hls_step(
    resource_id: str,
    version_id: str,
    user_id: Optional[str],
) -> dict[str, Any]:
    """Run TranscodeService.transcode_version. Idempotent at the
    workflow_id level (same id replays cached result); per-attempt
    retry handles transient ffmpeg/IO errors. Publishes progress to
    `task_progress:{user_id}` for the WebSocket layer."""
    from app.services.media.transcode.transcode_service import TranscodeService

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

    result = await svc.transcode_version(
        resource_id, version_id, on_progress=on_progress
    )
    # Service now returns {"status": completed|skipped|failed, "hls_path"?,
    # "reason"?}. Pass through the status; tag the IDs for downstream.
    return {
        **result,
        "resource_id": resource_id,
        "version_id": version_id,
    }


@DBOS.step()
async def log_transcode_outcome_step(
    *,
    user_id: Optional[str],
    resource_title: str,
    resource_id: str,
    version_id: str,
    outcome: dict[str, Any],
) -> None:
    """Audit row in user_logs. Skipped outcomes (small file, transcode
    disabled, no applicable tiers) are NOT logged — they were quiet
    no-ops that don't deserve a "Transcode failed" entry in Activity
    Logs. Real failures still write user_logs + flip
    resources_versions.transcode_status='failed' so the UI surfaces a
    Retry button. Best-effort — never raises."""
    from app.repositories.resources_repository import get_resources_repository
    from app.repositories.user_logs_repository import log_user_action

    status = outcome.get("status")

    if status == "completed":
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

    if status == "skipped":
        # Quiet skip — the service already wrote transcode_status='skipped'
        # on the version row. Don't pollute Activity Logs with a failure
        # entry the user shouldn't have to triage.
        logger.info(
            f"[transcode] version={version_id} skipped: "
            f"{outcome.get('reason', 'unknown')}"
        )
        return

    # failed
    try:
        repo = get_resources_repository()
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
                    "error": str(outcome.get("reason", ""))[:200],
                },
            )
        except Exception as e:
            logger.warning(f"[transcode] log_user_action on failure: {e}")


@DBOS.workflow()
async def transcode_workflow(
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
    # Establish the ambient tenant Scope for every awaited step (they touch
    # resources / resource_versions via get_resources_repository(); native-async
    # @DBOS.step bodies share this task's ContextVar — no run_async hop here, so
    # the wrap propagates directly into each step). user_id is a frozen workflow
    # input, so a DBOS replay reconstructs the identical scope deterministically:
    #   - on-behalf-of-user transcode (user_id truthy) → USER scope
    #   - batch/admin transcode (user_id falsy)        → SYSTEM (owner-agnostic)
    # INERT until SCOPE_ENFORCE_RESOURCES — the choke point ignores _scope today.
    scope_cm = (
        request_scope(Scope(user_id=user_id))
        if user_id
        else system_request_scope(reason="system-transcode")
    )
    async with scope_cm:
        title = await resolve_resource_title_step(resource_id, version_id)
        outcome = await transcode_to_hls_step(resource_id, version_id, user_id)
        await log_transcode_outcome_step(
            user_id=user_id,
            resource_title=title,
            resource_id=resource_id,
            version_id=version_id,
            outcome=outcome,
        )
    return outcome
