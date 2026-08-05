"""backfill_normalize_personal_project_team_ids DBOS workflow — restore the
projects NULL=personal convention for projects mis-stamped with their owner's
personal-team snowflake.

Before Todolist Wave 1's inline "+ New project" was fixed, creating a project
from the Todolist's Group-by-Project view in a PERSONAL workspace stamped the
project with the owner's personal-team snowflake (``teamIdNum`` = the personal
team's id in personal mode). Under the projects convention a personal project is
``team_id IS NULL`` (that's how the Projects page identifies personal projects —
``get_user_projects`` maps the "personal" filter to ``team_id IS NULL``), so
those snowflake-stamped rows are INVISIBLE on the Projects personal view — the
exact inverse of the mirror-issue bug, same seam disease.

This one-off normalizes them back: for every ``projects`` row whose ``team_id``
equals ITS OWN owner's personal team (``teams.kind='personal' AND
teams.owner_id = projects.owner_id``), set ``team_id = NULL``. The join's
owner-match keeps this conservative — a project stamped with a *collaborative*
team, or with some other user's team, is never touched. The dry-run report's
``would_normalize`` count is the "how many exist in the wild" answer.

Mirror issues are intentionally NOT touched here: a mis-stamped project's
existing mirror issue already carries the personal snowflake (visible in the
Todolist), and NULLing the project keeps it visible; going forward the hardened
``ensure_stage_issue`` resolves the personal snowflake for the issue while the
project stays NULL — one convention on each side.

Order of operations across the project/issue backfills (all idempotent):
  1. ``normalize_personal_project_team_ids`` (this) — projects back to NULL.
  2. ``project_stage_issues`` — create any missing mirror issues (the hardened
     ``ensure_stage_issue`` stamps the owner's personal team on them).
  3. ``project_stage_issue_team_ids`` — repair legacy NULL-team mirror issues.

Follows the backfill 规矩 (docs/runbook/data-backfills.md): DBOS workflow, Task
Center visible, ``dry_run=True`` default, failures raise (路线 C rule 4).
"""

from __future__ import annotations

from typing import Any

from dbos import DBOS
from loguru import logger

SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"


def _misstamped_personal_projects_stmt(limit: int):
    """Projects mis-stamped with their OWN owner's personal team. The
    owner-match in the JOIN is what makes this safe: a project legitimately
    attached to a collaborative team (or, defensively, one pointing at
    another user's team) is never selected.

    ``Projects.id.label("project_id")`` — a single explicit column, so
    ``.mappings()`` yields ``row["project_id"]`` matching the consumer below
    (not the entity-keyed shape ``select(Projects)`` would give)."""
    from sqlalchemy import select

    from app.models import Projects, Teams

    return (
        select(Projects.id.label("project_id"))
        .select_from(Projects)
        .join(
            Teams,
            (Teams.id == Projects.team_id)
            & (Teams.kind == "personal")
            & (Teams.owner_id == Projects.owner_id),
        )
        .where(Projects.team_id.is_not(None))
        .order_by(Projects.id)
        .limit(limit)
    )


async def run_backfill(dry_run: bool = True, limit: int = 500) -> dict[str, Any]:
    """Null out ``team_id`` on projects stamped with their owner's personal team.

    Undecorated body so tests can drive it without a DBOS runtime; the workflow
    below is the thin DBOS shell.
    """
    from app.db.session import read_scope
    from app.repositories.projects_repository import get_projects_repository
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    task_id = DBOS.workflow_id

    try:
        await manager.create(
            user_id=SYSTEM_RUN_USER_ID,
            task_type="backfill",
            title="Backfill: normalize personal-project team_ids (→ NULL)"[:200],
            subtitle=f"dry_run={dry_run} limit={limit}",
            dbos_workflow_id=task_id,
            metadata={
                "backfill": "normalize_personal_project_team_ids",
                "dry_run": dry_run,
            },
        )
    except Exception as e:
        logger.warning(
            f"[backfill-normalize-projects] create task_tracking failed: {e}"
        )
    try:
        await manager.start(task_id, user_id=SYSTEM_RUN_USER_ID, task_type="backfill")
    except Exception as e:
        logger.warning(f"[backfill-normalize-projects] start {task_id}: {e}")

    async with read_scope() as session:
        rows = (
            (await session.execute(_misstamped_personal_projects_stmt(limit)))
            .mappings()
            .all()
        )
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "scanned": len(rows),
        "would_normalize": 0,
        "normalized": 0,
        "failed": 0,
    }
    projects = get_projects_repository()

    for row in rows:
        try:
            if dry_run:
                result["would_normalize"] += 1
                continue
            await projects.update_project(str(row["project_id"]), {"team_id": None})
            result["normalized"] += 1
        except Exception as e:
            result["failed"] += 1
            logger.warning(
                f"[backfill-normalize-projects] project {row.get('project_id')} "
                f"failed (batch continues): {e!r}"
            )

    if result["failed"] > 0:
        try:
            await manager.patch_metadata(task_id, result)
        except Exception as e:
            logger.warning(
                f"[backfill-normalize-projects] failed-run metadata patch: {e}"
            )
        raise RuntimeError(
            f"[backfill-normalize-projects] {result['failed']}/{result['scanned']} failed"
        )

    subtitle = (
        f"dry-run: {result['would_normalize']} to normalize"
        if dry_run
        else f"{result['normalized']} normalized"
    )
    try:
        await manager.complete(task_id, subtitle=subtitle[:200], metadata_patch=result)
    except Exception as e:
        logger.warning(f"[backfill-normalize-projects] complete {task_id}: {e}")

    return result


@DBOS.workflow()
async def backfill_normalize_personal_project_team_ids_workflow(
    dry_run: bool = True,
    limit: int = 500,
) -> dict[str, Any]:
    """DBOS shell over :func:`run_backfill` (registered in _dispatch_bundle)."""
    return await run_backfill(dry_run=dry_run, limit=limit)
