"""Spec-2 slice 3 — periodic monitor that recovers STRANDED issues.

An issue is *stranded* when it is ``in_progress``, has an assigned agent, but no
live ``agent_run`` — its ``execute_issue`` workflow died (worker crash/restart)
without reaching a terminal status. Today nothing re-drives these; they sit
forever. This monitor re-dispatches them, bounded by three guards so it can
never thrash or double-run:

  1. DBOS ownership — if DBOS still has a live/queued claim on the issue's
     workflow it will recover it itself, so we SKIP (reuse
     ``workflow_health_sweeper._dbos_still_owns``; fail-closed to owned).
  2. Per-issue redispatch cap — ``execution_state.stranded_redispatch_count``
     (JSONB, no migration); past the cap → give up to ``needs_followup``.
  3. Cooldown + wall-clock ceiling — don't re-dispatch within COOLDOWN of the
     last attempt; past WALL_CLOCK since the issue started, give up to a human.

This is the *involuntary* continuation axis (execution died) — distinct from
slice 1's *voluntary* in-workflow continuation (agent declared FinishIssue=
continue). They do not overlap.
"""

from __future__ import annotations

import os
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

# Bounds (env-overridable). Distinct names from liveness's MAX_CONTINUATIONS —
# this is the issue-level involuntary-recovery axis, not process liveness.
try:
    STRANDED_MAX_REDISPATCH = max(0, int(os.getenv("STRANDED_MAX_REDISPATCH", "2")))
except ValueError:
    STRANDED_MAX_REDISPATCH = 2
try:
    STRANDED_COOLDOWN_S = max(0, int(os.getenv("STRANDED_COOLDOWN_SECONDS", "300")))
except ValueError:
    STRANDED_COOLDOWN_S = 300
try:
    STRANDED_WALL_CLOCK_S = max(
        0, int(os.getenv("STRANDED_WALL_CLOCK_SECONDS", "1800"))
    )
except ValueError:
    STRANDED_WALL_CLOCK_S = 1800


