"""backfill_project_stage_issues DBOS workflow — mirror EXISTING projects'
current stages into todos.

The stage→issue mirror originally fired only on the manual PUT path, so
projects created (or auto-advanced) before the repo-level hook landed have a
current stage but no mirror issue. This one-off walks every project with a
``current_stage_id`` and idempotently ensures the stage issue exists — the
same ``ensure_stage_issue`` the live hook uses, so origin_id de-dup makes
replays and double-runs free.

Follows the backfill 规矩 (docs/runbook/data-backfills.md): DBOS workflow,
Task Center visible, ``dry_run=True`` default, failures raise (路线 C rule 4).
"""

from __future__ import annotations

from typing import Any

from dbos import DBOS
from loguru import logger

from app.db import engine as db_engine

SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"

_PROJECTS_WITH_STAGE_SQL = """
SELECT p.id AS project_id, p.name, p.current_stage_id, p.team_id, p.owner_id,
       s.id AS stage_id, s.slug AS stage_slug, s.name AS stage_name
FROM public.projects p
JOIN public.project_stages s ON s.id = p.current_stage_id
WHERE p.current_stage_id IS NOT NULL
ORDER BY p.id
LIMIT :limit
"""


async def run_backfill(dry_run: bool = True, limit: int = 500) -> dict[str, Any]:
    """Ensure a mirror issue exists for every project's current stage.

    Undecorated body so tests can drive it without a DBOS runtime; the
    workflow below is the thin DBOS shell.
    """
    from app.repositories.issue_repository import get_issue_repository
    from app.services.infra.unified_task_manager import get_task_manager
    from app.services.library.project_stage_issues import (
        build_stage_origin_id,
        ensure_stage_issue,
    )

    manager = get_task_manager()
    task_id = DBOS.workflow_id

    try:
        await manager.create(
            user_id=SYSTEM_RUN_USER_ID,
            task_type="backfill",
            title="Backfill: project stage todos (pre-hook projects)"[:200],
            subtitle=f"dry_run={dry_run} limit={limit}",
            dbos_workflow_id=task_id,
            metadata={"backfill": "project_stage_issues", "dry_run": dry_run},
        )
    except Exception as e:
        logger.warning(f"[backfill-stage-issues] create task_tracking failed: {e}")
    try:
        await manager.start(task_id, user_id=SYSTEM_RUN_USER_ID, task_type="backfill")
    except Exception as e:
        logger.warning(f"[backfill-stage-issues] start {task_id}: {e}")

    rows = await db_engine.fetch_all(_PROJECTS_WITH_STAGE_SQL, {"limit": limit})
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "scanned": len(rows),
        "already_mirrored": 0,
        "would_create": 0,
        "created": 0,
        "failed": 0,
    }
    issues = get_issue_repository()

    for row in rows:
        try:
            origin_id = build_stage_origin_id(row["project_id"], row["stage_id"])
            existing = await issues.list_by_origin("project_stage", origin_id)
            if existing:
                result["already_mirrored"] += 1
                continue
            if dry_run:
                result["would_create"] += 1
                continue
            stage = {
                "id": row["stage_id"],
                "slug": row["stage_slug"],
                "name": row["stage_name"],
            }
            await ensure_stage_issue(
                issues,
                int(row["project_id"]),
                stage,
                str(row.get("owner_id") or SYSTEM_RUN_USER_ID),
            )
            result["created"] += 1
        except Exception as e:
            result["failed"] += 1
            logger.warning(
                f"[backfill-stage-issues] project {row.get('project_id')} "
                f"failed (batch continues): {e!r}"
            )

    if result["failed"] > 0:
        try:
            await manager.patch_metadata(task_id, result)
        except Exception as e:
            logger.warning(f"[backfill-stage-issues] failed-run metadata patch: {e}")
        raise RuntimeError(
            f"[backfill-stage-issues] {result['failed']}/{result['scanned']} failed"
        )

    subtitle = (
        f"dry-run: {result['would_create']} to create, "
        f"{result['already_mirrored']} already mirrored"
        if dry_run
        else f"{result['created']} created, {result['already_mirrored']} already mirrored"
    )
    try:
        await manager.complete(task_id, subtitle=subtitle[:200], metadata_patch=result)
    except Exception as e:
        logger.warning(f"[backfill-stage-issues] complete {task_id}: {e}")

    return result


@DBOS.workflow()
async def backfill_project_stage_issues_workflow(
    dry_run: bool = True,
    limit: int = 500,
) -> dict[str, Any]:
    """DBOS shell over :func:`run_backfill` (registered in _dispatch_bundle)."""
    return await run_backfill(dry_run=dry_run, limit=limit)
