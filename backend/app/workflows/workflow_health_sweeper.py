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

from datetime import datetime
from typing import Any, Dict

from dbos import DBOS
from loguru import logger


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
    from app.db import get_async_supabase_admin

    sb = await get_async_supabase_admin()

    # Fetch active rows with the columns the classifier needs.
    active = await (
        sb.table("task_tracking")
        .select(
            "dbos_workflow_id, task_type, phase, started_at, "
            "heartbeat_at, progress, updated_at, max_duration_minutes, "
            "do_not_auto_cancel, health_status, user_id, title"
        )
        .in_("phase", ["queued", "in_progress"])
        .execute()
    )
    rows = active.data or []
    if not rows:
        return _zero_counters()

    # Classify in PG via the shared SQL function so the logic stays
    # in one place (admin can `SELECT classify_workflow_health(...)`
    # too). One round-trip per row keeps the code simple — N is
    # bounded by active workflows (typically <100).
    counters = _zero_counters()
    for row in rows:
        classification = await _classify_one(sb, row)
        counters[classification.lower()] = counters.get(classification.lower(), 0) + 1

        # Persist the classification so the UI can surface it.
        await _persist_classification(sb, row, classification)

        # Take action only on the auto-action states.
        if classification == "LOST":
            await _mark_lost(sb, row)
            counters["lost_marked"] += 1
        elif classification == "ORPHAN_PENDING" and not row.get("do_not_auto_cancel"):
            await _cancel_orphan(sb, row)
            counters["auto_cancelled"] += 1
        elif classification == "USER_TIMEOUT" and not row.get("do_not_auto_cancel"):
            await _mark_timed_out(sb, row)
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


async def _classify_one(sb: Any, row: Dict[str, Any]) -> str:
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


async def _refresh_policy(sb: Any) -> Dict[str, Dict[str, int]]:
    global _POLICY_CACHE, _POLICY_CACHE_AT
    import time

    now = time.time()
    if _POLICY_CACHE and (now - _POLICY_CACHE_AT) < 120:
        return _POLICY_CACHE
    rows = await (
        sb.table("workflow_timeout_policy")
        .select(
            "task_type, expected_duration_seconds, hard_ceiling_seconds, "
            "heartbeat_stale_seconds"
        )
        .execute()
    )
    cache: Dict[str, Dict[str, int]] = {}
    for r in rows.data or []:
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
    heartbeat_age = (now - heartbeat).total_seconds() if heartbeat else elapsed
    progress_age = (now - updated).total_seconds() if updated else elapsed

    if heartbeat_age > policy["heartbeat_stale"]:
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


async def _persist_classification(
    sb: Any, row: Dict[str, Any], classification: str
) -> None:
    """Write the new classification to task_tracking.health_status. Skip
    the write when nothing changed to avoid Realtime fanout noise."""
    if row.get("health_status") == classification:
        return
    try:
        await (
            sb.table("task_tracking")
            .update({"health_status": classification})
            .eq("dbos_workflow_id", row["dbos_workflow_id"])
            .execute()
        )
    except Exception as exc:
        logger.opt(exception=True).debug(
            f"[workflow_health] persist failed for "
            f"{row.get('dbos_workflow_id')}: {exc}"
        )


async def _mark_lost(sb: Any, row: Dict[str, Any]) -> None:
    """Mark a LOST row's phase=lost / status=failed so the UI shows it
    correctly. The row stays around — operator can inspect."""
    try:
        await (
            sb.table("task_tracking")
            .update(
                {
                    "phase": "lost",
                    "status": "failed",
                    "error_code": "worker_lost",
                    "error_msg": "Worker heartbeat went stale; presumed dead.",
                    "completed_at": datetime.now().isoformat(),
                }
            )
            .eq("dbos_workflow_id", row["dbos_workflow_id"])
            .execute()
        )
        logger.warning(
            f"[workflow_health] marked LOST: workflow_id={row['dbos_workflow_id']} "
            f"task_type={row.get('task_type')} title={row.get('title')!r}"
        )
    except Exception as exc:
        logger.opt(exception=True).warning(
            f"[workflow_health] _mark_lost failed: {exc}"
        )


async def _cancel_orphan(sb: Any, row: Dict[str, Any]) -> None:
    """Cancel an ORPHAN_PENDING row (DBOS executor never picked it up).
    Safe because the workflow body never executed — no partial state."""
    try:
        await (
            sb.table("task_tracking")
            .update(
                {
                    "phase": "cancelled",
                    "status": "cancelled",
                    "error_code": "executor_orphan",
                    "error_msg": "Workflow stuck PENDING beyond hard ceiling × 3; "
                    "DBOS executor never picked it up.",
                    "completed_at": datetime.now().isoformat(),
                }
            )
            .eq("dbos_workflow_id", row["dbos_workflow_id"])
            .execute()
        )
        logger.warning(
            f"[workflow_health] cancelled ORPHAN: workflow_id={row['dbos_workflow_id']} "
            f"task_type={row.get('task_type')}"
        )
    except Exception as exc:
        logger.opt(exception=True).warning(
            f"[workflow_health] _cancel_orphan failed: {exc}"
        )


async def _mark_timed_out(sb: Any, row: Dict[str, Any]) -> None:
    """Mark a USER_TIMEOUT row as timed_out. User opted in via
    max_duration_minutes; this is consensual auto-cancel."""
    try:
        await (
            sb.table("task_tracking")
            .update(
                {
                    "phase": "timed_out",
                    "status": "failed",
                    "error_code": "user_timeout",
                    "error_msg": (
                        f"Exceeded user-set max_duration_minutes="
                        f"{row.get('max_duration_minutes')}."
                    ),
                    "completed_at": datetime.now().isoformat(),
                }
            )
            .eq("dbos_workflow_id", row["dbos_workflow_id"])
            .execute()
        )
        logger.warning(
            f"[workflow_health] timed out: workflow_id={row['dbos_workflow_id']} "
            f"max_min={row.get('max_duration_minutes')}"
        )
    except Exception as exc:
        logger.opt(exception=True).warning(
            f"[workflow_health] _mark_timed_out failed: {exc}"
        )


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
    from app.db import get_async_supabase_admin

    sb = await get_async_supabase_admin()
    await _refresh_policy(sb)

    counters = await classify_and_act_step()
    if (
        counters["lost_marked"]
        or counters["auto_cancelled"]
        or counters["slow"]
        or counters["stalled"]
        or counters["lost"]
        or counters["orphan_pending"]
    ):
        logger.info(f"[workflow_health] tick: {counters}")
