"""workflow_health_sweeper — DBOS-scheduled classifier for active workflows.

Runs every 2 minutes. Classifies every active task_tracking row using
the SQL `classify_workflow_health` function (mig 201) and acts ONLY on
two states:

  * LOST            — heartbeat stale beyond policy → worker died, the
                      row is sitting around with nobody driving it.
                      Mark phase=lost so the UI shows the right state.
                      Does NOT touch user data — the row stays around
                      so the user can see what happened.
  * ORPHAN_PENDING  — workflow stuck in PENDING for >3× the type's hard
                      ceiling, never picked up by the executor. The body
                      never ran, so cancelling is safe (no partial state
                      to clean up). Skipped if do_not_auto_cancel is on.
  * USER_TIMEOUT    — user-set max_duration_minutes exceeded. Auto-cancel
                      because the user opted in by setting the field.

Everything else (HEALTHY / SLOW / STUCK_IN_STEP / STALLED) is purely
informational — recorded in `health_status` for the UI to surface, with
a notification emitted at most once per state transition.

Why we don't auto-cancel SLOW / STALLED
---------------------------------------
A 6-hour transcription that's making slow progress is *legitimate work*.
Killing it because elapsed > expected loses real user data. The
classifier surfaces the slowness as a notification ("your transcription
is taking longer than usual, want to cancel?") and lets the user decide.
Per-task `do_not_auto_cancel` and `max_duration_minutes` give the user
full control.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict

from dbos import DBOS
from loguru import logger

from app.services.infra.dbos_orchestrator import _resolve_pinned_app_version

# Workflows on this queue (and `sched-*` workflows) are DBOS-internal
# housekeeping — reaped by `_pre_launch_sweep_stale_scheduled`, NEVER by the
# user-facing zombie reaper below.
_INTERNAL_QUEUE = "_dbos_internal_queue"


@DBOS.step()
async def classify_and_act_step() -> Dict[str, int]:
    """Classify every active workflow + take only safe auto-actions.

    Returns counters by classification for `/health/deep` and operator
    log greps:
        {"healthy": N, "slow": N, "stalled": N, "lost": N,
         "orphan_pending": N, "user_timeout": N, "auto_cancelled": N,
         "lost_marked": N}

    Async because `asyncio.run()` from a sync step body cascades into
    DBOS shared-executor shutdown — same pattern documented in
    scheduled_commitment_sweeper.py header (durable fix #188 era).
    Without this, every 2-min sweep tick crashed the executor and
    contributed to the parse-failure / slow-workflow symptoms reported
    after #176 deployed.
    """
    # Direct PG via SQLAlchemy engine (no httpx) — supabase-py's PostgREST
    # path leaked a CLOSE_WAIT connection per call (Issue #199 Bug C).
    from app.db import engine as db_engine

    # Skip gracefully when Supavisor isn't configured (dev/CI) instead of
    # crash-looping every 2 min on the engine's RuntimeError.
    if not db_engine.is_configured():
        return _zero_counters()

    # Fetch active rows with the columns the classifier needs.
    #
    # Active phase is 'processing', NOT 'in_progress': the DBOS lifecycle
    # trigger maps RUNNING -> 'processing' (migration 209/219), confirmed
    # against prod task_tracking. The old 'in_progress' literal never matched,
    # so the sweeper silently skipped every running workflow (no LOST /
    # USER_TIMEOUT detection for in-flight work). Mirrors get_queue_status.
    rows = await db_engine.fetch_all(
        "SELECT dbos_workflow_id, task_type, phase, started_at, "
        "heartbeat_at, progress, updated_at, max_duration_minutes, "
        "do_not_auto_cancel, health_status, user_id, title "
        "FROM public.task_tracking WHERE phase IN ('queued', 'processing')"
    )
    if not rows:
        return _zero_counters()

    # Classify in PG via the shared SQL function so the logic stays
    # in one place (admin can `SELECT classify_workflow_health(...)`
    # too). One round-trip per row keeps the code simple — N is
    # bounded by active workflows (typically <100).
    # G2: within the post-boot grace window, classify + persist for the UI
    # but take NO destructive reconciliation action. A deploy/restart leaves
    # started_at + heartbeat stale across the gap; DBOS is concurrently
    # recovering those workflows. Marking them LOST/cancelled now would steal
    # in-flight work that is about to resume.
    from app.workflows.sweep_guard import within_boot_grace

    in_grace = within_boot_grace()

    counters = _zero_counters()
    for row in rows:
        classification = await _classify_one(row)
        counters[classification.lower()] = counters.get(classification.lower(), 0) + 1

        # Persist the classification so the UI can surface it.
        await _persist_classification(row, classification)

        if in_grace:
            # Skip all LOST/stuck/timeout marking until grace elapses.
            continue

        # Take action only on the auto-action states.
        if classification == "LOST":
            # G3: don't steal a row DBOS still owns (PENDING/ENQUEUED). It
            # will resume or finalize the workflow — the lifecycle trigger
            # then mirrors the real outcome to task_tracking.
            if await _dbos_still_owns(row.get("dbos_workflow_id")):
                logger.info(
                    "[workflow_health] skip LOST (DBOS still owns): "
                    f"workflow_id={row.get('dbos_workflow_id')} "
                    f"task_type={row.get('task_type')}"
                )
            else:
                await _mark_lost(row)
                counters["lost_marked"] += 1
        elif classification == "ORPHAN_PENDING" and not row.get("do_not_auto_cancel"):
            await _cancel_orphan(row)
            counters["auto_cancelled"] += 1
        elif classification == "USER_TIMEOUT" and not row.get("do_not_auto_cancel"):
            await _mark_timed_out(row)
            counters["auto_cancelled"] += 1
    return counters


def _zero_counters() -> Dict[str, int]:
    return {
        "healthy": 0,
        "slow": 0,
        "stalled": 0,
        "stuck_in_step": 0,
        "lost": 0,
        "orphan_pending": 0,
        "user_timeout": 0,
        "auto_cancelled": 0,
        "lost_marked": 0,
    }


async def _classify_one(row: Dict[str, Any]) -> str:
    """Call the SQL classifier with the row's fields. PostgREST RPC route
    not used because of name conflicts with internal pg_catalog (see
    reference_postgrest_rpc.md in mediahub memory). Direct SQL instead.
    """
    try:
        # postgrest doesn't expose arbitrary SQL; use a wrapper rpc if
        # you have one, or fall back to embedding the logic in Python.
        # For now classify in Python to avoid an extra round-trip.
        return _classify_in_python(row)
    except Exception as exc:
        logger.opt(exception=True).warning(
            f"[workflow_health] classify failed for "
            f"{row.get('dbos_workflow_id')}: {exc}"
        )
        return "HEALTHY"


# ── Policy cache ───────────────────────────────────────────────────
# Read once per sweeper invocation so we don't query 100×N times. Stale
# cache between ticks is fine — policy rarely changes.
_POLICY_CACHE: Dict[str, Dict[str, int]] = {}
_POLICY_CACHE_AT: float = 0.0


async def _refresh_policy() -> Dict[str, Dict[str, int]]:
    global _POLICY_CACHE, _POLICY_CACHE_AT
    import time

    from app.db import engine as db_engine

    now = time.time()
    if _POLICY_CACHE and (now - _POLICY_CACHE_AT) < 120:
        return _POLICY_CACHE
    if not db_engine.is_configured():
        return _POLICY_CACHE
    policy_rows = await db_engine.fetch_all(
        "SELECT task_type, expected_duration_seconds, hard_ceiling_seconds, "
        "heartbeat_stale_seconds FROM public.workflow_timeout_policy"
    )
    cache: Dict[str, Dict[str, int]] = {}
    for r in policy_rows:
        cache[r["task_type"]] = {
            "expected": r["expected_duration_seconds"],
            "hard": r["hard_ceiling_seconds"],
            "heartbeat_stale": r["heartbeat_stale_seconds"],
        }
    _POLICY_CACHE = cache
    _POLICY_CACHE_AT = now
    return cache


def _parse_iso(value: Any) -> "datetime | None":
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _classify_in_python(row: Dict[str, Any]) -> str:
    """Python re-implementation of classify_workflow_health() for hot
    classification without an extra PG round-trip. Must match the SQL
    function — change them together.
    """
    from datetime import timezone

    phase = row.get("phase")
    started = _parse_iso(row.get("started_at"))
    heartbeat = _parse_iso(row.get("heartbeat_at"))
    updated = _parse_iso(row.get("updated_at"))
    task_type = row.get("task_type") or ""
    user_max_min = row.get("max_duration_minutes")

    policy = _POLICY_CACHE.get(task_type)
    if not policy:
        return "HEALTHY"  # unknown type, benefit of doubt

    now = datetime.now(timezone.utc)
    if phase == "queued":
        elapsed = (now - (started or now)).total_seconds()
        if elapsed > policy["hard"] * 3:
            return "ORPHAN_PENDING"
        return "HEALTHY"

    if started is None:
        return "HEALTHY"

    elapsed = (now - started).total_seconds()
    progress_age = (now - updated).total_seconds() if updated else elapsed

    if heartbeat is not None:
        # We have a heartbeat signal — stale heartbeat = worker died.
        heartbeat_age = (now - heartbeat).total_seconds()
        if heartbeat_age > policy["heartbeat_stale"]:
            return "LOST"
    else:
        # No heartbeat to be "stale". Download/soda workflows write NO
        # heartbeat (heartbeat_at always NULL), so the heartbeat signal is
        # meaningless for them — using it would mark EVERY download past
        # heartbeat_stale (600s) as LOST at 10 min of normal runtime (G1).
        # Fall back to the absolute `hard` ceiling on elapsed: only LOST
        # once the workflow has run past its hard maximum.
        if elapsed > policy["hard"]:
            return "LOST"

    if user_max_min is not None and elapsed > user_max_min * 60:
        return "USER_TIMEOUT"

    if elapsed <= policy["expected"]:
        if progress_age > policy["expected"] / 2:
            return "STUCK_IN_STEP"
        return "HEALTHY"

    if progress_age > policy["expected"] / 2:
        return "STALLED"
    return "SLOW"


# ── DBOS ownership guard (G3) ──────────────────────────────────────
# Before flipping a task to LOST/failed, consult dbos.workflow_status:
# if DBOS still owns the row (PENDING/ENQUEUED), it will resume or
# finalize the workflow — we must NOT mark it LOST and steal the row out
# from under the engine. Only mark LOST when DBOS has no live/queued claim
# (no row at all, or a terminal status that simply didn't mirror yet).
#
# VERSION-AWARENESS (post-deploy version-orphan): DBOS partitions workflows
# by `application_version` (= the build's commit_sha, _resolve_pinned_app_version).
# After a deploy the worker runs a NEW commit_sha, so a workflow still
# PENDING/ENQUEUED tagged with the OLD commit_sha is permanently orphaned —
# no executor of that version exists anymore, DBOS will never recover it. Such
# a row LOOKS recoverable (PENDING) but never will be, so it is NOT a real
# claim → the sweeper must be allowed to mark it LOST. We only flip to
# "not owned" when BOTH versions are known AND differ; if either version is
# unknown (dev / no build-info), we fail-closed to the old "owned" behavior.

_DBOS_LIVE_STATUSES = frozenset({"PENDING", "ENQUEUED"})


def _dbos_claims_workflow(
    status: "str | None",
    app_version: "str | None" = None,
    current_version: "str | None" = None,
) -> bool:
    """Pure decision: True when the DBOS status means the engine still
    owns / will recover the workflow, so the sweeper must SKIP marking it
    LOST. PENDING/ENQUEUED → owned; SUCCESS/ERROR/CANCELLED/None → no claim.

    Version-orphan rule: a PENDING/ENQUEUED workflow whose `app_version`
    differs from the live `current_version` is NOT a real claim (no executor
    of that old version exists post-deploy) → return False. Fail-closed: when
    either version is unknown (None), keep the "owned" behavior. The default
    args keep every status-only caller (recovery + tests) behaving as before.
    """
    if not status:
        return False
    if status.strip().upper() not in _DBOS_LIVE_STATUSES:
        return False
    if current_version and app_version and app_version != current_version:
        return False  # version-orphan: PENDING but no live executor of its version
    return True


def _is_permanent_dbos_orphan(
    status: "str | None",
    queue_name: "str | None",
    name: "str | None",
    app_version: "str | None",
    current_version: "str | None",
    age_seconds: float,
    max_age_seconds: float,
) -> bool:
    """Pure decision: True when a `dbos.workflow_status` row is a permanently-
    orphaned USER workflow that will never run and should be cancelled (the
    "zombie" download/parse). Tested without a DB.

    A zombie is ALL of:
      * PENDING / ENQUEUED — anything terminal already agrees with the UI.
      * user-facing — NOT on `_dbos_internal_queue`, NOT a `sched-*` workflow
        (those go through `_pre_launch_sweep_stale_scheduled`).
      * permanently orphaned — EITHER a post-deploy version-orphan
        (`app_version != current_version`, both known: no executor of that
        version exists) OR frozen in PENDING/ENQUEUED past `max_age_seconds`
        (worker died and DBOS recovery never re-claimed it).

    Conservative: a same-version row younger than `max_age_seconds` is NOT a
    zombie (real backlog item or one DBOS will still recover). When either
    version is unknown we fall through to the age backstop only — never cancel
    young work on a version we can't compare.
    """
    if not status or status.strip().upper() not in _DBOS_LIVE_STATUSES:
        return False
    if (queue_name or "") == _INTERNAL_QUEUE:
        return False
    if (name or "").startswith("sched-"):
        return False
    # Post-deploy version-orphan: both versions known and different → no live
    # executor of that version will ever pick this up.
    if current_version and app_version and app_version != current_version:
        return True
    # Age backstop: frozen far beyond any realistic queue wait.
    return age_seconds >= max_age_seconds


def _zombie_max_age_seconds() -> float:
    """Frozen-PENDING age past which a same-version user workflow is presumed a
    dead orphan. Generous default (6h) so a genuine queue backlog is never
    cancelled — the precise signal is the version-orphan check; this is only a
    backstop for same-version rows a dead worker left behind. Env-overridable."""
    try:
        return float(os.environ.get("DBOS_ZOMBIE_MAX_AGE_SECONDS", str(6 * 3600)))
    except (TypeError, ValueError):
        return 6 * 3600.0


async def _dbos_status_row(
    dbos_workflow_id: "str | None",
) -> "tuple[str | None, str | None]":
    """Returns (status, application_version) for this row's workflow_uuid, or
    (None, None) when there's no row. One indexed SELECT on
    dbos.workflow_status. RAISES on a DB error — the caller
    (`_dbos_still_owns`) decides the fail policy.
    """
    if not dbos_workflow_id:
        return (None, None)
    from app.db import engine as db_engine

    row = await db_engine.fetch_one(
        "SELECT status, application_version FROM dbos.workflow_status "
        "WHERE workflow_uuid = :wid",
        {"wid": dbos_workflow_id},
    )
    if not row:
        return (None, None)
    return (row.get("status"), row.get("application_version"))


async def _dbos_still_owns(dbos_workflow_id: "str | None") -> bool:
    """True when DBOS still has a live/queued claim on the workflow (caller
    must then SKIP marking it LOST — DBOS will recover/finalize it).

    Tasks with no dbos_workflow_id → False (current behavior). FAIL-CLOSED:
    on a DB error return True (treat as owned → skip). The whole point of this
    guard is to stop wrongly failing recoverable tasks, so a transient
    `dbos.workflow_status` hiccup must NOT cause a wrong LOST — the next sweep
    tick retries, and a genuinely-dead task is still gated by hard-ceiling +
    boot-grace, so skipping one tick is harmless.

    Version-aware: a PENDING/ENQUEUED workflow tagged with a different
    application_version than the live build is a post-deploy version-orphan
    (no executor of that version exists) → NOT owned → caller may mark it LOST.
    """
    if not dbos_workflow_id:
        return False
    try:
        status, app_version = await _dbos_status_row(dbos_workflow_id)
        current = _resolve_pinned_app_version()
        return _dbos_claims_workflow(status, app_version, current)
    except Exception as exc:
        logger.opt(exception=True).debug(
            f"[workflow_health] _dbos_status_row lookup failed for "
            f"{dbos_workflow_id}; treating as owned (skip mark): {exc}"
        )
        return True


# NOTE on the writers below: _mark_lost / _cancel_orphan / _mark_timed_out
# set task_tracking.phase + status directly, which is normally reserved for
# the mirror_dbos_lifecycle_to_tracking trigger ("phase/status by trigger
# only", CLAUDE.md). This is a DELIBERATE exception: these reconciliation
# paths handle workflows whose DBOS executor is dead/lost/orphaned, so the
# trigger will never fire for them — the sweeper is the writer of last
# resort. Do not "fix" this back to trigger-only.


async def _persist_classification(row: Dict[str, Any], classification: str) -> None:
    """Write the new classification to task_tracking.health_status. Skip
    the write when nothing changed to avoid Realtime fanout noise."""
    if row.get("health_status") == classification:
        return
    from app.db import engine as db_engine

    try:
        await db_engine.execute(
            "UPDATE public.task_tracking SET health_status = :cls "
            "WHERE dbos_workflow_id = :wid",
            {"cls": classification, "wid": row["dbos_workflow_id"]},
        )
    except Exception as exc:
        logger.opt(exception=True).debug(
            f"[workflow_health] persist failed for "
            f"{row.get('dbos_workflow_id')}: {exc}"
        )


async def _mark_lost(row: Dict[str, Any]) -> None:
    """Mark a LOST row's phase=lost / status=failed so the UI shows it
    correctly. The row stays around — operator can inspect."""
    from app.db import engine as db_engine

    try:
        await db_engine.execute(
            "UPDATE public.task_tracking SET phase = 'lost', "
            "status = 'failed', error_code = 'worker_lost', "
            "error_msg = 'Worker heartbeat went stale; presumed dead.', "
            "completed_at = :done WHERE dbos_workflow_id = :wid",
            {"done": datetime.now(timezone.utc), "wid": row["dbos_workflow_id"]},
        )
        logger.warning(
            f"[workflow_health] marked LOST: workflow_id={row['dbos_workflow_id']} "
            f"task_type={row.get('task_type')} title={row.get('title')!r}"
        )
    except Exception as exc:
        logger.opt(exception=True).warning(
            f"[workflow_health] _mark_lost failed: {exc}"
        )


async def _cancel_orphan(row: Dict[str, Any]) -> None:
    """Cancel an ORPHAN_PENDING row (DBOS executor never picked it up).
    Safe because the workflow body never executed — no partial state."""
    from app.db import engine as db_engine

    try:
        await db_engine.execute(
            "UPDATE public.task_tracking SET phase = 'cancelled', "
            "status = 'cancelled', error_code = 'executor_orphan', "
            "error_msg = 'Workflow stuck PENDING beyond hard ceiling x 3; "
            "DBOS executor never picked it up.', "
            "completed_at = :done WHERE dbos_workflow_id = :wid",
            {"done": datetime.now(timezone.utc), "wid": row["dbos_workflow_id"]},
        )
        logger.warning(
            f"[workflow_health] cancelled ORPHAN: workflow_id={row['dbos_workflow_id']} "
            f"task_type={row.get('task_type')}"
        )
    except Exception as exc:
        logger.opt(exception=True).warning(
            f"[workflow_health] _cancel_orphan failed: {exc}"
        )


async def _mark_timed_out(row: Dict[str, Any]) -> None:
    """Mark a USER_TIMEOUT row as timed_out. User opted in via
    max_duration_minutes; this is consensual auto-cancel."""
    from app.db import engine as db_engine

    error_msg = (
        f"Exceeded user-set max_duration_minutes={row.get('max_duration_minutes')}."
    )
    try:
        await db_engine.execute(
            "UPDATE public.task_tracking SET phase = 'timed_out', "
            "status = 'failed', error_code = 'user_timeout', "
            "error_msg = :msg, completed_at = :done "
            "WHERE dbos_workflow_id = :wid",
            {
                "msg": error_msg,
                "done": datetime.now(timezone.utc),
                "wid": row["dbos_workflow_id"],
            },
        )
        logger.warning(
            f"[workflow_health] timed out: workflow_id={row['dbos_workflow_id']} "
            f"max_min={row.get('max_duration_minutes')}"
        )
    except Exception as exc:
        logger.opt(exception=True).warning(
            f"[workflow_health] _mark_timed_out failed: {exc}"
        )


@DBOS.step()
async def reap_dbos_zombies_step() -> Dict[str, int]:
    """Cancel permanently-orphaned PENDING/ENQUEUED USER workflows directly in
    `dbos.workflow_status`, then reconcile their `task_tracking` row.

    These are the "zombie" downloads/parses (diagnosed 2026-06-20): a worker
    died/redeployed mid-flight, DBOS left the workflow PENDING tagged with an
    OLD `application_version` (no executor of that version exists → DBOS
    recovery never re-claims it), while `reap_stuck_pending_tasks` /
    `_mark_lost` independently flipped the `task_tracking` row to a terminal
    state. The two tables then diverge forever — engine says PENDING (a phantom
    "running" task), UI reads `task_tracking` and shows lost/nothing. Neither
    existing sweeper cancels the engine row, so it lingers indefinitely.

    Cancelling = `UPDATE dbos.workflow_status SET status='CANCELLED'` (DBOS
    recovery skips CANCELLED — the same blessed method as
    `_pre_launch_sweep_stale_scheduled`, which only handles `sched-*`). User
    workflows on real queues are this reaper's job. The `task_tracking` row is
    reconciled to the same terminal state `_mark_lost` writes so the UI shows a
    retryable lost task (backend `retry_task` already accepts lost rows).

    Skips boot grace (DBOS is recovering in-flight work then). Internal-queue /
    `sched-*` rows are excluded — they belong to the pre-launch sweep.
    """
    from app.db import engine as db_engine
    from app.workflows.sweep_guard import within_boot_grace

    if not db_engine.is_configured():
        return {"zombies_cancelled": 0}
    if within_boot_grace():
        return {"zombies_cancelled": 0, "skipped_boot_grace": 1}

    current_version = _resolve_pinned_app_version()
    max_age = _zombie_max_age_seconds()

    # Candidate user-facing PENDING/ENQUEUED rows. The 180s floor avoids racing
    # freshly-enqueued work; precise version/age filtering is the pure helper's
    # job. Bounded LIMIT keeps one tick cheap even after a bad deploy.
    rows = await db_engine.fetch_all(
        "SELECT workflow_uuid, queue_name, name, application_version, status, "
        "EXTRACT(EPOCH FROM (now() - to_timestamp(updated_at / 1000.0))) AS age_s "
        "FROM dbos.workflow_status "
        "WHERE status IN ('PENDING', 'ENQUEUED') "
        "AND queue_name <> :iq AND name NOT LIKE 'sched-%' "
        "AND updated_at / 1000.0 < EXTRACT(EPOCH FROM now()) - 180 "
        "ORDER BY updated_at ASC LIMIT 200",
        {"iq": _INTERNAL_QUEUE},
    )

    cancelled = 0
    for row in rows or []:
        if not _is_permanent_dbos_orphan(
            row.get("status"),
            row.get("queue_name"),
            row.get("name"),
            row.get("application_version"),
            current_version,
            float(row.get("age_s") or 0.0),
            max_age,
        ):
            continue
        if await _cancel_dbos_zombie(row.get("workflow_uuid")):
            cancelled += 1

    if cancelled:
        logger.warning(
            f"[workflow_health] reaped {cancelled} DBOS zombie workflow(s) "
            "(PENDING engine orphans the UI never showed)"
        )
    return {"zombies_cancelled": cancelled}


async def _cancel_dbos_zombie(workflow_uuid: "str | None") -> bool:
    """Cancel one engine zombie + reconcile its task_tracking row. Returns True
    when this call actually cancelled it (False if it raced to terminal or on
    error). The CANCELLED guard keeps it idempotent across overlapping ticks."""
    if not workflow_uuid:
        return False
    from app.db import engine as db_engine

    try:
        n = await db_engine.execute(
            "UPDATE dbos.workflow_status "
            "SET status = 'CANCELLED', updated_at = EXTRACT(EPOCH FROM now()) * 1000 "
            "WHERE workflow_uuid = :wid AND status IN ('PENDING', 'ENQUEUED')",
            {"wid": workflow_uuid},
        )
        if not n:
            return False  # raced — another tick / the engine already finalized it
        # Reconcile the UI row to the same terminal state _mark_lost writes:
        # status='failed' (UI-known, retryable) + phase='lost' (operator signal).
        # Guard against clobbering a genuinely-completed row whose lifecycle
        # trigger lagged.
        await db_engine.execute(
            "UPDATE public.task_tracking SET phase = 'lost', status = 'failed', "
            "error_code = 'worker_lost', error_msg = 'Worker died before "
            "executing; stale engine task cleared. Use Retry to re-queue.', "
            "completed_at = :done WHERE dbos_workflow_id = :wid "
            "AND status <> 'completed'",
            {"done": datetime.now(timezone.utc), "wid": workflow_uuid},
        )
        return True
    except Exception as exc:
        logger.opt(exception=True).warning(
            f"[workflow_health] _cancel_dbos_zombie failed for {workflow_uuid}: {exc}"
        )
        return False


@DBOS.scheduled("*/2 * * * *")  # every 2 minutes
@DBOS.workflow()
async def workflow_health_sweeper_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """One sweeper tick. DBOS dedups via the standard `sched-<name>-<iso>`
    workflow_id, so cluster-wide only one worker fires per scheduled time.

    Refresh the policy cache before classifying so a hot edit to
    workflow_timeout_policy takes effect within 2 minutes without
    restart.

    Async because the previous `def + asyncio.run()` pattern cascades
    into DBOS shared-executor shutdown — see
    scheduled_commitment_sweeper.py header for the gory details and
    the durable fix landed in #188. Hot-fix follow-up to #176.
    """
    await _refresh_policy()

    counters = await classify_and_act_step()
    # Layer 2: cancel engine-side zombies (PENDING in dbos.workflow_status that
    # the UI never showed). Separate step from classify_and_act because it scans
    # dbos.workflow_status directly, not active task_tracking rows.
    counters["zombies_cancelled"] = (await reap_dbos_zombies_step()).get(
        "zombies_cancelled", 0
    )
    if (
        counters["lost_marked"]
        or counters["auto_cancelled"]
        or counters["slow"]
        or counters["stalled"]
        or counters["lost"]
        or counters["orphan_pending"]
        or counters["zombies_cancelled"]
    ):
        logger.info(f"[workflow_health] tick: {counters}")
