"""publish_issue_mirror — every publish batch is a managed work item.

User decision (2026-07-18): "发布就应该触发管理" — the todolist is the one
place work is controlled, so a distribution publish is mirrored into Issues
for its whole life, not just on failure:

  * batch dispatched            → issue created (todo/in_progress)
  * batch completed             → issue auto-transitions to done (zero noise)
  * batch failed (retries done) → issue → blocked (the mockup's off-pipeline
                                  "incident" lane — a human decides what next)
  * pending_share               → the issue simply stays open as the reminder

路线 C compliance: the state machine stays in DBOS/task_tracking; this is a
ONE-WAY mirror (reads task_tracking, writes issues + the task row's
``issue_id`` business column). It never drives execution, so the two views
cannot drift into a second source of truth. Implemented as a scheduled
sweeper — not hooks inside the publish workflow — so the distribution
module's files stay untouched (another session's active territory) and
crash-lost batches still get mirrored. Eventual consistency (≤2 min) is fine
for a management view.

Idempotency: ``task_tracking.issue_id`` is both the back-link and the
create-once guard (stamped in the same sweep that creates the issue); the
issue's ``origin_id`` ('publish:{publish_task_id}') is the belt to that
suspender. Scheduled housekeeping runs do NOT create task_tracking rows
(路线 C rule 5 — mirrors stranded_issue_monitor).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

from app.db import engine as db_engine

ORIGIN_KIND = "publish"

# Issue statuses that no longer need syncing.
_TERMINAL_ISSUE_STATUSES = frozenset({"done", "cancelled"})

# Only mirror recent batches — resurrecting months-old history as fresh
# todos would be noise, not management.
_LOOKBACK = "30 days"
_BATCH_LIMIT = 50


def build_publish_origin_id(publish_task_id: Any) -> str:
    """'publish:{publish_task_id}' — id stays a string (Snowflake bigint)."""
    return f"{ORIGIN_KIND}:{publish_task_id}"


def issue_status_for_phase(phase: Optional[str]) -> Optional[str]:
    """Initial issue status when first mirroring a batch in ``phase``.

    completed batches without an issue are NOT back-mirrored (nothing to
    manage); failed ones are — that is precisely the work needing a human.
    """
    if phase == "queued":
        return "todo"
    if phase == "in_progress":
        return "in_progress"
    if phase in ("failed", "lost"):
        return "blocked"
    return None  # completed / cancelled / unknown → no retroactive mirror


def terminal_sync_action(
    phase: Optional[str], issue_status: Optional[str]
) -> Optional[str]:
    """Target issue status when a mirrored batch reaches a terminal phase."""
    if issue_status in _TERMINAL_ISSUE_STATUSES or issue_status == "blocked":
        return None
    if phase == "completed":
        return "done"
    if phase in ("failed", "lost", "cancelled"):
        return "blocked" if phase != "cancelled" else "cancelled"
    return None


_UNMIRRORED_SQL = f"""
SELECT t.dbos_workflow_id, t.title, t.user_id::text AS user_id, t.phase,
       (t.metadata->>'publish_task_id') AS publish_task_id,
       p.team_id
FROM public.task_tracking t
LEFT JOIN public.publish_tasks p
       ON p.id = NULLIF(t.metadata->>'publish_task_id', '')::bigint
WHERE t.task_type = 'publish'
  AND t.issue_id IS NULL
  AND t.created_at > NOW() - INTERVAL '{_LOOKBACK}'
ORDER BY t.created_at DESC
LIMIT {_BATCH_LIMIT}
"""

_MIRRORED_OPEN_SQL = f"""
SELECT t.dbos_workflow_id, t.phase, t.issue_id, i.status AS issue_status
FROM public.task_tracking t
JOIN public.issues i ON i.id = t.issue_id
WHERE t.task_type = 'publish'
  AND t.issue_id IS NOT NULL
  AND t.phase IN ('completed', 'failed', 'lost', 'cancelled')
  AND i.status NOT IN ('done', 'cancelled', 'blocked')
LIMIT {_BATCH_LIMIT}
"""

_STAMP_ISSUE_SQL = """
UPDATE public.task_tracking
SET issue_id = :issue_id
WHERE dbos_workflow_id = :wf_id AND issue_id IS NULL
"""


async def _mirror_new_batches() -> dict[str, int]:
    from app.repositories.issue_repository import get_issue_repository

    issues = get_issue_repository()
    counts = {"created": 0, "skipped": 0}
    rows = await db_engine.fetch_all(_UNMIRRORED_SQL)
    for row in rows:
        try:
            status = issue_status_for_phase(row.get("phase"))
            publish_task_id = row.get("publish_task_id")
            if status is None or not publish_task_id:
                counts["skipped"] += 1
                continue
            origin_id = build_publish_origin_id(publish_task_id)
            existing = await issues.list_by_origin(ORIGIN_KIND, origin_id)
            if existing:
                issue_id = int(existing[0]["id"])
            else:
                payload: dict[str, Any] = {
                    "title": (row.get("title") or "Publish batch")[:500],
                    "status": status,
                    "origin_kind": ORIGIN_KIND,
                    "origin_id": origin_id,
                    # Mirror never assigns — 管控凭据, not a dispatch (立约).
                    "created_by_user_id": row["user_id"],
                }
                if row.get("team_id") is not None:
                    payload["team_id"] = int(row["team_id"])
                created = await issues.atomic_create(payload)
                issue_id = int(created["id"])
                counts["created"] += 1
            await db_engine.execute(
                _STAMP_ISSUE_SQL,
                {"issue_id": issue_id, "wf_id": row["dbos_workflow_id"]},
            )
        except Exception as exc:  # noqa: BLE001 — batch continues
            counts["skipped"] += 1
            logger.warning(
                f"[publish-mirror] mirror failed for {row.get('dbos_workflow_id')}: {exc!r}"
            )
    return counts


async def _sync_terminal_batches() -> dict[str, int]:
    from app.repositories.issue_repository import get_issue_repository

    issues = get_issue_repository()
    counts = {"synced": 0, "skipped": 0}
    rows = await db_engine.fetch_all(_MIRRORED_OPEN_SQL)
    for row in rows:
        try:
            target = terminal_sync_action(row.get("phase"), row.get("issue_status"))
            if target is None:
                counts["skipped"] += 1
                continue
            await issues.transition_status(int(row["issue_id"]), target)
            counts["synced"] += 1
        except Exception as exc:  # noqa: BLE001 — batch continues
            counts["skipped"] += 1
            logger.warning(
                f"[publish-mirror] terminal sync failed for issue {row.get('issue_id')}: {exc!r}"
            )
    return counts


@DBOS.scheduled("*/2 * * * *")
@DBOS.workflow()
async def publish_issue_mirror_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> dict[str, int]:
    """One sweep: mirror unmirrored batches, then sync terminal outcomes."""
    created = await _mirror_new_batches()
    synced = await _sync_terminal_batches()
    result = {**created, **synced}
    if result.get("created") or result.get("synced"):
        logger.info(f"[publish-mirror] sweep: {result}")
    return result
