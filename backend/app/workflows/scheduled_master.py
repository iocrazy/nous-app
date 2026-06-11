"""scheduled_master — master scheduler that fires user_schedules rows.

Runs every minute via @DBOS.scheduled. Reads `public.user_schedules`
where enabled=true AND next_fire_at <= now(); for each due row,
dispatches the corresponding task_type workflow and recomputes
next_fire_at via croniter.

Why a single master vs N decorators
-----------------------------------
* Hard-coded `@DBOS.scheduled("0 9 * * *")` requires a code change +
  deploy to add a new schedule. Users (and ops) want to add/edit/
  disable schedules at runtime.
* Each per-user "daily summary email" schedule would need its own
  decorator → schedule explosion as user count grows.
* Centralizing dispatch lets us reuse one set of safety guards
  (timer 三防护 borrowed from openclaw / lane queue routing) instead
  of replicating across N decorators.

Timer safety guards (borrowed from openclaw cron/service/timer.ts:780)
----------------------------------------------------------------------
* MIN_REFIRE_GAP_MS=100 — even if next_fire_at races to now-now (rare,
  e.g. user sets cron "* * * * *" with 0 missed minutes), enforce a
  100ms floor so we don't tight-loop on a misconfigured row.
* MAX_TIMER_DELAY_MS=60_000 — DBOS @scheduled cron is already 1-min
  precision, but we re-check the table on every tick (vs. waiting for
  the next cron event) so a freshly-INSERTed schedule with
  next_fire_at <= now() fires within ≤1 min instead of waiting for
  the row's specific cron tick.
* armRunningRecheckTimer — handled implicitly by DBOS @scheduled
  (each tick is a separate workflow invocation; long ones don't
  block subsequent ticks because DBOS uses workflow_id dedup).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dbos import DBOS
from loguru import logger

_MIN_REFIRE_GAP_MS = 100
_BATCH_SIZE = 100  # don't dispatch more than this per tick


@DBOS.step()
async def fire_due_schedules_step() -> Dict[str, Any]:
    """Scan user_schedules for due rows; dispatch each; advance
    next_fire_at via croniter. Returns counters for telemetry plus
    `orders` — routine dispatch orders the WORKFLOW body must start
    (DBOS forbids start_workflow from inside a step — the empty-string
    AssertionError of PR #495; never dispatch workflows in here)."""
    # Direct PG via SQLAlchemy engine (no httpx) — supabase-py's PostgREST
    # path leaked a CLOSE_WAIT connection per call (Issue #199 Bug C).
    from app.db import engine as db_engine

    # Skip gracefully when Supavisor isn't configured (dev/CI) instead of
    # logging a warning + errors:1 every minute on the engine's RuntimeError.
    if not db_engine.is_configured():
        return {"due": 0, "fired": 0, "errors": 0}

    now = datetime.now(timezone.utc)

    # Pull due rows. enabled=true + next_fire_at <= now.
    try:
        rows = await db_engine.fetch_all(
            "SELECT * FROM public.user_schedules "
            "WHERE enabled = true AND next_fire_at <= :now "
            "ORDER BY next_fire_at LIMIT :limit",
            {"now": now, "limit": _BATCH_SIZE},
        )
    except Exception as exc:
        logger.opt(exception=True).warning(f"[scheduled_master] fetch failed: {exc}")
        return {"due": 0, "fired": 0, "errors": 1}

    if not rows:
        return {"due": 0, "fired": 0, "errors": 0}

    fired = 0
    errors = 0
    orders: List[Dict[str, Any]] = []
    for row in rows:
        try:
            order = await _dispatch_one(row)
            if order is not None:
                orders.append(order)
            fired += 1
        except Exception as exc:
            errors += 1
            logger.opt(exception=True).warning(
                f"[scheduled_master] dispatch row {row.get('id')} failed: {exc}"
            )
            try:
                await db_engine.execute(
                    "UPDATE public.user_schedules SET fail_count = :fc, "
                    "last_error = :err WHERE id = :id",
                    {
                        "fc": (row.get("fail_count") or 0) + 1,
                        "err": str(exc)[:500],
                        "id": row["id"],
                    },
                )
            except Exception:
                pass

    return {"due": len(rows), "fired": fired, "errors": errors, "orders": orders}


async def _dispatch_one(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Dispatch a single due row + advance its next_fire_at. Returns a
    dispatch order for the workflow body when the row is an agent
    routine (workflows can't be started from inside a step), else None."""
    task_type = row.get("task_type") or ""
    payload = row.get("payload") or {}
    user_id = row.get("user_id")
    sched_id = row["id"]

    # Compute next fire time before dispatch so a slow dispatch doesn't
    # delay the next tick.
    next_at = _compute_next_fire(row.get("cron_expr") or "* * * * *")

    # Update the row first (advance next_fire_at + bump counters) so
    # concurrent master ticks don't double-fire the same schedule.
    # NOTE: this is best-effort serialization — for true cluster-wide
    # exactly-once we'd need an advisory lock or DBOS workflow_id
    # dedup keyed on (id, last_fired_at). Acceptable here because
    # task_type dispatchers are themselves idempotent (PR #154 ensures
    # DBOS workflow_id dedup) and double-firing means at most one
    # extra task that the dispatcher will short-circuit.
    from app.db import engine as db_engine

    await db_engine.execute(
        "UPDATE public.user_schedules SET last_fired_at = :fired, "
        "next_fire_at = :next, fire_count = :fc, last_error = NULL "
        "WHERE id = :id",
        {
            "fired": datetime.now(timezone.utc),
            "next": next_at,
            "fc": (row.get("fire_count") or 0) + 1,
            "id": sched_id,
        },
    )

    # paperclip R1: agent routines don't dispatch a media workflow — a fire
    # creates an issue assigned to the agent (origin_kind='routine') and
    # returns a dispatch order; the WORKFLOW body starts execute_issue
    # (start_workflow inside a step raises an empty AssertionError, #495).
    # Handled before the generic task_type → workflow registry below.
    if task_type == "agent_routine":
        return await _fire_agent_routine(row)

    # Dispatch via the task_type → workflow registry. For now we route
    # through start_workflow_routed so the existing routing table
    # decides which workflow callable to fire. Unknown task_type just
    # logs + bumps fail_count (next call up the stack).
    from app.services.infra.dbos_orchestrator import start_workflow_routed

    # The mapping task_type → workflow callable lives in
    # app/workflows/__init__.py + dispatch routing. For each task_type
    # the master scheduler supports we'd add a small import + dispatch
    # entry here. Initial supported types: parse / download / ai_summary
    # — extend as user UX surfaces more.
    workflow_callable = await _resolve_workflow_callable(task_type)
    if workflow_callable is None:
        logger.warning(
            f"[scheduled_master] unknown task_type={task_type} for "
            f"schedule {sched_id} — skipping"
        )
        return

    kwargs = dict(payload)
    if user_id:
        kwargs.setdefault("user_id", str(user_id))

    await start_workflow_routed(
        task_type,
        dbos_workflow_callable=workflow_callable,
        dbos_workflow_kwargs=kwargs,
    )
    logger.info(
        f"[scheduled_master] fired schedule={sched_id} task_type={task_type} "
        f"user={user_id} next_at={next_at.isoformat()}"
    )


_ROUTINE_TERMINAL_ISSUE_STATUSES = ("done", "cancelled")


async def _fire_agent_routine(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One agent-routine fire: delivery-policy gate → create issue assigned
    to the agent → stash last_issue_id back onto the schedule payload —
    then RETURN a dispatch order for the workflow body to start
    execute_issue (paperclip R1). Runs inside fire_due_schedules_step, so
    it must never start a workflow itself.

    delivery_policy:
      - skip_if_active (default): if the issue created by the PREVIOUS fire
        is still open (not done/cancelled), skip this fire — prevents a slow
        agent from accumulating a backlog of identical issues (paperclip's
        coalesce_if_active analogue). Backed twice: the payload
        last_issue_id check below, and the DB partial unique index
        `issues_open_routine_execution_uq` (one open issue per routine) —
        a unique violation here is a quiet skip, not an error.
      - always: fire regardless (unique origin_fingerprint per fire so the
        index never blocks it).
    """
    import json as _json
    import uuid as _uuid

    from app.db import engine as db_engine

    payload = row.get("payload") or {}
    if isinstance(payload, str):  # asyncpg may hand jsonb back as str
        payload = _json.loads(payload)
    sched_id = row["id"]
    user_id = row.get("user_id")
    agent_slug = (payload.get("agent_slug") or "").strip()
    prompt_md = (payload.get("prompt_md") or "").strip()
    policy = payload.get("delivery_policy") or "skip_if_active"

    if not agent_slug or not prompt_md or not user_id:
        raise RuntimeError(
            f"agent_routine {sched_id} payload incomplete "
            f"(agent_slug={agent_slug!r}, prompt_md={'set' if prompt_md else 'empty'}, "
            f"user_id={'set' if user_id else 'empty'})"
        )

    # Delivery gate: previous fire's issue still open → skip quietly.
    last_issue_id = payload.get("last_issue_id")
    if policy == "skip_if_active" and last_issue_id:
        prev = await db_engine.fetch_one(
            "SELECT status FROM public.issues WHERE id = :iid",
            {"iid": int(last_issue_id)},
        )
        if prev and prev.get("status") not in _ROUTINE_TERMINAL_ISSUE_STATUSES:
            logger.info(
                f"[scheduled_master] routine {sched_id} skipped — previous "
                f"issue {last_issue_id} still {prev.get('status')}"
            )
            return

    agent = await db_engine.fetch_one(
        "SELECT id, name FROM public.ai_agents WHERE slug = :slug",
        {"slug": agent_slug},
    )
    if not agent:
        raise RuntimeError(f"agent_routine {sched_id}: agent '{agent_slug}' not found")

    from app.repositories.issue_repository import issue_repository

    now_label = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    body: Dict[str, Any] = {
        "title": f"{row.get('name') or 'Routine'} — {now_label}",
        "description": prompt_md,
        "status": "todo",
        "priority": "medium",
        "assignee_agent_id": str(agent["id"]),
        "created_by_user_id": str(user_id),
        "origin_kind": "routine",
        "origin_id": str(sched_id),
    }
    if policy == "always":
        # Dodge issues_open_routine_execution_uq — `always` legitimately
        # allows several open issues for the same routine.
        body["origin_fingerprint"] = _uuid.uuid4().hex

    try:
        issue_row = await issue_repository.atomic_create(body)
    except Exception as exc:
        # DB-enforced delivery gate: an open issue from a previous fire
        # already exists (e.g. the payload stash was lost). Quiet skip.
        if "issues_open_routine_execution_uq" in repr(exc):
            logger.info(
                f"[scheduled_master] routine {sched_id} skipped — open issue "
                "already exists (db unique gate)"
            )
            return None
        raise
    issue_id = int(issue_row["id"])

    # Pin the workflow id now and persist it; the WORKFLOW body performs
    # the actual dispatch (same path as POST /issues/{id}/dispatch).
    # Must go through the repository (service_role) — the mig-172 issues
    # column-allowlist trigger blocks dbos_workflow_id writes from the
    # app-role asyncpg engine.
    workflow_id = f"issue-{issue_id}-{_uuid.uuid4().hex[:12]}"
    await issue_repository.update(issue_id, {"dbos_workflow_id": workflow_id})

    # Stash last_issue_id for the next fire's delivery gate (merge, never
    # replace — the payload also carries the routine's config).
    merged = {**payload, "last_issue_id": issue_id}
    await db_engine.execute(
        "UPDATE public.user_schedules SET payload = CAST(:p AS jsonb) "
        "WHERE id = :id",
        {"p": _json.dumps(merged), "id": sched_id},
    )
    logger.info(
        f"[scheduled_master] routine {sched_id} fired → issue {issue_id} "
        f"(agent={agent_slug}, wf={workflow_id}) — dispatch deferred to workflow"
    )
    return {
        "sched_id": str(sched_id),
        "issue_id": issue_id,
        "workflow_id": workflow_id,
    }


async def _resolve_workflow_callable(task_type: str):
    """Map task_type string to the workflow callable. Kept here as a
    small static dispatch table — extends as we expose new types in the
    user-facing schedule UI. Unknown types return None (caller logs)."""
    if task_type == "parse":
        from app.workflows.parse import parse_workflow

        return parse_workflow
    if task_type == "download":
        from app.workflows.download import download_workflow

        return download_workflow
    if task_type == "ai_summary":
        from app.workflows.ai_summary import ai_summary_workflow

        return ai_summary_workflow
    if task_type == "transcode":
        from app.workflows.transcode import transcode_workflow

        return transcode_workflow
    return None


def _compute_next_fire(cron_expr: str) -> datetime:
    """Return the next fire time after now() for the given 5-field cron.
    Falls back to now+1h if the cron is invalid (operator misconfig).
    """
    try:
        from croniter import croniter

        base = datetime.now(timezone.utc)
        itr = croniter(cron_expr, base)
        next_at = itr.get_next(datetime)
        # MIN_REFIRE_GAP_MS guard — even if cron fires immediately, push
        # at least 100ms so we don't tight-loop.
        if (next_at - base).total_seconds() * 1000 < _MIN_REFIRE_GAP_MS:
            from datetime import timedelta

            next_at = base + timedelta(milliseconds=_MIN_REFIRE_GAP_MS)
        return next_at
    except Exception as exc:
        logger.opt(exception=True).warning(
            f"[scheduled_master] invalid cron {cron_expr!r}: {exc} — "
            "falling back to +1h"
        )
        from datetime import timedelta

        return datetime.now(timezone.utc) + timedelta(hours=1)


@DBOS.step()
async def record_routine_dispatch_error_step(sched_id: str, err: str) -> None:
    """Persist a routine dispatch failure onto its schedule row so the
    Routines UI surfaces it (the in-step error path can't see workflow-
    level dispatch failures)."""
    from app.db import engine as db_engine

    await db_engine.execute(
        "UPDATE public.user_schedules SET fail_count = fail_count + 1, "
        "last_error = :err WHERE id = :id",
        {"err": err[:500], "id": sched_id},
    )


async def _dispatch_routine_orders(
    orders: List[Dict[str, Any]], counters: Dict[str, Any]
) -> None:
    """Start execute_issue for each routine order. Runs in WORKFLOW
    context (child workflow starts are legal here, unlike in steps).
    Duplicate workflow_id is a soft success — DBOS already has the pinned
    workflow from a previous (recovered) run."""
    for order in orders:
        issue_id = int(order["issue_id"])
        workflow_id = str(order["workflow_id"])
        try:
            from app.api.issues_router import _dispatch_execute_issue

            _dispatch_execute_issue(issue_id, workflow_id)
            logger.info(
                f"[scheduled_master] routine issue {issue_id} dispatched "
                f"(wf={workflow_id})"
            )
        except Exception as exc:
            low = repr(exc).lower()
            if "already exists" in low or "duplicate" in low:
                continue
            counters["errors"] = (counters.get("errors") or 0) + 1
            logger.opt(exception=True).warning(
                f"[scheduled_master] routine issue {issue_id} dispatch "
                f"failed: {exc}"
            )
            try:
                await record_routine_dispatch_error_step(
                    str(order["sched_id"]), f"dispatch failed: {exc}"
                )
            except Exception:
                pass


@DBOS.scheduled("* * * * *")  # every minute
@DBOS.workflow()
async def scheduled_master_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """One tick. Counters are logged at INFO when there's actual work
    so a quiet system doesn't spam the log."""
    counters = await fire_due_schedules_step()
    orders = counters.pop("orders", None) or []
    if orders:
        await _dispatch_routine_orders(orders, counters)
    if counters.get("fired") or counters.get("errors"):
        logger.info(f"[scheduled_master] tick: {counters}")


__all__ = [
    "fire_due_schedules_step",
    "scheduled_master_workflow",
]
