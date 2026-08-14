"""backfill_publish_task_team_ids DBOS workflow — heal publish batches (and the
issues mirrored from them) that were written with a NULL team_id.

``PublishTaskCreate`` had no ``team_id`` field at all, so the column that has
existed on ``publish_tasks`` since mig 356 was never written. The mirror copies
that NULL onto the issue it creates, and the Todolist filters EVERY scope by
``team_id`` (even ``my``) — so a user's own failed publish batches were
invisible to them, which is exactly how the bug was reported: two image posts
failed, both issues reached ``blocked``, neither ever appeared.

The live path is now fixed (``distribution_router._resolve_task_team_id``
stamps the requested workspace, falling back to the caller's personal team), so
this one-off heals only the rows written before that. It is needed because the
mirror **never revisits** ``team_id``: ``_mirror_new_batches`` writes it at
create time and ``_sync_terminal_batches`` only moves status. Without this
backfill those issues stay invisible forever.

Two ordered halves, both idempotent:

  1. ``publish_tasks.team_id IS NULL`` → the batch owner's personal-team
     snowflake. A personal workspace IS a team row (``teams.kind='personal'``),
     and it is single-member, so attributing an unattributed batch to its
     creator's personal team can never widen who sees it.
  2. ``issues`` with ``origin_kind='publish'`` and ``team_id IS NULL`` → the
     team now on their publish_tasks row. The issue is repaired FROM the task
     row rather than from its own ``created_by_user_id`` on purpose: the task
     row is where workspace attribution lives, so a batch we later learn
     belonged to a real team heals correctly by re-running this, and the two
     tables can never disagree.

Half 2 depends on half 1 having run, which is why they are one workflow rather
than two registry entries.

Follows the backfill 规矩 (docs/runbook/data-backfills.md): DBOS workflow, Task
Center visible, ``dry_run=True`` default, failures raise (路线 C rule 4).
"""

from __future__ import annotations

from typing import Any, Optional

from dbos import DBOS
from loguru import logger
from sqlalchemy import select
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import Issues, PublishTasks
from app.workflows.publish_issue_mirror import ORIGIN_KIND, build_publish_origin_id

SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"

# Derived from the writer so the prefix has exactly ONE definition — a parser
# that spelled it independently could drift from the builder and silently match
# nothing, which on a repair job looks identical to "nothing needed repairing".
_ORIGIN_PREFIX = build_publish_origin_id("")


def parse_publish_origin_id(origin_id: Any) -> Optional[str]:
    """``'publish:123'`` → ``'123'``; anything else → None.

    Pure, and the id stays a STRING: these are Snowflakes, and an ``int``
    round-trip through float (or a rounded compare) can never match the exact
    row again. Rejects a bare/empty id and any non-digit payload, so a
    malformed origin can never reach a numeric bind.
    """
    if not isinstance(origin_id, str) or not origin_id.startswith(_ORIGIN_PREFIX):
        return None
    task_id = origin_id[len(_ORIGIN_PREFIX) :]
    return task_id if task_id.isdigit() else None


def _null_team_tasks_stmt(limit: int):
    """Publish batches with no workspace recorded, plus their owner."""
    return (
        select(PublishTasks.id, PublishTasks.user_id)
        .where(PublishTasks.team_id.is_(None))
        .order_by(PublishTasks.id)
        .limit(limit)
    )


def _null_team_publish_issues_stmt(limit: int):
    """Mirrored publish issues with no team. Hidden/soft-deleted rows are
    included: stamping a team on them is harmless and keeps the repair
    complete."""
    return (
        select(Issues.id, Issues.origin_id)
        .where(Issues.origin_kind == ORIGIN_KIND, Issues.team_id.is_(None))
        .order_by(Issues.id)
        .limit(limit)
    )


