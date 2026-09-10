"""backfill_agent_runs_issue_id — link historical agent_runs rows to their issue.

THE BACKFILL PARADIGM (see ``backfill_issue_scope.py``): a DBOS workflow,
``dry_run=True`` by default, row-wise idempotent, failures raise, Task Center
visible — never hand-run SQL.

What is broken: ``agent_runs.issue_id`` used to be written only by
``agent_runs_repository.backfill_issue_id``, which ``issue_lifecycle`` calls
AFTER a turn returns. A run that never returned — a crashed worker, a cancel,
an empty-output turn — kept ``issue_id`` NULL forever, and every consumer
(``issue_fork``, the issue run tree, per-issue cost rollups) then had to
reverse-derive the issue from ``conversation_id``. Phase 2b-2 §4.2 stamps the
column at creation; this repairs the rows created before that.

The link is not a guess: ``issues.ai_session_id`` IS the run's
``conversation_id``. Runs with no conversation are skipped rather than
matched on anything weaker.

Idempotent by construction: both the candidate scan and the UPDATE itself
re-check ``issue_id IS NULL``, so a replayed batch finds fewer rows and never
clobbers a value another writer already set.
"""

from __future__ import annotations

from typing import Any

from dbos import DBOS
from loguru import logger
from sqlalchemy import select, update

from app.db.session import read_scope, write_scope
from app.models import AgentRuns, Issues

# Same system-batch convention as the other backfills: task_tracking.user_id is
# UUID NOT NULL and no single end user owns a backfill.
SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"

# A whole-table sweep in one statement would hold a long write lock on a table
# the live harness writes to constantly; the workflow loops batches instead.
MAX_BATCHES = 200


def _issue_for_run():
    """Correlated scalar subquery: the issue whose session is THIS run's
    conversation. ``limit(1)`` is a guard, not a choice — ``ai_session_id`` is
    uniquely indexed where non-null, but a scalar subquery that ever returned
    two rows would abort the whole batch instead of skipping one row."""
    return (
        select(Issues.id)
        .where(Issues.ai_session_id == AgentRuns.conversation_id)
        .correlate(AgentRuns)
        .limit(1)
        .scalar_subquery()
    )


def candidates_stmt(limit: int):
    """The ids this batch would touch: NULL issue_id, a conversation to match
    on, and an issue that actually matches it."""
    return (
        select(AgentRuns.id)
        .where(AgentRuns.issue_id.is_(None), AgentRuns.conversation_id.isnot(None))
        .where(_issue_for_run().isnot(None))
        .order_by(AgentRuns.id)
        .limit(limit)
    )


def backfill_stmt(limit: int):
    """The batch UPDATE. The second ``issue_id IS NULL`` is deliberate: the
    candidate scan and the write are separate statements, so the row could
    have been stamped in between."""
    return (
        update(AgentRuns)
        .where(AgentRuns.id.in_(candidates_stmt(limit)))
        .where(AgentRuns.issue_id.is_(None))
        .values(issue_id=_issue_for_run())
    )


async def _one_batch(dry_run: bool, limit: int) -> int:
    """Rows this batch stamped — or, under ``dry_run``, would stamp."""
    if dry_run:
        async with read_scope() as session:
            return len((await session.execute(candidates_stmt(limit))).scalars().all())
    async with write_scope() as session:
        result = await session.execute(backfill_stmt(limit))
        return result.rowcount or 0


async def _dry_run_scan(limit: int) -> tuple[int, bool]:
    """``(would_stamp, exhausted)`` for one dry-run page.

    Scans ``limit + 1`` and reports ``limit``. Without the probe row,
    "candidates == limit" is ambiguous — it means either "exactly one full
    page and nothing more" or "the first of many" — and reporting the
    pessimistic reading sends the operator round again for nothing.
    """
    found = await _one_batch(True, limit + 1)
    return min(found, limit), found <= limit


@DBOS.workflow()
async def backfill_agent_runs_issue_id_workflow(
    dry_run: bool = True,
    limit: int = 500,
) -> dict[str, Any]:
    """Stamp ``agent_runs.issue_id`` on pre-§4.2 rows. ``dry_run=True`` (the
    default) only reports — and reports ONE batch, because a dry run does not
    shrink the candidate set and looping it would count the same rows forever."""
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    task_id = DBOS.workflow_id

    try:
        await manager.create(
            user_id=SYSTEM_RUN_USER_ID,
            task_type="backfill",
            title="Backfill: agent_runs.issue_id"[:200],
            subtitle=f"dry_run={dry_run} limit={limit}",
            dbos_workflow_id=task_id,
            metadata={
                "backfill": "agent_runs_issue_id",
                "dry_run": dry_run,
                "limit": limit,
            },
        )
    except Exception as e:
        logger.warning(f"[backfill-agent-runs-issue-id] create task row: {e}")
    try:
        await manager.start(task_id, user_id=SYSTEM_RUN_USER_ID, task_type="backfill")
    except Exception as e:
        logger.warning(f"[backfill-agent-runs-issue-id] start {task_id}: {e}")

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "limit": limit,
        "batches": 0,
        "rows_stamped": 0,
        "would_stamp": 0,
        "exhausted": False,
    }
    try:
        if dry_run:
            would, exhausted = await _dry_run_scan(limit)
            result["would_stamp"] = would
            result["batches"] = 1
            result["exhausted"] = exhausted
        else:
            for _ in range(MAX_BATCHES):
                stamped = await _one_batch(False, limit)
                result["batches"] += 1
                result["rows_stamped"] += stamped
                if stamped == 0:
                    result["exhausted"] = True
                    break
    except Exception:
        # Persist what was counted before the crash, then raise — 路线 C rule 4:
        # the trigger writes phase=failed, we never do.
        try:
            await manager.patch_metadata(task_id, result)
        except Exception as e:
            logger.warning(f"[backfill-agent-runs-issue-id] failed-run patch: {e}")
        raise

    subtitle = (
        f"dry-run: {result['would_stamp']} run(s) would be linked"
        if dry_run
        else f"{result['rows_stamped']} run(s) linked in {result['batches']} batch(es)"
    )
    try:
        await manager.complete(task_id, subtitle=subtitle[:200], metadata_patch=result)
    except Exception as e:
        logger.warning(f"[backfill-agent-runs-issue-id] complete {task_id}: {e}")

    return result
