"""Scheduled cleanup workflows.

Each is a thin @DBOS.scheduled wrapper that delegates to the existing
service code:
  - cleanup_temp_files            (daily 00:00) — FS sweep of .tmp/.part files
  - cleanup_old_task_tracking     (daily 02:00) — drop terminal rows >7d old
  - cleanup_trashed_resources     (daily 01:00) — soft-delete sweep >15d old
  - cleanup_orphan_storage        (weekly Sun 03:00) — sweep upload dirs
    whose resource_id no longer exists in DB (catches CASCADE deletes,
    direct DELETEs, and upload aborts that bypass the trash flow)

Cron offsets (00/01/02 daily, 03 weekly) spread IO so workflows don't
all hit DB at midnight together.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dbos import DBOS
from loguru import logger

from app.tasks.utils import run_async


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

    async def _do() -> int:
        from app.db import engine as db_engine

        # See scheduled_recovery.reap_stuck_pending_tasks_step for why
        # this MUST be timezone-aware UTC, not naive local time.
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        return await db_engine.execute(
            "DELETE FROM public.task_tracking "
            "WHERE status IN ('completed', 'failed', 'cancelled') "
            "AND updated_at < :cutoff",
            {"cutoff": cutoff},
        )

    deleted = run_async(_do())
    return {"status": "success", "deleted": deleted}


@DBOS.step()
def cleanup_trashed_resources_step() -> dict[str, Any]:
    """Permanently delete resources soft-deleted >15 days ago."""
    from app.services.library.resources_service import ResourcesService

    async def _do() -> int:
        svc = ResourcesService()
        return await svc.cleanup_expired_trash(older_than_days=15)

    cleaned = run_async(_do())
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


# Minimum age before an orphan upload dir is eligible for deletion.
# Buffer against in-progress uploads whose DB row has not yet been written.
ORPHAN_STORAGE_MIN_AGE_DAYS = 7


def _sweep_orphan_upload_dirs(
    base: Path,
    valid_resource_ids: set[int],
    min_age_seconds: float,
) -> dict[str, int]:
    """Walk `{base}/teams/*/uploads/*/` and rmtree dirs whose name is a
    numeric resource_id not present in `valid_resource_ids`.

    Pure-FS helper; takes the DB-id set as a parameter so it can be
    unit-tested with a fake filesystem and no DB.
    """
    import shutil
    import time

    deleted_dirs = 0
    deleted_bytes = 0
    skipped_too_young = 0
    skipped_unparseable = 0

    teams_root = base / "teams"
    if not teams_root.is_dir():
        return {
            "deleted_dirs": 0,
            "deleted_bytes": 0,
            "skipped_too_young": 0,
            "skipped_unparseable": 0,
        }

    cutoff_mtime = time.time() - min_age_seconds

    for scope_dir in teams_root.iterdir():
        uploads_dir = scope_dir / "uploads"
        if not uploads_dir.is_dir():
            continue
        for rid_dir in uploads_dir.iterdir():
            if not rid_dir.is_dir():
                continue
            name = rid_dir.name
            if not name.isdigit():
                skipped_unparseable += 1
                continue
            try:
                rid = int(name)
            except ValueError:
                skipped_unparseable += 1
                continue
            if rid in valid_resource_ids:
                continue
            try:
                mtime = rid_dir.stat().st_mtime
            except OSError as e:
                logger.warning(f"[orphan_storage] stat failed {rid_dir}: {e}")
                continue
            if mtime > cutoff_mtime:
                skipped_too_young += 1
                continue
            try:
                size = sum(f.stat().st_size for f in rid_dir.rglob("*") if f.is_file())
            except OSError:
                size = 0
            try:
                shutil.rmtree(rid_dir)
            except Exception as e:
                logger.warning(f"[orphan_storage] rmtree failed {rid_dir}: {e}")
                continue
            deleted_dirs += 1
            deleted_bytes += size
            logger.info(
                f"[orphan_storage] removed teams/{scope_dir.name}/uploads/{name} "
                f"({size} bytes)"
            )

    return {
        "deleted_dirs": deleted_dirs,
        "deleted_bytes": deleted_bytes,
        "skipped_too_young": skipped_too_young,
        "skipped_unparseable": skipped_unparseable,
    }


@DBOS.step()
def cleanup_orphan_storage_step() -> dict[str, Any]:
    """Remove `teams/{scope}/uploads/{resource_id}/` directories whose
    resource_id is not referenced by `public.resources.id`.

    Catches orphans the trash-flow GC misses:
      - auth.users CASCADE that nukes resources rows directly
      - manual DELETE FROM resources (legacy ops, tests)
      - upload paths that wrote files but never inserted a row

    Files younger than ORPHAN_STORAGE_MIN_AGE_DAYS are skipped so an
    in-flight upload whose DB insert hasn't landed yet survives.
    """
    from app.core.utils import Utils

    try:
        base = Path(Utils.get_download_base_path()).resolve()
    except ValueError:
        return {"status": "skipped", "reason": "download path not configured"}
    if not base.exists():
        return {"status": "skipped", "reason": "download dir missing"}

    async def _load_resource_ids() -> set[int]:
        from app.db import engine as db_engine

        rows = await db_engine.fetch_all("SELECT id FROM public.resources")
        return {row["id"] for row in rows if row.get("id") is not None}

    valid_ids = run_async(_load_resource_ids())
    min_age_seconds = ORPHAN_STORAGE_MIN_AGE_DAYS * 86400.0

    counts = _sweep_orphan_upload_dirs(base, valid_ids, min_age_seconds)
    return {
        "status": "success",
        "deleted_dirs": counts["deleted_dirs"],
        "bytes_freed_mb": round(counts["deleted_bytes"] / (1024 * 1024), 2),
        "skipped_too_young": counts["skipped_too_young"],
        "skipped_unparseable": counts["skipped_unparseable"],
    }


@DBOS.scheduled("0 3 * * 0")  # weekly Sunday 03:00 UTC
@DBOS.workflow()
def cleanup_orphan_storage_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    result = cleanup_orphan_storage_step()
    if result.get("deleted_dirs") or result.get("skipped_too_young"):
        logger.info(f"[cleanup_orphan_storage] {result}")