async def run_backfill(dry_run: bool = True, limit: int = 500) -> dict[str, Any]:
    """Stamp workspace attribution onto NULL-team publish batches and issues.

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
            title="Backfill: publish batch + mirrored issue team_ids"[:200],
            subtitle=f"dry_run={dry_run} limit={limit}",
            dbos_workflow_id=task_id,
            metadata={"backfill": "publish_task_team_ids", "dry_run": dry_run},
        )
    except Exception as e:
        logger.warning(f"[backfill-publish-team-ids] create task_tracking failed: {e}")
    try:
        await manager.start(task_id, user_id=SYSTEM_RUN_USER_ID, task_type="backfill")
    except Exception as e:
        logger.warning(f"[backfill-publish-team-ids] start {task_id}: {e}")

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "tasks_scanned": 0,
        "tasks_stamped": 0,
        "tasks_unresolvable_owner": 0,
        "issues_scanned": 0,
        "issues_stamped": 0,
        "issues_unresolvable_task": 0,
        "failed": 0,
    }
    teams = get_team_repository()
    issues = get_issue_repository()

    # ── half 1: the batches themselves ──────────────────────────────
    async with read_scope() as session:
        tasks = (await session.execute(_null_team_tasks_stmt(limit))).mappings().all()
    result["tasks_scanned"] = len(tasks)
    # task id (str) → team id (str), so half 2 can resolve the rows half 1 just
    # fixed without depending on read-your-write visibility across sessions.
    resolved: dict[str, str] = {}

    for row in tasks:
        try:
            personal = await teams.get_personal_team_id(str(row["user_id"]))
            if not personal:
                result["tasks_unresolvable_owner"] += 1
                continue
            resolved[str(row["id"])] = personal
            if dry_run:
                continue
            async with write_scope() as session:
                await session.execute(
                    sa_update(PublishTasks)
                    # Row-level idempotency: the NULL guard rides in the WHERE,
                    # so a replay (or a concurrent live write) never overwrites
                    # a team that has since been recorded properly.
                    .where(PublishTasks.id == int(row["id"]))
                    .where(PublishTasks.team_id.is_(None))
                    .values(team_id=int(personal))
                )
            result["tasks_stamped"] += 1
        except Exception as e:
            result["failed"] += 1
            logger.warning(
                f"[backfill-publish-team-ids] publish_task {row.get('id')} "
                f"failed (batch continues): {e!r}"
            )

    # ── half 2: the issues mirrored from them ───────────────────────
    async with read_scope() as session:
        rows = (
            (await session.execute(_null_team_publish_issues_stmt(limit)))
            .mappings()
            .all()
        )
    result["issues_scanned"] = len(rows)

    for row in rows:
        try:
            publish_task_id = parse_publish_origin_id(row.get("origin_id"))
            team_id = resolved.get(publish_task_id or "")
            if team_id is None and publish_task_id:
                # Already-attributed task row (fixed by the live path, or by an
                # earlier run of this backfill) whose issue predates it.
                async with read_scope() as session:
                    existing = await session.scalar(
                        select(PublishTasks.team_id).where(
                            PublishTasks.id == int(publish_task_id)
                        )
                    )
                team_id = str(existing) if existing is not None else None
            if not team_id:
                result["issues_unresolvable_task"] += 1
                continue
            if dry_run:
                continue
            await issues.update(int(row["id"]), {"team_id": int(team_id)})
            result["issues_stamped"] += 1
        except Exception as e:
            result["failed"] += 1
            logger.warning(
                f"[backfill-publish-team-ids] issue {row.get('id')} "
                f"failed (batch continues): {e!r}"
            )

    if result["failed"] > 0:
        try:
            await manager.patch_metadata(task_id, result)
        except Exception as e:
            logger.warning(
                f"[backfill-publish-team-ids] failed-run metadata patch: {e}"
            )
        raise RuntimeError(
            f"[backfill-publish-team-ids] {result['failed']} row(s) failed"
        )

    would_tasks = len(resolved)
    would_issues = result["issues_scanned"] - result["issues_unresolvable_task"]
    subtitle = (
        f"dry-run: {would_tasks} batches / {would_issues} issues to stamp"
        if dry_run
        else f"{result['tasks_stamped']} batches / "
        f"{result['issues_stamped']} issues stamped"
    )
    try:
        await manager.complete(task_id, subtitle=subtitle[:200], metadata_patch=result)
    except Exception as e:
        logger.warning(f"[backfill-publish-team-ids] complete {task_id}: {e}")

    return result


@DBOS.workflow()
async def backfill_publish_task_team_ids_workflow(
    dry_run: bool = True,
    limit: int = 500,
) -> dict[str, Any]:
    """DBOS shell over :func:`run_backfill` (registered in ``_BACKFILLS``)."""
    return await run_backfill(dry_run=dry_run, limit=limit)
