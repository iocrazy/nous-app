"""Scheduled cleanup workflows — port of three GC jobs from
`tasks.scheduled_tasks`.

Each is a thin @DBOS.scheduled wrapper that delegates to the existing
service code:
  - cleanup_temp_files       (daily 00:00) — FS sweep of .tmp/.part files
  - cleanup_old_task_tracking (daily 02:00) — drop terminal rows >7d old
  - cleanup_trashed_resources (daily 01:00) — soft-delete sweep >15d old

Cron offsets (00/01/02) spread the daily IO so they don't all hit the
DB at midnight together.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dbos import DBOS
from loguru import logger


@DBOS.step()
def cleanup_temp_files_step() -> dict[str, Any]:
    """Walk the download base path and unlink .tmp/.part/.downloading
    files older than 7 days; remove resulting empty directories."""
    from app.core.utils import Utils

    try:
        base_path = Path(Utils.get_download_base_path())
    except ValueError:
        return {"status": "skipped", "reason": "download path not configured"}

    if not base_path.exists():
        return {"status": "skipped", "reason": "download dir missing"}

    files_deleted = 0
    dirs_deleted = 0
    space_freed = 0
    cutoff = datetime.now() - timedelta(days=7)

    for root, dirs, files in os.walk(base_path, topdown=False):
        root_path = Path(root)
        for file in files:
            if not file.endswith((".tmp", ".part", ".downloading")):
                continue
            file_path = root_path / file
            try:
                stat = file_path.stat()
                if datetime.fromtimestamp(stat.st_mtime) < cutoff:
                    space_freed += stat.st_size
                    file_path.unlink()
                    files_deleted += 1
            except Exception as e:
                logger.warning(f"[cleanup_temp_files] {file_path}: {e}")
        for dir_name in dirs:
            dir_path = root_path / dir_name
            try:
                if dir_path.is_dir() and not any(dir_path.iterdir()):
                    dir_path.rmdir()
                    dirs_deleted += 1
            except Exception as e:
                logger.warning(f"[cleanup_temp_files] {dir_path}: {e}")

    return {
        "status": "success",
        "files_deleted": files_deleted,
        "dirs_deleted": dirs_deleted,
        "space_freed_mb": round(space_freed / (1024 * 1024), 2),
    }


@DBOS.step()
def cleanup_old_task_tracking_step() -> dict[str, Any]:
    """Drop task_tracking rows in terminal state older than 7 days."""
    from app.db.supabase_client import get_async_supabase_admin

    async def _do() -> int:
        supabase = await get_async_supabase_admin()
        # See scheduled_recovery.reap_stuck_pending_tasks_step for why
        # this MUST be timezone-aware UTC, not naive local time.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        result = (
            await supabase.table("task_tracking")
            .delete()
            .in_("status", ["completed", "failed", "cancelled"])
            .lt("updated_at", cutoff)
            .execute()
        )
        return len(result.data) if result.data else 0

    deleted = asyncio.run(_do())
    return {"status": "success", "deleted": deleted}


@DBOS.step()
def cleanup_trashed_resources_step() -> dict[str, Any]:
    """Permanently delete resources soft-deleted >15 days ago."""
    from app.services.library.resources_service import ResourcesService

    async def _do() -> int:
        svc = ResourcesService()
        return await svc.cleanup_expired_trash(older_than_days=15)

    cleaned = asyncio.run(_do())
    return {"status": "success", "cleaned": cleaned}


@DBOS.scheduled("0 0 * * *")  # daily 00:00 UTC
@DBOS.workflow()
def cleanup_temp_files_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = cleanup_temp_files_step()
    if result.get("files_deleted") or result.get("dirs_deleted"):
        logger.info(f"[cleanup_temp_files] {result}")


@DBOS.scheduled("0 1 * * *")  # daily 01:00 UTC
@DBOS.workflow()
def cleanup_trashed_resources_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = cleanup_trashed_resources_step()
    if result.get("cleaned"):
        logger.info(f"[cleanup_trashed_resources] {result}")


@DBOS.scheduled("0 2 * * *")  # daily 02:00 UTC
@DBOS.workflow()
def cleanup_old_task_tracking_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = cleanup_old_task_tracking_step()
    if result.get("deleted"):
        logger.info(f"[cleanup_old_task_tracking] {result}")
