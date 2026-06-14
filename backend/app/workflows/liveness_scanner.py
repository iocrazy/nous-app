"""Liveness scanner for agent_runs (paperclip-inspired, A8.5).

Runs every 30s via @DBOS.scheduled. For each agent_runs row with
status='running', re-evaluates the liveness_state field based on three
signals:

  1. heartbeat_at age           — process alive at all?
  2. last_useful_action_at age  — making real progress?
  3. output_silence_bytes       — output growing or frozen?

State machine (transitions are one-way except cancelled / dead):

  running --[useful action old > T1]--> silent
  silent  --[stayed silent > T2]------> stuck
  stuck   --[stayed stuck  > T3 OR
             attempted N continuations]-> dead

When 'dead' is reached, the scanner ALSO marks status='failed' with
error_code='liveness_dead'. Migration 206's bridge trigger then emits
an issue_messages row into any associated chat thread.

Thresholds chosen for typical LLM agents — generous enough that a
healthy-but-slow tool call doesn't get clipped. Tunable via env vars
LIVENESS_T1/T2/T3_SECONDS / LIVENESS_MAX_CONTINUATIONS.

Concurrency: a Postgres advisory lock prevents two backend instances
from scanning concurrently. If the lock is held, the scheduled tick
skips silently (the holder's pass already updated state).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

# Re-export the startup reconcile helper from its canonical home so any
# existing caller of `from app.workflows.liveness_scanner import
# reconcile_stranded_runs` keeps working. New callers should import
# directly from app.services.liveness.reconcile to avoid pulling this
# module's @DBOS.scheduled decorator onto their process (gateway
# leak fix, 2026-05-27).
from app.services.liveness.reconcile import (  # noqa: F401
    HEARTBEAT_DEAD_SECONDS,
    reconcile_stranded_runs,
)

# Threshold defaults (paperclip-aligned). Override with env vars.
# HEARTBEAT_DEAD_SECONDS is owned by app.services.liveness.reconcile
# (re-exported above) so the reconcile helper and the scheduled
# handler stay in lockstep.
T1_SECONDS = int(os.environ.get("LIVENESS_T1_SECONDS", "60"))  # running → silent
T2_SECONDS = int(os.environ.get("LIVENESS_T2_SECONDS", "180"))  # silent  → stuck
T3_SECONDS = int(os.environ.get("LIVENESS_T3_SECONDS", "300"))  # stuck   → dead
MAX_CONTINUATIONS = int(os.environ.get("LIVENESS_MAX_CONTINUATIONS", "2"))

# Postgres advisory lock id — picked once, never collide with another
# scheduled job in the same DB. Number is arbitrary but stable.
SCANNER_LOCK_ID = 0xA8050001


@DBOS.step()
async def liveness_scan_step() -> dict[str, Any]:
    """One pass over running agent_runs. Returns counts per transition
    so the operator dashboard / logs can see scanner activity."""
    # Counts via the asyncpg pool (direct PG, no httpx) — supabase-py's
    # PostgREST/Kong path leaked a CLOSE_WAIT connection per call (known
    # httpcore bug; Issue #199 Bug C / 2026-05-22 incident). At-most-once
    # write per row is enforced by the WHERE liveness_state = <expected>
    # CAS guard below, so no advisory lock is needed for correctness.
    from app.db import engine as db_engine

    now = datetime.now(timezone.utc)
    counts: dict[str, int] = {
        "scanned": 0,
        "running_to_silent": 0,
        "silent_to_stuck": 0,
        "stuck_to_dead": 0,
        "heartbeat_dead": 0,
        "noop": 0,
    }

    # Skip gracefully when Supavisor isn't configured (dev/CI) instead of
    # crash-looping every 30s on the engine's RuntimeError.
    if not db_engine.is_configured():
        return counts

    # Pull the candidate set in one trip (cap at 200 — in practice
    # mediahub doesn't have hundreds of running agent_runs at once).
    rows = await db_engine.fetch_all(
        "SELECT id, status, liveness_state, heartbeat_at, "
        "last_useful_action_at, liveness_changed_at, continuation_attempt, "
        "output_silence_bytes FROM public.agent_runs "
        "WHERE status = 'running' LIMIT 200"
    )
    counts["scanned"] = len(rows)

    for row in rows:
        run_id = row["id"]
        cur_state = row["liveness_state"]
        hb = _parse_ts(row.get("heartbeat_at"))
        last_useful = _parse_ts(row.get("last_useful_action_at"))
        state_since = _parse_ts(row.get("liveness_changed_at")) or hb or now
        attempts = int(row.get("continuation_attempt") or 0)

        hb_age = (now - hb).total_seconds() if hb else 999_999
        useful_age = (now - last_useful).total_seconds() if last_useful else 999_999
        state_age = (now - state_since).total_seconds()

        # Heartbeat-dead overrides everything (process gone).
        if hb_age > HEARTBEAT_DEAD_SECONDS and cur_state != "dead":
            await _mark_dead(run_id, cur_state, reason="heartbeat_lost")
            counts["heartbeat_dead"] += 1
            continue

        if cur_state == "running":
            if useful_age > T1_SECONDS:
                await _transition(run_id, "running", "silent")
                counts["running_to_silent"] += 1
            else:
                counts["noop"] += 1
        elif cur_state == "silent":
            if state_age > T2_SECONDS:
                await _transition(run_id, "silent", "stuck")
                counts["silent_to_stuck"] += 1
            elif useful_age <= T1_SECONDS:
                # Recovered — agent reported new useful action recently
                await _transition(run_id, "silent", "running")
            else:
                counts["noop"] += 1
        elif cur_state == "stuck":
            if state_age > T3_SECONDS or attempts >= MAX_CONTINUATIONS:
                await _mark_dead(run_id, "stuck", reason="liveness_dead")
                counts["stuck_to_dead"] += 1
            elif useful_age <= T1_SECONDS:
                # Continuation worked — recovered. Count it: a run that keeps
                # flapping stuck→running burns through MAX_CONTINUATIONS and is
                # then killed by the cap above (closes the "flap forever, never
                # judged dead because each recovery resets state_age" hole).
                await _recover_from_stuck(run_id)
            else:
                counts["noop"] += 1
        else:  # dead / cancelled — scanner doesn't touch
            counts["noop"] += 1

    if counts["scanned"] > 0:
        logger.info(f"[liveness-scanner] {counts}")
    return counts


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        s = value.replace("Z", "+00:00") if isinstance(value, str) else value
        return datetime.fromisoformat(s)
    except (ValueError, AttributeError):
        return None


async def _transition(run_id: Any, expected: str, target: str) -> None:
    """CAS update — only writes if liveness_state is still `expected`.
    Idempotent under concurrent scanners."""
    from app.db import engine as db_engine

    try:
        await db_engine.execute(
            "UPDATE public.agent_runs SET liveness_state = :target "
            "WHERE id = :id AND liveness_state = :expected",
            {"target": target, "id": run_id, "expected": expected},
        )
    except Exception as exc:
        logger.warning(
            f"[liveness-scanner] transition {run_id} {expected}->{target} failed: {exc}"
        )


async def _recover_from_stuck(run_id: Any) -> None:
    """CAS stuck→running AND increment continuation_attempt in one write — each
    recovery from stuck counts toward MAX_CONTINUATIONS so a chronically-flapping
    run is eventually judged dead. Idempotent under concurrent scanners (CAS on
    liveness_state='stuck')."""
    from app.db import engine as db_engine

    try:
        await db_engine.execute(
            "UPDATE public.agent_runs "
            "SET liveness_state = 'running', "
            "continuation_attempt = continuation_attempt + 1 "
            "WHERE id = :id AND liveness_state = 'stuck'",
            {"id": run_id},
        )
    except Exception as exc:
        logger.warning(f"[liveness-scanner] recover-from-stuck {run_id} failed: {exc}")


async def _mark_dead(run_id: Any, expected_state: str, *, reason: str) -> None:
    """Mark a run dead + flip status='failed' with the liveness reason.
    The bridge trigger from migration 206 picks this up and emits a
    chat row into any associated issue thread."""
    from app.db import engine as db_engine

    try:
        await db_engine.execute(
            "UPDATE public.agent_runs SET liveness_state = 'dead', "
            "status = 'failed', ended_at = :ended, error_code = :reason, "
            "error_message = :msg "
            "WHERE id = :id AND liveness_state = :expected AND status = 'running'",
            {
                "ended": datetime.now(timezone.utc),
                "reason": reason,
                "msg": f"Marked dead by liveness scanner: {reason}",
                "id": run_id,
                "expected": expected_state,
            },
        )
    except Exception as exc:
        logger.warning(f"[liveness-scanner] mark dead {run_id} failed: {exc}")


@DBOS.scheduled("*/30 * * * * *")  # every 30s (6-field cron)
@DBOS.workflow()
async def liveness_scan_scheduled(
    scheduled_at: datetime, actual_at: datetime
) -> dict[str, Any]:
    """Scheduled entry — re-evaluates liveness for all running agent_runs."""
    return await liveness_scan_step()


# ─────────────────────────────────────────────────────────────────
# Startup reconciliation: paperclip-style "fix stranded runs."
# The implementation moved to app.services.liveness.reconcile so that
# the gateway process (and any other importer that just needs the
# reconcile helper) can pull it WITHOUT triggering this file's
# @DBOS.scheduled decorator above. The helper is still re-exported
# at the top of this module for back-compat. See the 2026-05-27
# gateway-leak fix for the why.
# ─────────────────────────────────────────────────────────────────


__all__ = [
    "liveness_scan_step",
    "liveness_scan_scheduled",
    "reconcile_stranded_runs",
    "T1_SECONDS",
    "T2_SECONDS",
    "T3_SECONDS",
    "HEARTBEAT_DEAD_SECONDS",
    "MAX_CONTINUATIONS",
]
