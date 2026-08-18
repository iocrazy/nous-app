"""Effective AI processing status for a resource.

``resources.transcript_status`` / ``summary_status`` are only ever written
with TERMINAL values: ``ai_transcription`` writes completed/failed,
``extract_audio`` writes failed, ``ai_summary`` writes completed. The
generic setter that could write the intermediate ones
(``ai_repository.update_media_ai_status``) has zero call sites, and the
production DB agrees — of 1421 resource rows, ``pending`` / ``processing``
/ ``skipped`` hold 0 each (measured 2026-08-17). Every consumer branching
on "is this being processed right now" was therefore branching on a value
that never arrives.

The fix is deliberately a READ-path fix. Teaching the columns to hold
``processing`` would create a second lifecycle holder that nothing clears:
a worker killed mid-transcription leaves the column stuck forever, and the
UI would then confidently show "processing" for a job that died days ago —
the exact "state holder that lies" failure this codebase keeps paying for.
``task_tracking`` already holds the truth (the DBOS mirror trigger owns
``status`` / ``phase``, and the stall reaper cleans up abandoned rows), so
the in-flight half is derived from there at read time. Route-C compliant:
this only READS ``task_tracking``, and never touches its trigger-owned
columns.

Rules, in order:
  1. a terminal column value wins outright — once work has finished the
     column is the authority (content exists / is known-missing);
  2. otherwise an active ``task_tracking`` row for this resource maps to
     ``processing`` (running) or ``pending`` (queued);
  3. otherwise the column value passes through untouched.

Wire contract is UNCHANGED for every consumer:
``none | pending | processing | completed | failed | skipped``, plus
``None`` for row shapes that carry no column at all.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.utils.ai_status import ai_status_str

# The two ``resources`` columns this module resolves. Keys are the column
# names so a consumer can ``dict.update()`` the result straight onto a row.
TRANSCRIPT_STATUS_FIELD = "transcript_status"
SUMMARY_STATUS_FIELD = "summary_status"
_STATUS_FIELDS = (TRANSCRIPT_STATUS_FIELD, SUMMARY_STATUS_FIELD)

# Work has finished (well or badly) — the column, not the task table,
# decides. A queued re-run must not hide an already-usable transcript.
TERMINAL_AI_STATUSES = frozenset({"completed", "failed", "skipped"})

# The transcribe endpoint dispatches ``extract_audio`` (chain_transcription=
# True) whenever the media has no audio track yet, so an in-flight
# transcription legitimately shows up under either task_type.
TRANSCRIPT_TASK_TYPES = ("ai_transcription", "extract_audio")
SUMMARY_TASK_TYPES = ("ai_summary",)
_FIELD_BY_TASK_TYPE = {
    **{t: TRANSCRIPT_STATUS_FIELD for t in TRANSCRIPT_TASK_TYPES},
    **{t: SUMMARY_STATUS_FIELD for t in SUMMARY_TASK_TYPES},
}
_ALL_AI_TASK_TYPES = tuple(_FIELD_BY_TASK_TYPE)

# ``task_tracking.status`` values that mean "not finished". Copied from the
# predicate of migration 121's partial unique index
# (idx_task_tracking_active_per_resource_type) so "would the DB block a new
# insert" and "does the read path call it in-flight" cannot drift apart.
# In practice the mirror trigger only ever writes 'pending' / 'processing';
# 'queued' / 'running' are carried for the index's sake and for any legacy
# row that predates the trigger.
ACTIVE_TASK_STATUSES = ("pending", "queued", "processing", "running")
# ``phase`` is the finer-grained business column; the mirror trigger writes
# 'queued' / 'in_progress' for the same two lifecycle points.
_ACTIVE_TASK_PHASES = ("queued", "in_progress")
_TERMINAL_TASK_STATUSES = ("completed", "failed", "cancelled", "lost")
_RUNNING_TASK_STATES = frozenset({"processing", "running", "in_progress"})

# Migration 121's partial unique index. An INSERT that trips it means
# another active task already covers this (resource_id, task_type) — the
# migration's own comment tells callers to treat it as "already in
# progress" rather than an error.
ACTIVE_TASK_UNIQUE_INDEX = "idx_task_tracking_active_per_resource_type"


def is_active_task_conflict(exc: BaseException) -> bool:
    """True when ``exc`` is migration 121's unique index rejecting a
    duplicate active task (as opposed to any other integrity error).

    Checks the driver-supplied constraint name first (asyncpg sets it on
    ``UniqueViolationError``), falling back to the rendered message that
    SQLAlchemy wraps around it.
    """
    name = getattr(getattr(exc, "orig", None), "constraint_name", None)
    if name == ACTIVE_TASK_UNIQUE_INDEX:
        return True
    return ACTIVE_TASK_UNIQUE_INDEX in str(exc)


def _derive_from_task(status: Any, phase: Any) -> str:
    """Map one active task row to the wire status the consumer renders."""
    state = {ai_status_str(status), ai_status_str(phase)}
    return "processing" if state & _RUNNING_TASK_STATES else "pending"


async def _active_ai_tasks(resource_ids: Sequence[str]) -> dict[str, dict[str, str]]:
    """``{resource_id: {column_name: 'pending' | 'processing'}}`` for every
    unfinished AI task covering those resources. One query, never per row."""
    if not resource_ids:
        return {}

    from sqlalchemy import or_, select

    from app.db.session import read_scope
    from app.models import TaskTracking

    stmt = (
        select(
            TaskTracking.resource_id,
            TaskTracking.task_type,
            TaskTracking.status,
            TaskTracking.phase,
        )
        # resource_id is a TEXT column holding the snowflake as a string.
        .where(TaskTracking.resource_id.in_([str(r) for r in resource_ids]))
        .where(TaskTracking.task_type.in_(_ALL_AI_TASK_TYPES))
        # status is the authoritative lifecycle mirror, so a terminal
        # status vetoes an in-flight phase (agent_task rows keep an
        # 8-state phase that can lag behind their own completion).
        .where(TaskTracking.status.notin_(_TERMINAL_TASK_STATUSES))
        .where(
            or_(
                TaskTracking.status.in_(ACTIVE_TASK_STATUSES),
                TaskTracking.phase.in_(_ACTIVE_TASK_PHASES),
            )
        )
    )

    # task_tracking carries no scope mixin (it is keyed by dbos_workflow_id
    # and filtered by resource_id here), so a plain read session is enough —
    # same shape as the dedup SELECTs in ai_router.
    async with read_scope() as session:
        rows = (await session.execute(stmt)).mappings().all()

    out: dict[str, dict[str, str]] = {}
    for row in rows or []:
        field = _FIELD_BY_TASK_TYPE.get(row["task_type"])
        if field is None:
            continue
        # Belt and braces: the WHERE already excludes finished rows, but a
        # reducer that reports "pending" for anything it fails to classify
        # would turn a future predicate slip into a permanently-stuck
        # "processing" badge rather than a visible error.
        if ai_status_str(row["status"]) in _TERMINAL_TASK_STATUSES:
            continue
        derived = _derive_from_task(row["status"], row["phase"])
        bucket = out.setdefault(str(row["resource_id"]), {})
        # 'processing' outranks 'pending': extract_audio running with a
        # transcription queued behind it is work in progress, not a wait.
        if bucket.get(field) != "processing":
            bucket[field] = derived
    return out


async def effective_ai_statuses(
    rows: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, str | None]]:
    """Resolve the effective transcript/summary status for each resource.

    ``rows`` maps ``resource_id`` to anything dict-like carrying the two
    ``resources`` columns (an ORM row mapping, a picker result dict, the
    ResourceFetch access-check row). The return value has the same keys and
    is safe to ``update()`` onto the caller's own dict.
    """
    if not rows:
        return {}

    columns: dict[str, dict[str, str | None]] = {
        str(rid): {f: ai_status_str(row.get(f)) for f in _STATUS_FIELDS}
        for rid, row in rows.items()
    }

    # Only resources with at least one unfinished column can change, so a
    # page of fully-processed results costs no query at all.
    needs_lookup = [
        rid
        for rid, cols in columns.items()
        if any(v not in TERMINAL_AI_STATUSES for v in cols.values())
    ]
    active = await _active_ai_tasks(needs_lookup)

    return {
        rid: {
            f: (
                cols[f]
                if cols[f] in TERMINAL_AI_STATUSES
                else (active.get(rid, {}).get(f) or cols[f])
            )
            for f in _STATUS_FIELDS
        }
        for rid, cols in columns.items()
    }


__all__ = [
    "ACTIVE_TASK_STATUSES",
    "ACTIVE_TASK_UNIQUE_INDEX",
    "TERMINAL_AI_STATUSES",
    "TRANSCRIPT_TASK_TYPES",
    "effective_ai_statuses",
    "is_active_task_conflict",
]