def decide_stranded_action(
    *,
    now: datetime,
    started_at: Optional[datetime],
    redispatch_count: int,
    last_dispatched_at: Optional[datetime],
    dbos_owned: bool,
    max_redispatch: int = STRANDED_MAX_REDISPATCH,
    cooldown_s: int = STRANDED_COOLDOWN_S,
    wall_clock_s: int = STRANDED_WALL_CLOCK_S,
) -> tuple[str, Optional[str]]:
    """Pure decision: ``("redispatch"|"giveup"|"skip", reason)``.

    Guard order matters: DBOS ownership is absolute (never touch a workflow DBOS
    will recover); then wall-clock (hand a too-old issue to a human); then the
    redispatch cap; then cooldown. Anything left → redispatch.
    """
    if dbos_owned:
        return ("skip", "dbos_owns")
    if started_at is not None and (now - started_at).total_seconds() > wall_clock_s:
        return ("giveup", "wall_clock_exceeded")
    if redispatch_count >= max_redispatch:
        return ("giveup", "max_redispatch")
    if (
        last_dispatched_at is not None
        and (now - last_dispatched_at).total_seconds() < cooldown_s
    ):
        return ("skip", "cooldown")
    return ("redispatch", None)


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(val))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@DBOS.step()
async def _scan_stranded() -> list[dict[str, Any]]:
    """Find stranded issues + gather each one's recovery state (reads only).

    Stranded = in_progress + assigned agent + NO live agent_run
    (running/silent/stuck). Per candidate we also resolve DBOS ownership here so
    the workflow body just decides + dispatches."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import AgentRuns, Issues
    from app.workflows.workflow_health_sweeper import _dbos_still_owns

    live_run_exists = (
        select(AgentRuns.id)
        .where(
            AgentRuns.issue_id == Issues.id,
            AgentRuns.status == "running",
            AgentRuns.liveness_state.in_(["running", "silent", "stuck"]),
        )
        .exists()
    )
    async with read_scope() as session:
        rows = (
            (
                await session.execute(
                    select(
                        Issues.id,
                        Issues.started_at,
                        Issues.execution_state,
                        Issues.dbos_workflow_id,
                    ).where(
                        Issues.status == "in_progress",
                        Issues.assignee_agent_id.isnot(None),
                        ~live_run_exists,
                    )
                )
            )
            .mappings()
            .all()
        )
    out: list[dict[str, Any]] = []
    for r in rows or []:
        state = r.get("execution_state") or {}
        if not isinstance(state, dict):
            state = {}
        owned = await _dbos_still_owns(r.get("dbos_workflow_id"))
        out.append(
            {
                "id": int(r["id"]),
                "started_at": r.get("started_at"),
                "redispatch_count": int(state.get("stranded_redispatch_count") or 0),
                "last_dispatched_at": state.get("stranded_last_dispatched_at"),
                "dbos_owned": bool(owned),
            }
        )
    return out


@DBOS.step()
async def _prepare_redispatch(issue_id: int, new_count: int) -> str:
    """Clear the stale lock + bump recovery state, and return a FRESH
    workflow_id (generated here, in a step, so DBOS replay is deterministic).
    Does NOT dispatch — the workflow body does that (never dispatch in a step)."""
    from sqlalchemy import Integer, Text, cast, func, literal, text, update
    from sqlalchemy.dialects.postgresql import JSONB

    from app.db.session import write_scope
    from app.models import Issues

    wf_id = f"issue-{issue_id}-{_uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    empty_jsonb = cast(literal("{}"), JSONB)
    merge_obj = func.jsonb_build_object(
        "stranded_redispatch_count",
        func.to_jsonb(cast(new_count, Integer)),
        "stranded_last_dispatched_at",
        func.to_jsonb(cast(now_iso, Text)),
    )
    # Merge into execution_state jsonb without clobbering other keys.
    async with write_scope() as session:
        await session.execute(text("SET LOCAL ROLE service_role"))
        await session.execute(
            update(Issues)
            .where(Issues.id == issue_id)
            .values(
                execution_locked_at=None,
                dbos_workflow_id=wf_id,
                execution_state=func.coalesce(Issues.execution_state, empty_jsonb).op(
                    "||", return_type=JSONB
                )(merge_obj),
            )
        )
    return wf_id


@DBOS.step()
async def _giveup_stranded(issue_id: int, reason: str) -> None:
    """Past the cap / wall-clock: hand the issue to a human (needs_followup)
    and release the lock so it isn't stuck locked."""
    from app.workflows.issue_lifecycle import clear_lock, set_status

    await set_status(
        issue_id,
        "needs_followup",
        agent_outcome="stranded",
        outcome_reason=f"auto-recovery gave up: {reason}",
    )
    await clear_lock(issue_id)


@DBOS.scheduled("*/3 * * * *")  # every 3 minutes
@DBOS.workflow()
async def stranded_issue_monitor_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> dict[str, int]:
    """Re-drive stranded issues (bounded). Dispatch happens in the workflow body
    — never inside a step (DBOS forbids start_workflow there)."""
    from app.api.issues_router import _dispatch_execute_issue

    candidates = await _scan_stranded()
    now = datetime.now(timezone.utc)
    counts = {"scanned": len(candidates), "redispatched": 0, "gaveup": 0, "skipped": 0}

    for c in candidates:
        action, reason = decide_stranded_action(
            now=now,
            started_at=_parse_ts(c["started_at"]),
            redispatch_count=c["redispatch_count"],
            last_dispatched_at=_parse_ts(c["last_dispatched_at"]),
            dbos_owned=c["dbos_owned"],
        )
        if action == "redispatch":
            wf_id = await _prepare_redispatch(c["id"], c["redispatch_count"] + 1)
            await _dispatch_execute_issue(c["id"], wf_id)
            counts["redispatched"] += 1
            logger.info(f"[stranded] re-dispatched issue {c['id']} as {wf_id}")
        elif action == "giveup":
            await _giveup_stranded(c["id"], reason or "unknown")
            counts["gaveup"] += 1
            logger.warning(f"[stranded] gave up issue {c['id']}: {reason}")
        else:
            counts["skipped"] += 1

    if counts["redispatched"] or counts["gaveup"]:
        logger.info(f"[stranded] monitor tick: {counts}")
    return counts


__all__ = ["decide_stranded_action", "stranded_issue_monitor_workflow"]
