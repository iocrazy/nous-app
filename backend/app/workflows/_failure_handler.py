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
            from app.services.task_tracking_manager import get_task_manager

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

    # Sprint 2 #2: emit lifecycle event so listeners (Discord notify,
    # Sentry, agent_runs writer, Realtime push) can react without this
    # handler needing to know about each one. Emitter ignorance =
    # decoupling. The bus listener-exception isolation contract means
    # a buggy listener can't crash the failure handler that emitted it.
    try:
        _emit_lifecycle_workflow_fail(workflow_id, err_type, err_msg, ctx)
    except Exception as e:
        logger.debug(f"[workflow.fail] lifecycle emit failed: {e}")

    return {
        "status": "failed",
        "error_type": err_type,
        "error": err_msg,
        **ctx,
    }


def _emit_lifecycle_workflow_fail(
    workflow_id: Optional[str],
    err_type: str,
    err_msg: str,
    ctx: dict[str, Any],
) -> None:
    """Best-effort fire of EVT_WORKFLOW_FAIL on app.state.lifecycle_bus.

    The bus is held on FastAPI app.state per the lifespan setup. Workflow
    code runs in DBOS context which doesn't carry the FastAPI app, so we
    look it up via the global accessor when available.

    Failure here is silent — emit is observability, not load-bearing.
    The actual failure recording (mgr.fail above) already happened.
    """
    try:
        from app.agent_framework import EVT_WORKFLOW_FAIL, LifecycleEvent
        from app.main import app as _app  # late import — avoid cycle at module load

        bus = getattr(_app.state, "lifecycle_bus", None)
        if bus is None:
            return

        async def _do() -> None:
            await bus.emit(
                LifecycleEvent(
                    type=EVT_WORKFLOW_FAIL,
                    payload={
                        "workflow_id": workflow_id,
                        "error_type": err_type,
                        "error_msg": err_msg,
                        "context": ctx,
                    },
                )
            )

        asyncio.run(_do())
    except (ImportError, RuntimeError):
        # ImportError: agent_framework or app.main not yet importable
        # (cold start race). RuntimeError: no running loop / asyncio.run
        # called from inside a running loop. Swallow either silently —
        # we already recorded the failure.
        pass
