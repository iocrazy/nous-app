"""DBOS scheduled workflow: soft-delete expired chat temp resources.

Runs daily at 04:00 UTC. For every scope (personal + team) that owns a
``temp`` folder, reads the scope's ``chat_temp_ttl_days`` setting and
soft-deletes resources whose ``created_at + ttl_days < now``.

Soft delete only — file cleanup is handled by the existing trash pipeline
(``cleanup_trashed_resources_workflow``), not by this workflow.

Schema note: ``folder_id`` lives on ``resource_items``, not ``resources``.
The repo helper ``list_resources_in_folder`` handles the join transparently,
returning flattened ``{"id": resource_id, "created_at": ...}`` rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Tuple

from dbos import DBOS  # type: ignore[import-not-found]
from loguru import logger

from app.services.library.chat_upload import TEMP_FOLDER_NAME
from app.services.library.temp_ttl_settings import get_chat_temp_ttl_days


def _build_repo():
    """Indirection so tests can monkeypatch the repo factory."""
    from app.repositories.resources_repository import ResourcesRepository

    return ResourcesRepository()


async def _fetch_scopes_with_temp(scope_type: str) -> list[dict]:
    """Return scope rows that own at least one non-trashed ``temp`` folder.

    Uses the SQLAlchemy engine (``db_engine.fetch_all``) so it works
    even when the Supabase-py client isn't available in background jobs.

    Both scope types alias the result column as ``scope_id`` so callers
    can read it uniformly — adding a third scope type later only needs
    to follow the same convention.
    """
    from app.db import engine as db_engine

    sql = (
        "SELECT DISTINCT scope_id::text AS scope_id FROM public.folders "
        "WHERE scope_type = :scope_type AND name = :name AND is_trashed = false"
    )
    rows = await db_engine.fetch_all(
        sql, {"scope_type": scope_type, "name": TEMP_FOLDER_NAME}
    )
    return rows or []


async def _iter_scopes() -> AsyncIterator[Tuple[str, str]]:
    """Yield ``(scope_type, scope_id)`` pairs for all scopes with a temp folder."""
    for scope_type in ("personal", "team"):
        rows = await _fetch_scopes_with_temp(scope_type)
        for row in rows:
            yield (scope_type, str(row["scope_id"]))


def _is_expired(created_at_str: str, ttl_days: int, now: datetime) -> bool:
    """Return True when the resource's age exceeds the TTL."""
    try:
        created = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
    except ValueError:
        logger.warning(
            f"[temp_sweeper] unparseable created_at {created_at_str!r}; skip"
        )
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created) > timedelta(days=ttl_days)


async def _sweep_scope(scope_type: str, scope_id: str) -> int:
    """Soft-delete expired resources in this scope's temp folder.

    Returns the count of resources soft-deleted.
    Returns 0 without touching the DB when:
    - TTL is ``None`` (never-expire setting), or
    - the scope has no temp folder.
    """
    ttl_days = await get_chat_temp_ttl_days(scope_type, scope_id)
    if ttl_days is None:
        return 0

    repo = _build_repo()
    folders = await repo.get_folders(scope_type, scope_id)
    temp_folder = next((f for f in folders if f.get("name") == TEMP_FOLDER_NAME), None)
    if temp_folder is None:
        return 0

    resources = await repo.list_resources_in_folder(temp_folder["id"])
    now = datetime.now(timezone.utc)
    deleted = 0
    for res in resources:
        created_at = res.get("created_at")
        if created_at and _is_expired(created_at, ttl_days, now):
            await repo.soft_delete_resource(str(res["id"]))
            deleted += 1

    if deleted:
        logger.info(
            f"[temp_sweeper] {scope_type}/{scope_id}: soft-deleted {deleted} expired"
        )
    return deleted


async def sweep_temp_resources() -> dict:
    """Sweep every scope's temp folder for expired resources.

    Plain async function (not decorated) so tests can call it directly
    without a live DBOS singleton. ``temp_resource_sweeper_scheduled``
    delegates here from the daily cron.

    Errors in individual scopes are logged and skipped so one bad scope
    never prevents the rest from being swept.
    """
    started = datetime.now(timezone.utc)
    total_deleted = 0
    scopes_swept = 0

    async for scope_type, scope_id in _iter_scopes():
        try:
            total_deleted += await _sweep_scope(scope_type, scope_id)
            scopes_swept += 1
        except Exception as exc:
            logger.exception(
                f"[temp_sweeper] {scope_type}/{scope_id} sweep failed: {exc}"
            )

    duration_s = (datetime.now(timezone.utc) - started).total_seconds()
    logger.info(
        f"[temp_sweeper] done: scopes={scopes_swept} deleted={total_deleted} "
        f"duration_s={duration_s:.1f}"
    )
    return {
        "scopes_swept": scopes_swept,
        "total_deleted": total_deleted,
        "duration_s": duration_s,
    }


@DBOS.step()
async def _sweep_all_scopes_step() -> dict:
    """Wraps ``sweep_temp_resources`` as a DBOS step so the scheduled
    workflow checkpoints between sweep runs.

    Codebase pattern — every other scheduled workflow (e.g.
    ``agent_runs_sweeper_workflow``, ``cleanup_temp_files_workflow``,
    ``cleanup_trashed_resources_workflow``, ``workflow_health_sweeper_workflow``)
    delegates to a ``@DBOS.step()`` decorated function so a mid-sweep
    worker crash doesn't replay the work from scratch.
    """
    return await sweep_temp_resources()


@DBOS.scheduled("0 4 * * *")  # Daily 04:00 UTC
@DBOS.workflow()
async def temp_resource_sweeper_scheduled(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """Scheduled entry-point.  Delegates to ``_sweep_all_scopes_step``."""
    result = await _sweep_all_scopes_step()
    logger.info(f"[temp_sweeper] scheduled run complete: {result}")


__all__ = [
    "sweep_temp_resources",
    "_sweep_all_scopes_step",
    "temp_resource_sweeper_scheduled",
]
