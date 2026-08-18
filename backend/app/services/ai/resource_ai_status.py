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

from dataclasses import dataclass
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
_ALL_AI_TASK_TYPES = TRANSCRIPT_TASK_TYPES + SUMMARY_TASK_TYPES

# ⚠️ ``extract_audio`` alone does NOT mean a transcript is coming. The same
# task_type is created from four places with two completely different
# intents, and the difference decides whether any transcript will ever
# exist:
#
#   ai_router (manual transcribe click, both endpoints)  → chains
#       ``chain_transcription=True`` → extract_audio_workflow calls
#       ``chain_transcription_unconditional`` → a transcript WILL follow.
#   download.py (auto after every download) / media_fetch_router
#   ("Extract Audio" button)                             → does NOT chain
#       default ``chain_transcription=False`` → the workflow calls
#       ``chain_transcript_summary_for_tags``, which returns early unless
#       the resource carries a Transcript intent tag AND the user has a
#       ``user_settings`` row. Production: 14/14 historical extract_audio
#       rows are of this non-chaining kind.
#
# Treating the second kind as "transcription in flight" is what made the
# dedup answer "already in progress" to a request that then produced no
# transcription at all — a silent no-op (CLAUDE.md: every user-action →
# trigger path must return a typed result). ``chain_transcription`` is a
# frozen DBOS workflow **input**, so it cannot be flipped on a running
# task; instead each creation point records its intent here, in the
# metadata jsonb that route-C assigns to business code.
CHAIN_TRANSCRIPTION_KEY = "chain_transcription"


def task_chains_transcription(task_type: str, metadata: Any) -> bool:
    """True when this active task will actually produce a transcript.

    A row with no metadata (every row predating this field) counts as NOT
    chaining — the safe direction: the caller then tells the user to retry
    rather than promising a transcription that never comes.
    """
    if task_type == "ai_transcription":
        return True
    if task_type == "extract_audio":
        return bool((metadata or {}).get(CHAIN_TRANSCRIPTION_KEY))
    return False


# ``task_tracking.status`` values that mean "not finished". Copied from the
# predicate of migration 121's partial unique index
# (idx_task_tracking_active_per_resource_type) so "would the DB block a new
# insert" and "does the read path call it in-flight" cannot drift apart.
# In practice the mirror trigger only ever writes 'pending' / 'processing';
# 'queued' / 'running' are carried for the index's sake and for any legacy
# row that predates the trigger.
ACTIVE_TASK_STATUSES = ("pending", "queued", "processing", "running")
# ``phase`` is the finer-grained business column; the mirror trigger writes
# 'queued' / 'in_progress' for the same two lifecycle points. Note this
# makes the read predicate strictly WIDER than the index predicate above:
# for these three task_types the trigger writes the pair atomically, so the
# phase arm is unreachable today and exists only so a row whose status
# lagged still reads as in-flight rather than silently idle. Only the
# status arm is index-identical — do not describe the whole predicate as
# "same as migration 121".
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


def _active_ai_task_stmt(resource_ids: Sequence[str], task_types: Sequence[str]):
    """SELECT of every unfinished AI task for those resources.

    ``metadata`` rides along because the chaining intent is classified in
    Python, not in SQL: the row set here is tiny (a resource has at most a
    couple of active AI tasks), and keeping the predicate free of JSONB
    operators keeps it portable — the ResourceFetch end-to-end test runs
    this exact statement against SQLite.
    """
    from sqlalchemy import or_, select

    from app.models import TaskTracking

    return (
        select(
            TaskTracking.dbos_workflow_id,
            TaskTracking.resource_id,
            TaskTracking.task_type,
            TaskTracking.status,
            TaskTracking.phase,
            TaskTracking.metadata_.label("task_metadata"),
        )
        # resource_id is a TEXT column holding the snowflake as a string.
        .where(TaskTracking.resource_id.in_([str(r) for r in resource_ids]))
        .where(TaskTracking.task_type.in_(list(task_types)))
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


def _field_for_task(row: Mapping[str, Any]) -> str | None:
    """Which resources column this active task speaks for, if any.

    An ``extract_audio`` run that will NOT chain transcription speaks for
    neither column: it is real work, but no transcript will come out of it,
    so reporting ``transcript_status='processing'`` would tell the agent and
    the picker to wait for something that is never going to arrive.
    """
    task_type = row["task_type"]
    if task_type in SUMMARY_TASK_TYPES:
        return SUMMARY_STATUS_FIELD
    if task_chains_transcription(task_type, row.get("task_metadata")):
        return TRANSCRIPT_STATUS_FIELD
    return None


async def _active_ai_tasks(resource_ids: Sequence[str]) -> dict[str, dict[str, str]]:
    """``{resource_id: {column_name: 'pending' | 'processing'}}`` for every
    unfinished AI task covering those resources. One query, never per row."""
    if not resource_ids:
        return {}

    from app.db.session import read_scope

    stmt = _active_ai_task_stmt(resource_ids, _ALL_AI_TASK_TYPES)

    # task_tracking carries no scope mixin (it is keyed by dbos_workflow_id
    # and filtered by resource_id here), so a plain read session is enough —
    # same shape as the dedup SELECTs in ai_router.
    async with read_scope() as session:
        rows = (await session.execute(stmt)).mappings().all()

    out: dict[str, dict[str, str]] = {}
    for row in rows or []:
        field = _field_for_task(row)
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


@dataclass(frozen=True)
class ActiveTranscriptionTask:
    """An unfinished task occupying this resource's transcription slot.

    ``chains_transcription`` is the whole point: both kinds block a new
    dispatch (migration 121's unique index is per ``(resource_id,
    task_type)`` and does not read intent), but only one of them ends in a
    transcript. The caller must answer differently for each.
    """

    workflow_id: str
    task_type: str
    chains_transcription: bool


async def find_active_transcription_task(
    resource_id: str,
) -> ActiveTranscriptionTask | None:
    """The active transcription-slot task for ``resource_id``, if any.

    A chaining task wins when both kinds are present: it is the one that
    makes "already in progress" a true statement.
    """
    from app.db.session import read_scope

    stmt = _active_ai_task_stmt([resource_id], TRANSCRIPT_TASK_TYPES).limit(10)
    async with read_scope() as session:
        rows = (await session.execute(stmt)).mappings().all()

    found: ActiveTranscriptionTask | None = None
    for row in rows or []:
        task = ActiveTranscriptionTask(
            workflow_id=str(row["dbos_workflow_id"]),
            task_type=row["task_type"],
            chains_transcription=task_chains_transcription(
                row["task_type"], row.get("task_metadata")
            ),
        )
        if task.chains_transcription:
            return task
        found = found or task
    return found


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
    "CHAIN_TRANSCRIPTION_KEY",
    "TERMINAL_AI_STATUSES",
    "TRANSCRIPT_TASK_TYPES",
    "ActiveTranscriptionTask",
    "effective_ai_statuses",
    "find_active_transcription_task",
    "is_active_task_conflict",
    "task_chains_transcription",
]
