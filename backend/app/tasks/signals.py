# app/tasks/signals.py

"""
Celery signal handlers for automatic TaskOrchestrator phase transitions.

Connects Celery's built-in task lifecycle signals to the phase-based state
machine in TaskOrchestrator. When a task has ``_unified_task_id`` in its
kwargs, the signals automatically:

  - **prerun**: transition QUEUED/DEDUP_CHECK -> PROCESSING, renew dedup lock
  - **success**: transition -> COMPLETED, notify subscribers, release lock
  - **failure**: classify error, transition -> FAILED, notify subscribers, release lock

Tasks without ``_unified_task_id`` in kwargs are unaffected (handlers return early).

All handlers are wrapped in try/except so they never crash the Celery worker.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from celery.signals import task_failure, task_prerun, task_success
from loguru import logger


# ─── Async helper ────────────────────────────────────────────────────

def _run_async(coro) -> Any:
    """Run an async coroutine from synchronous Celery signal context.

    Tries ``get_running_loop().run_until_complete()`` first (for cases
    where we are inside an existing event loop). Falls back to
    ``asyncio.run()`` which creates a fresh loop.
    """
    try:
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(coro)
    except RuntimeError:
        # No running loop — create one
        return asyncio.run(coro)


# ─── Kwargs extraction helpers ───────────────────────────────────────

def _get_unified_task_id(kwargs: dict) -> Optional[str]:
    """Extract ``_unified_task_id`` from task kwargs, or None."""
    return kwargs.get("_unified_task_id") if kwargs else None


def _get_dedup_key(kwargs: dict) -> Optional[str]:
    """Extract ``_dedup_key`` from task kwargs, or None."""
    return kwargs.get("_dedup_key") if kwargs else None


# ─── Signal: task_prerun ─────────────────────────────────────────────

@task_prerun.connect
def on_task_prerun(sender=None, task_id=None, args=None, kwargs=None, **kw):
    """When a Celery worker starts executing a task.

    If the task carries ``_unified_task_id`` in kwargs:
      1. Transition the unified_task to PROCESSING phase.
      2. Renew the dedup lock TTL (if ``_dedup_key`` is present).
    """
    try:
        unified_id = _get_unified_task_id(kwargs)
        if not unified_id:
            return

        # Lazy imports to avoid circular dependencies at module level
        from app.services.task_orchestrator import TaskPhase, get_orchestrator

        orch = get_orchestrator()

        # Transition to PROCESSING
        _run_async(orch.transition(unified_id, TaskPhase.PROCESSING))
        logger.debug(
            f"[Signal:prerun] Task {task_id} -> PROCESSING "
            f"(unified_task={unified_id})"
        )

        # Renew dedup lock if present
        dedup_key = _get_dedup_key(kwargs)
        if dedup_key:
            orch.renew_lock(dedup_key)

    except Exception as exc:
        logger.error(
            f"[Signal:prerun] Error handling task {task_id}: {exc}",
            exc_info=True,
        )


# ─── Signal: task_success ────────────────────────────────────────────

@task_success.connect
def on_task_success(sender=None, result=None, **kw):
    """When a Celery task completes successfully.

    If the task carries ``_unified_task_id`` in kwargs:
      1. Transition the unified_task to COMPLETED phase.
      2. Notify all dedup subscribers (fan-out results).
      3. Release the dedup lock.
    """
    try:
        task_kwargs = getattr(sender.request, "kwargs", None) or {}
        unified_id = _get_unified_task_id(task_kwargs)
        if not unified_id:
            return

        from app.services.task_orchestrator import TaskPhase, get_orchestrator

        orch = get_orchestrator()

        # Transition to COMPLETED
        _run_async(orch.transition(unified_id, TaskPhase.COMPLETED))
        logger.debug(
            f"[Signal:success] Task {sender.request.id} -> COMPLETED "
            f"(unified_task={unified_id})"
        )

        # Notify subscribers
        _run_async(orch.notify_subscribers(unified_id, success=True))

        # Release dedup lock
        dedup_key = _get_dedup_key(task_kwargs)
        if dedup_key:
            orch.release_lock(dedup_key)

    except Exception as exc:
        logger.error(
            f"[Signal:success] Error handling task "
            f"{getattr(sender, 'name', '?')}: {exc}",
            exc_info=True,
        )


# ─── Signal: task_failure ────────────────────────────────────────────

@task_failure.connect
def on_task_failure(sender=None, task_id=None, exception=None, **kw):
    """When a Celery task fails with an exception.

    If the task carries ``_unified_task_id`` in kwargs:
      1. Classify the exception into a structured error code.
      2. Transition the unified_task to FAILED phase (with error_code).
      3. Notify all dedup subscribers of the failure.
      4. Release the dedup lock.
    """
    try:
        task_kwargs = getattr(sender.request, "kwargs", None) or {}
        unified_id = _get_unified_task_id(task_kwargs)
        if not unified_id:
            return

        from app.services.task_orchestrator import TaskPhase, get_orchestrator

        orch = get_orchestrator()

        # Classify error
        error_code = orch.classify_error(exception) if exception else "UNKNOWN"

        # Transition to FAILED
        _run_async(
            orch.transition(unified_id, TaskPhase.FAILED, error_code=error_code)
        )
        logger.warning(
            f"[Signal:failure] Task {task_id} -> FAILED "
            f"(unified_task={unified_id}, error_code={error_code})"
        )

        # Notify subscribers
        _run_async(
            orch.notify_subscribers(
                unified_id, success=False, error_code=error_code
            )
        )

        # Release dedup lock
        dedup_key = _get_dedup_key(task_kwargs)
        if dedup_key:
            orch.release_lock(dedup_key)

    except Exception as exc:
        logger.error(
            f"[Signal:failure] Error handling task {task_id}: {exc}",
            exc_info=True,
        )
