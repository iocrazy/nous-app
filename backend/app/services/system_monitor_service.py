# backend/app/services/system_monitor_service.py

"""
System Monitor Service

Shared logic for system metrics collection.
Used by both the API endpoint (on-demand) and Celery Beat (periodic push to Supabase).
"""

import json
import os
import shutil
import time

from loguru import logger

from app.core.config import settings

# ---------------------------------------------------------------------------
# Internal cache for Celery inspect calls (avoid hammering the broker)
# ---------------------------------------------------------------------------
_queue_cache: dict = {"data": None, "timestamp": 0, "ttl": 5}

# Critical tasks that MUST be registered for downloads to work
_CRITICAL_TASKS = {
    "app.tasks.download_tasks.download_unified_task",
    "app.tasks.parse_tasks.parse_single_link_task",
}


def check_worker_ready(
    task_name: str = "app.tasks.download_tasks.download_unified_task",
) -> tuple[bool, str]:
    """Worker readiness probe — used to be a Celery inspect ping.

    PR-D7 phase 3: Celery is gone. DBOS workers are in-process with
    the FastAPI server, so if this code is running the worker pool is
    online by definition. Returns ready=True unconditionally; the
    `task_name` arg is kept for caller signature compatibility but
    no longer queried.
    """
    from app.services import dbos_orchestrator

    if not dbos_orchestrator.is_enabled():
        return False, "DBOS not enabled — workflow dispatch unavailable"
    return True, ""


def _format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    elif bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    else:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"


def _parse_speed(speed_str: str) -> float:
    try:
        parts = speed_str.split()
        if len(parts) != 2:
            return 0
        value = float(parts[0])
        unit = parts[1].lower()
        if "kb" in unit:
            return value * 1024
        elif "mb" in unit:
            return value * 1024 * 1024
        elif "gb" in unit:
            return value * 1024 * 1024 * 1024
        return value
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Public helpers — all return plain dicts (JSON-serializable)
# ---------------------------------------------------------------------------


async def get_queue_status() -> dict:
    """Return DBOS workflow queue metrics (5-second cache).

    PR-D7: Celery introspection replaced with DBOS workflow_status
    table aggregation. Status values:
    - "offline" — DBOS not enabled (defensive; should never happen in
      production since lifespan launches DBOS at startup)
    - "online"  — DBOS launched + queue counts available

    Async because DBOS.list_workflows() (sync) refuses to run inside an
    asyncio event loop and FastAPI handlers always have one.
    """
    current_time = time.time()

    if (
        _queue_cache["data"]
        and (current_time - _queue_cache["timestamp"]) < _queue_cache["ttl"]
    ):
        return _queue_cache["data"]

    from app.services import dbos_orchestrator

    if not dbos_orchestrator.is_enabled():
        result = {"active": 0, "pending": 0, "scheduled": 0, "status": "offline"}
        _queue_cache.update(data=result, timestamp=current_time)
        return result

    try:
        from dbos import DBOS

        running = await DBOS.list_workflows_async(status="RUNNING") or []
        pending = await DBOS.list_workflows_async(status="PENDING") or []
        enqueued = await DBOS.list_workflows_async(status="ENQUEUED") or []

        result = {
            "active": len(running),
            "pending": len(pending) + len(enqueued),
            "scheduled": 0,
            "status": "online",
        }
        _queue_cache.update(data=result, timestamp=current_time)
        return result
    except Exception as e:
        logger.opt(exception=True).warning(f"get_queue_status failed: {e}")
        result = {"active": 0, "pending": 0, "scheduled": 0, "status": "offline"}
        _queue_cache.update(data=result, timestamp=current_time)
        return result


def get_storage_status() -> dict:
    """Return disk usage for the configured download path."""
    try:
        storage_path = settings.DOWNLOAD_PATH

        if not os.path.exists(storage_path):
            return {
                "total_bytes": 0,
                "used_bytes": 0,
                "free_bytes": 0,
                "percent_used": 0,
                "status": "error",
                "path": storage_path,
            }

        usage = shutil.disk_usage(storage_path)
        percent_used = (usage.used / usage.total) * 100 if usage.total > 0 else 0

        if percent_used >= 95:
            status = "critical"
        elif percent_used >= 85:
            status = "warning"
        else:
            status = "ok"

        return {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "percent_used": round(percent_used, 1),
            "status": status,
            "path": storage_path,
        }

    except Exception as e:
        logger.opt(exception=True).warning(f"get_storage_status failed: {e}")
        return {
            "total_bytes": 0,
            "used_bytes": 0,
            "free_bytes": 0,
            "percent_used": 0,
            "status": "error",
            "path": "",
        }


def get_network_status() -> dict:
    """Return download speed from Redis progress keys."""
    try:
        from app.core.redis import get_sync_redis

        redis_client = get_sync_redis()
        progress_keys = redis_client.keys("download_progress:*")

        total_speed = 0
        active_downloads = 0

        for key in progress_keys:
            try:
                data = redis_client.get(key)
                if data:
                    progress = json.loads(data)
                    if progress.get("status") == "downloading":
                        active_downloads += 1
                        speed_str = progress.get("speed", "0 B/s")
                        total_speed += _parse_speed(speed_str)
            except Exception:
                continue

        if active_downloads > 0:
            return {"speed": _format_speed(total_speed), "status": "active"}
        return {"speed": "0 B/s", "status": "idle"}

    except Exception as e:
        logger.opt(exception=True).warning(f"get_network_status failed: {e}")
        return {"speed": "0 B/s", "status": "error"}


async def get_worker_stats() -> list[dict]:
    """Return DBOS worker pool info. PR-D7: replaces Celery worker
    inspection. DBOS workers are in-process; we report a single
    synthetic 'in-process' worker entry so the admin UI keeps a
    consistent shape."""
    from app.services import dbos_orchestrator

    if not dbos_orchestrator.is_enabled():
        return []
    try:
        from dbos import DBOS

        return [
            {
                "name": "dbos@local",
                "status": "online",
                "concurrency": 8,  # matches WORKFORCE_QUEUE_CONCURRENCY default
                "processes": [],
                "total_tasks": {
                    "running": len(
                        await DBOS.list_workflows_async(status="RUNNING") or []
                    )
                },
            }
        ]
    except Exception as e:
        logger.opt(exception=True).warning(f"get_worker_stats failed: {e}")
        return []


async def get_active_tasks() -> list[dict]:
    """Return list of currently RUNNING DBOS workflows. Mirrors the
    legacy Celery active_tasks shape so the admin TaskCenter UI keeps
    rendering."""
    from app.services import dbos_orchestrator

    if not dbos_orchestrator.is_enabled():
        return []
    try:
        from dbos import DBOS

        running = await DBOS.list_workflows_async(status="RUNNING") or []
        return [
            {
                "task_id": getattr(w, "workflow_id", None)
                or getattr(w, "workflow_uuid", None),
                "name": getattr(w, "name", "") or "",
                "status": "active",
                "worker": "dbos@local",
                "args": [],
            }
            for w in running
        ]
    except Exception as e:
        logger.opt(exception=True).warning(f"get_active_tasks failed: {e}")
        return []
