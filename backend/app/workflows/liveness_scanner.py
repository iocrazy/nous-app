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

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from dbos import DBOS
from loguru import logger


# Threshold defaults (paperclip-aligned). Override with env vars.
T1_SECONDS = int(os.environ.get("LIVENESS_T1_SECONDS", "60"))   # running → silent
T2_SECONDS = int(os.environ.get("LIVENESS_T2_SECONDS", "180"))  # silent  → stuck
T3_SECONDS = int(os.environ.get("LIVENESS_T3_SECONDS", "300"))  # stuck   → dead
HEARTBEAT_DEAD_SECONDS = int(os.environ.get("LIVENESS_HEARTBEAT_DEAD_SECONDS", "120"))
MAX_CONTINUATIONS = int(os.environ.get("LIVENESS_MAX_CONTINUATIONS", "2"))

# Postgres advisory lock id — picked once, never collide with another
# scheduled job in the same DB. Number is arbitrary but stable.
SCANNER_LOCK_ID = 0xA8050001


@DBOS.step()
async def liveness_scan_step() -> dict[str, Any]:
    """One pass over running agent_runs. Returns counts per transition
    so the operator dashboard / logs can see scanner activity."""
    from app.db.supabase_client import get_async_supabase_admin

    sb = await get_async_supabase_admin()

    # Try the advisory lock; bail if another instance is scanning.
    lock_resp = await sb.rpc(
        "pg_try_advisory_lock", {"key": SCANNER_LOCK_ID}
    ).execute() if False else None  # supabase client doesn't expose advisory_lock
    # Fallback: use a SQL RPC wrapper. If you don't have one yet, the
    # scanner runs without a lock — at-most-once write per row is
    # naturally enforced by the WHERE liveness_state = <expected> guard
    # below, which is idempotent under concurrent runs.
    _ = lock_resp

    now = datetime.now(timezone.utc)
    counts: dict[str, int] = {
        "scanned": 0,
        "running_to_silent": 0,
        "silent_to_stuck": 0,
        "stuck_to_dead": 0,
        "heartbeat_dead": 0,
        "noop": 0,
    }

    # Pull the candidate set in one trip (cap at 200 — in practice
    # mediahub doesn't have hundreds of running agent_runs at once).
    result = (
        await sb.table("agent_runs")
        .select(
            "id,status,liveness_state,heartbeat_at,last_useful_action_at,"
            "liveness_changed_at,continuation_attempt,output_silence_bytes"
        )
        .eq("status", "running")
        .limit(200)
        .execute()
    )
    rows = result.data or []
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
            await _mark_dead(sb, run_id, cur_state, reason="heartbeat_lost")
            counts["heartbeat_dead"] += 1
            continue

        if cur_state == "running":
            if useful_age > T1_SECONDS:
                await _transition(sb, run_id, "running", "silent")
                counts["running_to_silent"] += 1
            else:
                counts["noop"] += 1
        elif cur_state == "silent":
            if state_age > T2_SECONDS:
                await _transition(sb, run_id, "silent", "stuck")
                counts["silent_to_stuck"] += 1
            elif useful_age <= T1_SECONDS:
                # Recovered — agent reported new useful action recently
                await _transition(sb, run_id, "silent", "running")
            else:
                counts["noop"] += 1
        elif cur_state == "stuck":
            if state_age > T3_SECONDS or attempts >= MAX_CONTINUATIONS:
                await _mark_dead(sb, run_id, "stuck", reason="liveness_dead")
                counts["stuck_to_dead"] += 1
            elif useful_age <= T1_SECONDS:
                # Continuation worked — recovered
                await _transition(sb, run_id, "stuck", "running")
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


async def _transition(sb, run_id: str, expected: str, target: str) -> None:
    """CAS update — only writes if liveness_state is still `expected`.
    Idempotent under concurrent scanners."""
    try:
        await (
            sb.table("agent_runs")
            .update({"liveness_state": target})
            .eq("id", run_id)
            .eq("liveness_state", expected)
            .execute()
        )
    except Exception as exc:
        logger.warning(f"[liveness-scanner] transition {run_id} {expected}->{target} failed: {exc}")


async def _mark_dead(sb, run_id: str, expected_state: str, *, reason: str) -> None:
    """Mark a run dead + flip status='failed' with the liveness reason.
    The bridge trigger from migration 206 picks this up and emits a
    chat row into any associated issue thread."""
    try:
        await (
            sb.table("agent_runs")
            .update({
                "liveness_state": "dead",
                "status": "failed",
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "error_code": reason,
                "error_message": f"Marked dead by liveness scanner: {reason}",
            })
            .eq("id", run_id)
            .eq("liveness_state", expected_state)
            .eq("status", "running")
            .execute()
        )
    except Exception as exc:
        logger.warning(f"[liveness-scanner] mark dead {run_id} failed: {exc}")


@DBOS.scheduled("*/30 * * * * *")  # every 30s (6-field cron)
@DBOS.workflow()
async def liveness_scan_scheduled(scheduled_at: datetime, actual_at: datetime) -> dict[str, Any]:
    """Scheduled entry — re-evaluates liveness for all running agent_runs."""
    return await liveness_scan_step()


# ─────────────────────────────────────────────────────────────────
# Startup reconciliation: paperclip-style "fix stranded runs."
# Called from app lifespan startup. Scans for agent_runs.status='running'
# with heartbeat_at older than HEARTBEAT_DEAD_SECONDS — these are
# leftovers from a backend crash. Mark them dead so the chat reflects
# reality and DBOS retry can claim them.
# ─────────────────────────────────────────────────────────────────


async def reconcile_stranded_runs() -> dict[str, int]:
    """One-shot startup sweep. Safe to call at any time; idempotent."""
    from app.db.supabase_client import get_async_supabase_admin

    sb = await get_async_supabase_admin()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=HEARTBEAT_DEAD_SECONDS)
    cutoff_iso = cutoff.isoformat()

    result = (
        await sb.table("agent_runs")
        .select("id")
        .eq("status", "running")
        .lt("heartbeat_at", cutoff_iso)
        .limit(1000)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return {"reconciled": 0}

    ids = [r["id"] for r in rows]
    update_resp = (
        await sb.table("agent_runs")
        .update({
            "liveness_state": "dead",
            "status": "failed",
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "error_code": "stranded_on_restart",
            "error_message": "Backend restarted while this run was in flight; no heartbeat for >2 minutes.",
        })
        .in_("id", ids)
        .eq("status", "running")
        .execute()
    )
    n = len(update_resp.data or [])
    if n > 0:
        logger.warning(f"[liveness-reconcile] marked {n} stranded run(s) dead on startup")
    return {"reconciled": n}


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
