"""backfill_project_stage_issue_team_ids DBOS workflow — repair project-stage
mirror issues that were written with a NULL team_id.

A *personal* project carries ``team_id IS NULL`` (the projects convention), and
the stage→issue mirror used to copy that NULL straight onto the issue. But a
NULL-team issue is filtered out of every team-scoped Todolist query, so those
mirror todos are invisible (e.g. prod issue MH-14 "test3 — Planning"). The live
hook is now hardened (``project_stage_issues._resolve_issue_team_id`` translates
a NULL project team into the owner's personal-team snowflake), so THIS one-off
heals the issues written before that hardening: for every
``origin_kind='project_stage'`` issue with ``team_id IS NULL`` whose project has
a resolvable owner personal team, stamp that snowflake on the issue.

Order of operations (all idempotent):
  * ``normalize_personal_project_team_ids`` — first restores mis-stamped
    personal projects to ``team_id IS NULL`` (the inverse seam bug).
  * ``project_stage_issues`` — creates the missing mirror issues for projects
    that predate the mirror hook; via the hardened ``ensure_stage_issue`` those
    new rows already carry the right team_id, so they never need this repair.
  * ``project_stage_issue_team_ids`` (this one) — repairs the team_id on mirror
    issues that ALREADY exist but were stamped NULL before the hardening.

The project rows themselves are deliberately left untouched (a personal project
stays ``team_id IS NULL`` — the projects list keys off that). This backfill only
touches ``issues.team_id``.

Follows the backfill 规矩 (docs/runbook/data-backfills.md): DBOS workflow, Task
Center visible, ``dry_run=True`` default, failures raise (路线 C rule 4).
"""

from __future__ import annotations

from typing import Any

from dbos import DBOS
from loguru import logger
from sqlalchemy import select

from app.db.session import read_scope
from app.models import Issues, Projects

SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"


def _orphan_stage_issues_stmt(limit: int):
    """Orphan mirror issues + their project owner. hidden/soft-deleted issues
    are included: a stamped team_id is harmless there and keeps the repair
    complete."""
    return (
        select(Issues.id.label("issue_id"), Issues.project_id, Projects.owner_id)
        .select_from(Issues)
        .join(Projects, Projects.id == Issues.project_id)
        .where(
            Issues.origin_kind == "project_stage",
            Issues.team_id.is_(None),
            Issues.project_id.isnot(None),
        )
        .order_by(Issues.id)
        .limit(limit)
    )


async def run_backfill(dry_run: bool = True, limit: int = 500) -> dict[str, Any]:
    """Stamp the owner's personal-team snowflake onto NULL-team stage issues.

    Undecorated body so tests can drive it without a DBOS runtime; the workflow
    below is the thin DBOS shell.
    """
    from app.repositories.issue_repository import get_issue_repository
    from app.repositories.team_repository import get_team_repository
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    task_id = DBOS.workflow_id

    try:
        await manager.create(
            user_id=SYSTEM_RUN_USER_ID,
            task_type="backfill",
            title="Backfill: project-stage issue team_ids (NULL-team mirrors)"[:200],
            subtitle=f"dry_run={dry_run} limit={limit}",
            dbos_workflow_id=task_id,
            metadata={
                "backfill": "project_stage_issue_team_ids",
                "dry_run": dry_run,
            },
        )
    except Exception as e:
        logger.warning(f"[backfill-issue-team-ids] create task_tracking failed: {e}")
    try:
        await manager.start(task_id, user_id=SYSTEM_RUN_USER_ID, task_type="backfill")
    except Exception as e:
        logger.warning(f"[backfill-issue-team-ids] start {task_id}: {e}")

    async with read_scope() as session:
        rows = (
            (await session.execute(_orphan_stage_issues_stmt(limit))).mappings().all()
        )
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "scanned": len(rows),
        "would_stamp": 0,
        "stamped": 0,
        "unresolvable_owner": 0,
        "failed": 0,
    }
    issues = get_issue_repository()
    teams = get_team_repository()

    for row in rows:
        try:
            owner_id = row.get("owner_id")
            personal = (
                await teams.get_personal_team_id(str(owner_id)) if owner_id else None
            )
            if not personal:
                # Owner has no personal team — nothing to stamp; leave NULL.
                result["unresolvable_owner"] += 1
                continue
            if dry_run:
                result["would_stamp"] += 1
                continue
            await issues.update(int(row["issue_id"]), {"team_id": int(personal)})
            result["stamped"] += 1
        except Exception as e:
            result["failed"] += 1
            logger.warning(
                f"[backfill-issue-team-ids] issue {row.get('issue_id')} "
                f"failed (batch continues): {e!r}"
            )

    if result["failed"] > 0:
        try:
            await manager.patch_metadata(task_id, result)
        except Exception as e:
            logger.warning(f"[backfill-issue-team-ids] failed-run metadata patch: {e}")
        raise RuntimeError(
            f"[backfill-issue-team-ids] {result['failed']}/{result['scanned']} failed"
        )

    subtitle = (
        f"dry-run: {result['would_stamp']} to stamp, "
        f"{result['unresolvable_owner']} unresolvable"
        if dry_run
        else f"{result['stamped']} stamped, "
        f"{result['unresolvable_owner']} unresolvable"
    )
    try:
        await manager.complete(task_id, subtitle=subtitle[:200], metadata_patch=result)
    except Exception as e:
        logger.warning(f"[backfill-issue-team-ids] complete {task_id}: {e}")

    return result


@DBOS.workflow()
async def backfill_project_stage_issue_team_ids_workflow(
    dry_run: bool = True,
    limit: int = 500,
) -> dict[str, Any]:
    """DBOS shell over :func:`run_backfill` (registered in _dispatch_bundle)."""
    return await run_backfill(dry_run=dry_run, limit=limit)
