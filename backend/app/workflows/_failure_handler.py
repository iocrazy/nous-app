"""Shared workflow-level exception handler.

Why this exists: when a `@DBOS.step` exhausts its retry budget DBOS
raises ``DBOSMaxStepRetriesExceeded`` to the workflow body. If the
workflow body doesn't catch it, the exception propagates to DBOS's
internal worker thread — which can damage the host process (DBOS runs
in-process with FastAPI/uvicorn, unlike Celery's separated worker).

This helper gives every workflow a one-line tail-catch that:
  1. Marks the corresponding ``task_tracking`` row as failed
     (status='failed', error_msg, completed_at) so the frontend's
     Realtime channel pushes the failure to the user instead of leaving
     a forever-pending card.
  2. Returns a uniform failure dict so callers (chain_followups,
     trigger_*) get a recognisable shape instead of an exception.
  3. Logs the error with full context for post-mortem.

Use:
    @DBOS.workflow()
    def my_workflow(...):
        try:
            ... # original body
        except Exception as e:  # noqa: BLE001
            return record_workflow_failure(
                workflow_id=DBOS.workflow_id,
                error=e,
                context={"k": "v", ...},
            )
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger


def record_workflow_failure(
    *,
    workflow_id: Optional[str],
    error: BaseException,
    context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Mark task_tracking failed + return uniform failure dict. Never raises."""
    err_type = type(error).__name__
    err_msg = str(error)[:500] or err_type
    ctx = context or {}

    logger.exception(
        f"[workflow.fail] wf={workflow_id} type={err_type} ctx={ctx}"
    )

    if workflow_id:
        try:
            from app.services.unified_task_manager import get_task_manager

            mgr = get_task_manager()

            async def _do() -> None:
                try:
                    await mgr.fail(
                        workflow_id,
                        error_msg=f"{err_type}: {err_msg}",
                        error_code=err_type,
                    )
                except Exception as inner:
                    # mgr.fail is itself idempotent / no-op when the row
                    # is already terminal — but if it raises (network,
                    # auth) we still don't want to bring the workflow
                    # down trying to record the failure.
                    logger.warning(
                        f"[workflow.fail] task_tracking.fail({workflow_id}) "
                        f"raised: {inner!r}"
                    )

            asyncio.run(_do())
        except Exception as outer:
            logger.warning(
                f"[workflow.fail] failed to schedule task_tracking update: {outer!r}"
            )

    return {
        "status": "failed",
        "error_type": err_type,
        "error": err_msg,
        **ctx,
    }
