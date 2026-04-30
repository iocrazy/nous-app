"""Scheduled health workflows — port of two monitoring jobs from
`tasks.scheduled_tasks`.

  - update_system_status (every 30s) — upsert single-row system_status
    table for Realtime broadcast to admin dashboard
  - health_check         (hourly)     — multi-component liveness check

The 30s cadence is preserved using 6-field cron ("*/30 * * * * *");
DBOS croniter is initialized with second_at_beginning=True so seconds
are honored.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dbos import DBOS
from loguru import logger


@DBOS.step()
async def collect_system_status_step() -> dict[str, Any]:
    """Snapshot queue/storage/network/workers/active_tasks; upsert
    single-row system_status. The fixed UUID id matches the legacy
    Celery task so the Realtime channel doesn't double up.

    Async because get_queue_status now awaits DBOS.list_workflows_async
    (the sync DBOS API refuses to run in an event-loop context)."""
    from app.db.supabase_client import get_async_supabase_admin
    from app.services.system_monitor_service import (
        get_active_tasks,
        get_network_status,
        get_queue_status,
        get_storage_status,
        get_worker_stats,
    )

    data = {
        "id": "00000000-0000-0000-0000-000000000001",
        "queue": await get_queue_status(),
        "storage": get_storage_status(),
        "network": get_network_status(),
        "workers": get_worker_stats(),
        "active_tasks": get_active_tasks(),
    }

    supabase = await get_async_supabase_admin()
    await supabase.table("system_status").upsert(data).execute()
    return {"status": "success"}


@DBOS.step()
def health_check_step() -> dict[str, Any]:
    """Multi-component liveness check. Mirrors the legacy implementation —
    Celery ping + Supabase query + storage writability. Result is logged;
    no DB write."""
    checks: dict[str, str] = {
        "redis": "unknown",
        "supabase": "unknown",
        "storage": "unknown",
    }

    # PR-D7 phase 3: was celery_app.control.ping. Celery is gone;
    # check Redis directly via the get_sync_redis helper.
    try:
        from app.core.redis import get_sync_redis

        get_sync_redis().ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {str(e)[:50]}"

    try:
        from app.repositories.media_repository import MediaRepository

        async def _ping_db() -> None:
            await MediaRepository().get_statistics()

        asyncio.run(_ping_db())
        checks["supabase"] = "ok"
    except Exception as e:
        checks["supabase"] = f"error: {str(e)[:50]}"

    try:
        from app.core.utils import Utils

        base_path = Path(Utils.get_download_base_path())
        if base_path.exists() and os.access(base_path, os.W_OK):
            checks["storage"] = "ok"
        else:
            checks["storage"] = "not writable"
    except ValueError:
        checks["storage"] = "not configured"
    except Exception as e:
        checks["storage"] = f"error: {str(e)[:50]}"

    all_ok = all(v == "ok" for v in checks.values())
    return {
        "status": "healthy" if all_ok else "degraded",
        "checks": checks,
        "timestamp": datetime.now().isoformat(),
    }


@DBOS.scheduled("*/30 * * * * *")  # every 30s (6-field cron)
@DBOS.workflow()
async def update_system_status_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    await collect_system_status_step()


@DBOS.scheduled("0 * * * *")  # hourly at :00
@DBOS.workflow()
def health_check_workflow(scheduled_time: datetime, actual_time: datetime) -> None:
    result = health_check_step()
    if result["status"] != "healthy":
        logger.warning(f"[health_check] degraded: {result['checks']}")
