"""DBOS Orchestrator — central place that owns DBOS instance + routing decisions.

Responsibilities:
    - Initialize the DBOS singleton from `DBOS_DATABASE_URL` env var
    - Read & cache the `dbos_workflow_routing` table at startup; expose a
      `get_routing(task_type)` helper for the celery / shadow / dbos decision
    - Provide `start_workflow_routed(task_type, payload)` — the canonical
      dispatcher that backend code calls instead of `task.delay(...)`. Looks
      up routing, then either:
        * 'celery' → enqueue Celery task only (legacy)
        * 'shadow' → enqueue Celery (canonical) + DBOS workflow (parallel,
          output captured for comparison, not used)
        * 'dbos'   → start DBOS workflow only

This service is deliberately **not** mandatory — existing call sites can keep
using `celery_task.delay(...)` directly during the migration. New code paths
(handlers being PR-D6'd, agent dispatchers being PR-D5'd) call the routed
dispatcher.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from loguru import logger

from app.db import get_async_supabase_admin

# DBOS instance — set by `init_dbos`; None until lifespan startup runs.
_dbos = None
_routing_cache: dict[str, str] = {}
_routing_loaded_at: float = 0.0
_routing_refresh_interval_s: float = 60.0  # poll dbos_workflow_routing every 60s


@dataclass(frozen=True)
class RoutingDecision:
    task_type: str
    mode: str  # 'celery' | 'shadow' | 'dbos'
    notes: Optional[str] = None


async def _refresh_routing_cache() -> None:
    """Reload dbos_workflow_routing into the in-process cache."""
    global _routing_cache, _routing_loaded_at
    try:
        client = await get_async_supabase_admin()
        result = (
            await client.table("dbos_workflow_routing")
            .select("task_type, mode")
            .execute()
        )
        rows = result.data or []
        _routing_cache = {row["task_type"]: row["mode"] for row in rows}
        _routing_loaded_at = asyncio.get_event_loop().time()
        logger.info(f"[dbos] routing cache refreshed: {len(_routing_cache)} entries")
    except Exception as e:
        logger.warning(
            f"[dbos] routing cache refresh failed: {e!r} — keeping previous cache"
        )


async def get_routing(task_type: str) -> RoutingDecision:
    """Look up routing for a task_type. Defaults to 'celery' if unknown."""
    now = asyncio.get_event_loop().time()
    if not _routing_cache or (now - _routing_loaded_at) > _routing_refresh_interval_s:
        await _refresh_routing_cache()
    mode = _routing_cache.get(task_type, "celery")
    return RoutingDecision(task_type=task_type, mode=mode)


def init_dbos() -> None:
    """Initialize the DBOS singleton (idempotent). Called from FastAPI lifespan
    BEFORE workflow modules are imported (decorators register against the
    singleton at import time).
    """
    global _dbos
    if _dbos is not None:
        return

    db_url = os.environ.get("DBOS_DATABASE_URL")
    if not db_url:
        logger.warning("[dbos] DBOS_DATABASE_URL not set; DBOS orchestrator disabled")
        return

    from dbos import DBOS, DBOSConfig

    # Co-locate DBOS sys tables (workflow_status, operation_outputs,
    # workflow_events, etc.) in the SAME database as the app, in the
    # `dbos` schema. Default behaviour creates a separate
    # `<db>_dbos_sys` database, which would prevent Supabase Realtime
    # from broadcasting workflow_status changes (Realtime only
    # publishes from the configured app DB). Co-locating lets us add
    # `dbos.workflow_status` to supabase_realtime publication and
    # have the frontend Task Center subscribe natively.
    cfg: DBOSConfig = {
        "name": "mediahub",
        "application_database_url": db_url,
        "system_database_url": db_url,
    }
    _dbos = DBOS(config=cfg)
    logger.info("[dbos] singleton instantiated (sys + app share same DB)")


def launch_dbos() -> None:
    """Start the DBOS worker pool + run pending-workflow recovery. Call AFTER
    all `@DBOS.workflow` modules have been imported.
    """
    if _dbos is None:
        return
    from dbos import DBOS

    DBOS.launch()
    logger.info("[dbos] launched (worker pool started, recovery complete)")


def shutdown_dbos() -> None:
    """Drain the DBOS worker pool. Called from FastAPI lifespan teardown."""
    if _dbos is None:
        return
    from dbos import DBOS

    try:
        DBOS.destroy()
        logger.info("[dbos] destroyed")
    except Exception as e:
        logger.warning(f"[dbos] destroy raised {e!r}")


def is_enabled() -> bool:
    return _dbos is not None


async def start_workflow_routed(
    task_type: str,
    *,
    dbos_workflow_callable: Optional[Callable[..., Any]] = None,
    dbos_workflow_kwargs: Optional[dict[str, Any]] = None,
    celery_dispatch: Optional[Callable[[], Any]] = None,
    workflow_id: Optional[str] = None,
) -> dict[str, Any]:
    """Route a task by `task_type` per dbos_workflow_routing config.

    Args:
        task_type: matched against routing table
        dbos_workflow_callable: the @DBOS.workflow function to invoke
        dbos_workflow_kwargs: kwargs passed to the workflow
        celery_dispatch: callable that does the celery `.delay(...)` and returns
            the AsyncResult (or whatever the legacy code path expects)
        workflow_id: optional explicit DBOS workflow id (default: server-generated)

    Returns:
        dict with keys: mode, dbos_workflow_id (if applicable), celery_task_id
        (if applicable). Caller can persist to issues.dbos_workflow_id.
    """
    decision = await get_routing(task_type)
    out: dict[str, Any] = {"mode": decision.mode, "task_type": task_type}

    if decision.mode in ("celery", "shadow") and celery_dispatch is not None:
        try:
            celery_result = celery_dispatch()
            out["celery_task_id"] = getattr(celery_result, "id", None)
        except Exception as e:
            logger.error(f"[dbos] celery dispatch failed for {task_type}: {e!r}")
            raise

    if (
        decision.mode in ("shadow", "dbos")
        and dbos_workflow_callable is not None
        and is_enabled()
    ):
        from contextlib import nullcontext

        from dbos import DBOS, DBOSContextSetAuth, SetWorkflowID

        kwargs = dbos_workflow_kwargs or {}
        # Set authenticated_user on the DBOS workflow_status row so
        # GET /api/v1/workflows can filter by user. Requires user_id
        # in workflow kwargs.
        user_id = kwargs.get("user_id")
        auth_ctx = (
            DBOSContextSetAuth(user=user_id, roles=[]) if user_id else nullcontext()
        )
        try:
            with auth_ctx:
                if workflow_id:
                    with SetWorkflowID(workflow_id):
                        handle = DBOS.start_workflow(dbos_workflow_callable, **kwargs)
                else:
                    handle = DBOS.start_workflow(dbos_workflow_callable, **kwargs)
            out["dbos_workflow_id"] = handle.workflow_id
            if decision.mode == "shadow":
                logger.info(
                    f"[dbos][shadow] task_type={task_type} celery={out.get('celery_task_id')} "
                    f"dbos_wf={handle.workflow_id} (DBOS output discarded for comparison only)"
                )
        except Exception as e:
            # Shadow mode: never let DBOS failure break the canonical celery path
            if decision.mode == "shadow":
                logger.warning(
                    f"[dbos][shadow] DBOS dispatch failed for {task_type}: {e!r}"
                )
            else:
                raise

    return out
