# app/tasks/signals.py

"""
Celery signal handlers — automatic UnifiedTaskManager lifecycle transitions.

Connects Celery's built-in task lifecycle signals to the unified phase-based
state machine.  When a task has ``_unified_task_id`` in its kwargs, the
signals automatically:

  - **prerun**: start() → PROCESSING, renew dedup lock
  - **success**: complete() → COMPLETED, notify subscribers, release lock
  - **failure**: fail() → FAILED, notify subscribers, release lock

All methods are idempotent — if the task body has already completed/failed
the row, the signal handler silently skips the duplicate transition.

Tasks without ``_unified_task_id`` in kwargs are unaffected (handlers return
early).  All handlers are wrapped in try/except so they never crash the
Celery worker.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from celery.signals import task_failure, task_prerun, task_success
from loguru import logger

# ─── Async helper ────────────────────────────────────────────────────


def _run_async(coro) -> Any:
    """Run an async coroutine from synchronous Celery signal context."""
    try:
        loop = asyncio.get_running_loop()
        return loop.run_until_complete(coro)
    except RuntimeError:
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

    Calls manager.start() (idempotent) and renews dedup lock.
    """
    try:
        unified_id = _get_unified_task_id(kwargs)
        if not unified_id:
            return

        from app.services.unified_task_manager import get_task_manager

        mgr = get_task_manager()
        _run_async(mgr.start(unified_id))
        logger.debug(
            f"[Signal:prerun] Task {task_id} -> PROCESSING "
            f"(unified_task={unified_id})"
        )

        dedup_key = _get_dedup_key(kwargs)
        if dedup_key:
            mgr.renew_lock(dedup_key)

    except Exception as exc:
        logger.error(
            f"[Signal:prerun] Error handling task {task_id}: {exc}",
            exc_info=True,
        )


# ─── Signal: task_success ────────────────────────────────────────────


@task_success.connect
def on_task_success(sender=None, result=None, **kw):
    """When a Celery task completes successfully.

    Calls manager.complete() (idempotent), notifies subscribers,
    and releases dedup lock.
    """
    try:
        task_kwargs = getattr(sender.request, "kwargs", None) or {}
        unified_id = _get_unified_task_id(task_kwargs)
        if not unified_id:
            return

        from app.services.unified_task_manager import get_task_manager

        mgr = get_task_manager()
        _run_async(mgr.complete(unified_id))
        logger.debug(
            f"[Signal:success] Task {sender.request.id} -> COMPLETED "
            f"(unified_task={unified_id})"
        )

        _run_async(mgr.notify_subscribers(unified_id, success=True))

        dedup_key = _get_dedup_key(task_kwargs)
        if dedup_key:
            mgr.release_lock(dedup_key)

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

    Classifies the error, calls manager.fail() (idempotent), notifies
    subscribers, and releases dedup lock.
    """
    try:
        task_kwargs = getattr(sender.request, "kwargs", None) or {}
        unified_id = _get_unified_task_id(task_kwargs)
        if not unified_id:
            return

        from app.services.unified_task_manager import get_task_manager

        mgr = get_task_manager()
        error_code = mgr.classify_error(exception) if exception else "UNKNOWN"
        error_msg = str(exception)[:500] if exception else "Unknown error"

        _run_async(mgr.fail(unified_id, error_msg, error_code=error_code))
        logger.warning(
            f"[Signal:failure] Task {task_id} -> FAILED "
            f"(unified_task={unified_id}, error_code={error_code})"
        )

        _run_async(
            mgr.notify_subscribers(unified_id, success=False, error_code=error_code)
        )

        dedup_key = _get_dedup_key(task_kwargs)
        if dedup_key:
            mgr.release_lock(dedup_key)

    except Exception as exc:
        logger.error(
            f"[Signal:failure] Error handling task {task_id}: {exc}",
            exc_info=True,
        )
