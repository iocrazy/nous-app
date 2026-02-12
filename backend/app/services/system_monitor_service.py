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


def get_queue_status() -> dict:
    """Return Celery queue metrics (with 5-second cache)."""
    current_time = time.time()

    if (
        _queue_cache["data"]
        and (current_time - _queue_cache["timestamp"]) < _queue_cache["ttl"]
    ):
        return _queue_cache["data"]

    try:
        from app.celery_app import celery_app

        inspect = celery_app.control.inspect(timeout=0.5)
        ping = inspect.ping()

        if not ping:
            result = {"active": 0, "pending": 0, "scheduled": 0, "status": "offline"}
            _queue_cache.update(data=result, timestamp=current_time)
            return result

        active = inspect.active() or {}
        reserved = inspect.reserved() or {}

        active_count = sum(len(tasks) for tasks in active.values())
        pending_count = sum(len(tasks) for tasks in reserved.values())

        result = {
            "active": active_count,
            "pending": pending_count,
            "scheduled": 0,
            "status": "online",
        }
        _queue_cache.update(data=result, timestamp=current_time)
        return result

    except Exception as e:
        logger.warning(f"get_queue_status failed: {e}")
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
        logger.warning(f"get_storage_status failed: {e}")
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
        from app.celery_app import celery_app

        redis_client = celery_app.backend.client
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
        logger.warning(f"get_network_status failed: {e}")
        return {"speed": "0 B/s", "status": "error"}


def get_worker_stats() -> list[dict]:
    """Return list of Celery worker info dicts."""
    try:
        from app.celery_app import celery_app

        inspect = celery_app.control.inspect(timeout=1.0)
        ping = inspect.ping() or {}
        stats = inspect.stats() or {}

        workers = []
        for worker_name in ping:
            worker_stats = stats.get(worker_name, {})
            pool = worker_stats.get("pool", {})
            workers.append(
                {
                    "name": worker_name,
                    "status": "online",
                    "concurrency": pool.get("max-concurrency", 0),
                    "processes": pool.get("processes", []),
                    "total_tasks": worker_stats.get("total", {}),
                }
            )

        return workers

    except Exception as e:
        logger.warning(f"get_worker_stats failed: {e}")
        return []


def get_active_tasks() -> list[dict]:
    """Return list of currently active Celery tasks."""
    try:
        from app.celery_app import celery_app

        inspect = celery_app.control.inspect(timeout=0.5)
        active = inspect.active() or {}

        tasks = []
        for worker_name, worker_tasks in active.items():
            for task in worker_tasks:
                tasks.append(
                    {
                        "task_id": task.get("id", ""),
                        "name": task.get("name", ""),
                        "status": "active",
                        "worker": worker_name,
                        "args": task.get("args", []),
                    }
                )

        return tasks

    except Exception as e:
        logger.warning(f"get_active_tasks failed: {e}")
        return []
