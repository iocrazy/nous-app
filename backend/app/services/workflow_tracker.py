"""
workflow_tracker — bridge DBOS workflow lifecycle to unified_tasks.

Phase 3b learning: DBOS records workflow status durably in its own
sys-DB (`dbos.workflow_status`), but our frontend Task Center reads
`unified_tasks` (and Supabase Realtime is wired to that table). With
no glue, frontend never sees a workflow flip past "pending" — and
the reaper later kills it as a zombie.

This module provides `@tracked_workflow` to decorate workflows so
they automatically upsert a unified_tasks row at start and flip its
status at completion / failure. Writes go through `@DBOS.step` so
they're durable + idempotent on workflow recovery.

Usage:

    @DBOS.workflow()
    @tracked_workflow(
        task_type="parse",
        title_fn=lambda kw: f"Parse {(kw.get('url') or '')[:50]}",
    )
    def parse_workflow(url, user_id, *, video_bool=True, ...):
        ...

The router still writes a `pending` row at dispatch (for instant UI
feedback). The decorator UPSERTs on `celery_task_id` (= DBOS
workflow_id) so the two writers merge cleanly.
"""

from __future__ import annotations

import functools
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from dbos import DBOS
from loguru import logger

from app.tasks.utils import run_async


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@DBOS.step()
def _mark_started_step(
    wf_id: str,
    task_type: str,
    title: str,
    user_id: Optional[str],
) -> None:
    """UPSERT unified_tasks → status=running. Survives workflow recovery."""
    from app.db.supabase_client import get_async_supabase_admin

    async def _do() -> None:
        try:
            sb = await get_async_supabase_admin()
            now = _now_utc_iso()
            # Router pre-creates the row at dispatch with status=
            # pending. We just flip it to running. If the row is
            # absent (e.g. workflow recovered after a crash without
            # router context), this UPDATE silently no-ops — the
            # workflow still runs, we just don't surface it in the
            # Task Center until D8 (when DBOS workflow_status replaces
            # unified_tasks entirely).
            await (
                sb.table("unified_tasks")
                .update(
                    {
                        "status": "running",
                        "phase": "processing",
                        "started_at": now,
                        "updated_at": now,
                    }
                )
                .eq("celery_task_id", wf_id)
                .execute()
            )
        except Exception as e:
            logger.warning(f"[tracker] mark_started wf={wf_id[:8]}: {e}")

    run_async(_do())


@DBOS.step()
def _mark_completed_step(wf_id: str) -> None:
    """UPDATE unified_tasks → status=completed."""
    from app.db.supabase_client import get_async_supabase_admin

    async def _do() -> None:
        try:
            sb = await get_async_supabase_admin()
            now = _now_utc_iso()
            await (
                sb.table("unified_tasks")
                .update(
                    {
                        "status": "completed",
                        "phase": "completed",
                        "completed_at": now,
                        "updated_at": now,
                        "progress": 100,
                    }
                )
                .eq("celery_task_id", wf_id)
                .execute()
            )
        except Exception as e:
            logger.warning(f"[tracker] mark_completed wf={wf_id[:8]}: {e}")

    run_async(_do())


@DBOS.step()
def _mark_failed_step(wf_id: str, error_msg: str) -> None:
    """UPDATE unified_tasks → status=failed."""
    from app.db.supabase_client import get_async_supabase_admin

    async def _do() -> None:
        try:
            sb = await get_async_supabase_admin()
            now = _now_utc_iso()
            await (
                sb.table("unified_tasks")
                .update(
                    {
                        "status": "failed",
                        "phase": "failed",
                        "error_msg": (error_msg or "")[:500],
                        "updated_at": now,
                    }
                )
                .eq("celery_task_id", wf_id)
                .execute()
            )
        except Exception as e:
            logger.warning(f"[tracker] mark_failed wf={wf_id[:8]}: {e}")

    run_async(_do())


def tracked_workflow(
    *,
    task_type: str,
    title_fn: Optional[Callable[[dict[str, Any]], str]] = None,
):
    """Wrap a DBOS workflow so it auto-syncs to unified_tasks.

    Decorator order matters — `@DBOS.workflow()` MUST be the outermost
    decorator so DBOS workflow context is established before our
    wrapper runs (we need DBOS.workflow_id to identify the row):

        @DBOS.workflow()
        @tracked_workflow(...)
        def my_workflow(...): ...

    Args:
        task_type: Goes into unified_tasks.task_type — frontend uses
            this to pick an icon / route.
        title_fn: Optional callable receiving the workflow's kwargs
            and returning a human-readable title (e.g. "Parse <url>").
            Defaults to "<workflow_name>(<wf_id_short>)".
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                wf_id = DBOS.workflow_id
            except Exception:
                # Outside DBOS workflow context — treat as plain call,
                # no tracking. Useful for unit tests / direct invocation.
                return fn(*args, **kwargs)

            user_id = kwargs.get("user_id")
            try:
                title = title_fn(kwargs) if title_fn else f"{fn.__name__}({wf_id[:8]})"
            except Exception:
                title = f"{fn.__name__}({wf_id[:8]})"

            _mark_started_step(wf_id, task_type, title, user_id)
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                _mark_failed_step(wf_id, str(exc))
                raise
            _mark_completed_step(wf_id)
            return result

        return wrapper

    return decorator
