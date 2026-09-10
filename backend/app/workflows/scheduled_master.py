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

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dbos import DBOS
from loguru import logger

_MIN_REFIRE_GAP_MS = 100
_BATCH_SIZE = 100  # don't dispatch more than this per tick

# W2a autopilot hardening (mig 370)
# --------------------------------
# * _AUTO_PAUSE_THRESHOLD — a schedule that fails to dispatch this many times
#   in a row (no success in between) is auto-paused (enabled=false) so a
#   permanently broken routine stops firing every minute. Skipped fires (stale
#   discard / skip_if_active gate) do NOT count toward this run.
# * _DEFAULT_STALE_AFTER_MINUTES — fallback when a row predates the
#   stale_after_minutes column (defensive; the DB default is the same).
_AUTO_PAUSE_THRESHOLD = 5
_DEFAULT_STALE_AFTER_MINUTES = 60


def _due_schedules_stmt(now: datetime):
    """Statement for the due-rows scan (enabled=true + next_fire_at <= now).

    ``select(*UserSchedules.__table__.c)`` — NOT ``select(UserSchedules)``.
    The entity-level form maps each result row to a single 'UserSchedules'
    key (the ORM instance), not one key per column; ``.mappings()`` on it
    then yields ``RowMapping({'UserSchedules': <instance>})`` instead of a
    column-keyed dict, so every ``row.get('task_type')``/``row['id']``
    consumer below would silently return None / raise KeyError (legacy
    ``SELECT *`` returned a column-keyed dict). The column-list form
    reproduces that shape exactly — verified against a real SQLAlchemy
    Result in tests/test_scheduled_master_row_shape_e2e.py (not just the
    compiled SQL text, which looks identical either way).
    """
    from sqlalchemy import select

    from app.models import UserSchedules

    return (
        select(*UserSchedules.__table__.c)
        .where(
            UserSchedules.enabled.is_(True),
            UserSchedules.next_fire_at <= now,
        )
        .order_by(UserSchedules.next_fire_at)
        .limit(_BATCH_SIZE)
    )


@DBOS.step()
async def fire_due_schedules_step() -> Dict[str, Any]:
    """Scan user_schedules for due rows; dispatch each; advance
    next_fire_at via croniter. Returns counters for telemetry plus
    `orders` — routine dispatch orders the WORKFLOW body must start
    (DBOS forbids start_workflow from inside a step — the empty-string
    AssertionError of PR #495; never dispatch workflows in here)."""
    # Direct PG via the SQLAlchemy engine (no httpx) — supabase-py's PostgREST
    # path leaked a CLOSE_WAIT connection per call (Issue #199 Bug C).
    from app.db import engine as db_engine

    # Skip gracefully when Supavisor isn't configured (dev/CI) instead of
    # logging a warning + errors:1 every minute on the engine's RuntimeError.
    if not db_engine.is_configured():
        return {"due": 0, "fired": 0, "skipped": 0, "errors": 0}

    now = datetime.now(timezone.utc)

    from app.db.session import read_scope

    # Pull due rows. See _due_schedules_stmt's docstring for why this must be
    # a column-level select, not select(UserSchedules).
    try:
        async with read_scope() as session:
            rows = (await session.execute(_due_schedules_stmt(now))).mappings().all()
    except Exception as exc:
        logger.opt(exception=True).warning(f"[scheduled_master] fetch failed: {exc}")
        return {"due": 0, "fired": 0, "skipped": 0, "errors": 1}

    if not rows:
        return {"due": 0, "fired": 0, "skipped": 0, "errors": 0}

    fired = 0
    skipped = 0
    errors = 0
    paused = 0
    orders: List[Dict[str, Any]] = []
    for row in rows:
        try:
            result = await _dispatch_one(row)
        except Exception as exc:
            errors += 1
            logger.opt(exception=True).warning(
                f"[scheduled_master] dispatch row {row.get('id')} failed: {exc}"
            )
            # Failure counts toward the consecutive-failure run → maybe
            # auto-pause. Best-effort: a bookkeeping failure must not kill the
            # loop or the tick.
            try:
                if await _record_dispatch_failure(row, str(exc)):
                    paused += 1
            except Exception:
                logger.opt(exception=True).warning(
                    "[scheduled_master] failed to record dispatch failure for "
                    f"row {row.get('id')}"
                )
            continue

        if result.get("outcome") == "skipped":
            # Stale discard or a delivery-policy gate — NOT a failure, so the
            # consecutive-failure run is left untouched (multica: skipped
            # excluded from the failure rate).
            skipped += 1
            continue

        # Successful fire → clear any consecutive-failure run.
        await _reset_consecutive_fails(row)
        order = result.get("order")
        if order is not None:
            orders.append(order)
        fired += 1

    counters: Dict[str, Any] = {
        "due": len(rows),
        "fired": fired,
        "skipped": skipped,
        "errors": errors,
    }
    if paused:
        counters["paused"] = paused
    if orders:
        counters["orders"] = orders
    return counters


async def _record_dispatch_failure(row: Dict[str, Any], err: str) -> bool:
    """Bump fail_count + consecutive_fails + last_error after a dispatch
    failure. When the consecutive-failure run reaches _AUTO_PAUSE_THRESHOLD,
    auto-pause the row (enabled=false, paused_at=now, pause_reason=...) so a
    permanently broken routine stops firing every minute. Returns True when
    the row was auto-paused."""
    from sqlalchemy import update

    from app.db.session import write_scope
    from app.models import UserSchedules

    new_consec = (row.get("consecutive_fails") or 0) + 1
    if new_consec >= _AUTO_PAUSE_THRESHOLD:
        reason = f"auto-paused after {new_consec} consecutive failures: {err[:200]}"
        async with write_scope() as session:
            await session.execute(
                update(UserSchedules)
                .where(UserSchedules.id == row["id"])
                .values(
                    fail_count=UserSchedules.fail_count + 1,
                    consecutive_fails=new_consec,
                    last_error=err[:500],
                    enabled=False,
                    paused_at=datetime.now(timezone.utc),
                    pause_reason=reason[:500],
                )
            )
        logger.warning(
            f"[scheduled_master] schedule {row.get('id')} auto-paused after "
            f"{new_consec} consecutive failures"
        )
        return True

    async with write_scope() as session:
        await session.execute(
            update(UserSchedules)
            .where(UserSchedules.id == row["id"])
            .values(
                fail_count=UserSchedules.fail_count + 1,
                consecutive_fails=new_consec,
                last_error=err[:500],
            )
        )
    return False


async def _reset_consecutive_fails(row: Dict[str, Any]) -> None:
    """Clear the consecutive-failure run after a successful fire. Skipped when
    already zero (the common case) to avoid a needless write every tick."""
    if (row.get("consecutive_fails") or 0) == 0:
        return
    from sqlalchemy import update

    from app.db.session import write_scope
    from app.models import UserSchedules

    try:
        async with write_scope() as session:
            await session.execute(
                update(UserSchedules)
                .where(UserSchedules.id == row["id"])
                .values(consecutive_fails=0)
            )
    except Exception as exc:
        logger.warning(
            f"[scheduled_master] reset consecutive_fails failed (non-fatal): {exc}"
        )


def _is_stale(
    prev_fire_at: Optional[datetime], now: datetime, stale_after_minutes: int
) -> bool:
    """A due fire is stale when it's more than stale_after_minutes past its
    scheduled next_fire_at — worker was down, or we slept through a wall-clock
    run. stale_after_minutes<=0 disables the check."""
    if prev_fire_at is None or not stale_after_minutes or stale_after_minutes <= 0:
        return False
    # asyncpg hands back tz-aware datetimes; guard a naive value just in case.
    if prev_fire_at.tzinfo is None:
        prev_fire_at = prev_fire_at.replace(tzinfo=timezone.utc)
    return (now - prev_fire_at) > timedelta(minutes=stale_after_minutes)


async def _dispatch_one(row: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch a single due row + advance its next_fire_at.

    Returns an outcome dict:
      * {"outcome": "fired", "order": <order>|None} — a fire happened. For an
        agent routine `order` carries the dispatch the WORKFLOW body must start
        (workflows can't be started from inside a step); other task types
        dispatch inline and carry no order.
      * {"outcome": "skipped"} — no fire: the due fire was discarded as stale,
        gated by delivery policy, or its task_type is unknown. Skips never
        touch the consecutive-failure run.
    Dispatch failures raise (the caller records them + may auto-pause)."""
    task_type = row.get("task_type") or ""
    payload = row.get("payload") or {}
    user_id = row.get("user_id")
    sched_id = row["id"]
    tz_name = row.get("timezone") or "UTC"
    cron_expr = row.get("cron_expr") or "* * * * *"

    from sqlalchemy import update

    from app.db.session import write_scope
    from app.models import UserSchedules

    now = datetime.now(timezone.utc)
    prev_fire_at = row.get("next_fire_at")
    # Compute next fire time before dispatch so a slow dispatch doesn't
    # delay the next tick. Timezone-aware so "0 9 * * *" means 9am local.
    next_at = _compute_next_fire(cron_expr, tz_name)

    # Stale-fire discard (multica stale-plan): a due fire far past its
    # scheduled time is dropped, not dispatched — this stops a worker restart
    # from backfilling a flood of missed fires AND stops a 9am daily run from
    # firing at 8pm after downtime. Advance next_fire_at + count it skipped.
    stale_after = row.get("stale_after_minutes")
    if stale_after is None:
        stale_after = _DEFAULT_STALE_AFTER_MINUTES
    if _is_stale(prev_fire_at, now, stale_after):
        async with write_scope() as session:
            await session.execute(
                update(UserSchedules)
                .where(UserSchedules.id == sched_id)
                .values(
                    next_fire_at=next_at,
                    skipped_count=UserSchedules.skipped_count + 1,
                )
            )
        logger.info(
            f"[scheduled_master] schedule {sched_id} fire discarded as stale "
            f"(due {prev_fire_at}, now {now}, >{stale_after}m) — "
            f"next {next_at.isoformat()}"
        )
        return {"outcome": "skipped"}

    # Resolve a generic task_type's workflow up front so an unknown /
    # misconfigured type is a quiet skip (advance so it doesn't hot-loop)
    # rather than a hard failure that would drive the row toward auto-pause.
    workflow_callable = None
    if task_type != "agent_routine":
        workflow_callable = await _resolve_workflow_callable(task_type)
        if workflow_callable is None:
            async with write_scope() as session:
                await session.execute(
                    update(UserSchedules)
                    .where(UserSchedules.id == sched_id)
                    .values(
                        next_fire_at=next_at,
                        skipped_count=UserSchedules.skipped_count + 1,
                    )
                )
            logger.warning(
                f"[scheduled_master] unknown task_type={task_type!r} for "
                f"schedule {sched_id} — skipping"
            )
            return {"outcome": "skipped"}

    # Advance the row first (next_fire_at + fire_count) so concurrent master
    # ticks don't double-fire the same schedule.
    # NOTE: this is best-effort serialization — for true cluster-wide
    # exactly-once we'd need an advisory lock or DBOS workflow_id dedup keyed
    # on (id, next_fire_at). Acceptable here because task_type dispatchers are
    # idempotent (agent_routine via the unique index; generic via the pinned
    # workflow_id below) so a double-fire dedups.
    async with write_scope() as session:
        await session.execute(
            update(UserSchedules)
            .where(UserSchedules.id == sched_id)
            .values(
                last_fired_at=now,
                next_fire_at=next_at,
                fire_count=(row.get("fire_count") or 0) + 1,
                last_error=None,
            )
        )

    # paperclip R1: agent routines don't dispatch a media workflow — a fire
    # creates an issue assigned to the agent (origin_kind='routine') and
    # returns a dispatch order; the WORKFLOW body starts execute_issue
    # (start_workflow inside a step raises an empty AssertionError, #495).
    # Handled before the generic task_type → workflow registry below.
    if task_type == "agent_routine":
        order = await _fire_agent_routine(row)
        if order is None:
            # skip_if_active delivery gate or the DB unique gate — a quiet
            # skip, not a fire, so it must not reset consecutive_fails.
            return {"outcome": "skipped"}
        return {"outcome": "fired", "order": order}

    # Dispatch via the task_type → workflow registry through
    # start_workflow_routed (the routing table decides which callable fires).
    from app.services.infra.dbos_orchestrator import start_workflow_routed

    kwargs = dict(payload)
    if user_id:
        kwargs.setdefault("user_id", str(user_id))

    # Fire idempotency: pin a deterministic DBOS workflow id keyed on this
    # fire's scheduled time so two concurrent master ticks that both read the
    # row before either advances dedup to one workflow. Keyed on the fire's
    # OWN scheduled time (prev_fire_at), not next_at, so successive fires stay
    # distinct. (The agent_routine path is instead idempotent via the
    # issues_open_routine_execution_uq index.)
    fire_key = (
        prev_fire_at.isoformat() if prev_fire_at is not None else next_at.isoformat()
    )
    pinned_wf_id = f"sched:{sched_id}:{fire_key}"

    await start_workflow_routed(
        task_type,
        dbos_workflow_callable=workflow_callable,
        dbos_workflow_kwargs=kwargs,
        workflow_id=pinned_wf_id,
    )
    logger.info(
        f"[scheduled_master] fired schedule={sched_id} task_type={task_type} "
        f"user={user_id} wf={pinned_wf_id} next_at={next_at.isoformat()}"
    )
    return {"outcome": "fired"}


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

    # W3c budget breaker: a routine issue carries no team_id, so gate on the
    # schedule owner's personal team. Over budget → skip this fire (bump
    # skipped_count; leave consecutive_fails untouched — a budget pause is not a
    # dispatch failure) instead of creating + dispatching more paid agent work.
    # An unresolvable team yields is_team_over_budget(None) == False, so the
    # gate is a no-op for users without a personal team / budget row.
    from sqlalchemy import select, text, update

    from app.db.session import read_scope, write_scope
    from app.models import Teams, UserSchedules
    from app.services.ai_usage import is_team_over_budget

    async with read_scope() as session:
        budget_team_id = (
            await session.execute(
                select(Teams.id)
                .where(Teams.owner_id == str(user_id), Teams.kind == "personal")
                .limit(1)
            )
        ).scalar()
    if await is_team_over_budget(budget_team_id):
        async with write_scope() as session:
            await session.execute(
                update(UserSchedules)
                .where(UserSchedules.id == sched_id)
                .values(skipped_count=UserSchedules.skipped_count + 1)
            )
        logger.info(
            f"[scheduled_master] routine {sched_id} budget-paused — team "
            f"{budget_team_id} over monthly AI budget; fire skipped"
        )
        return None

    from app.models import AiAgents, Issues

    # Delivery gate: previous fire's issue still open → skip quietly.
    last_issue_id = payload.get("last_issue_id")
    if policy == "skip_if_active" and last_issue_id:
        async with read_scope() as session:
            prev = (
                (
                    await session.execute(
                        select(Issues.status).where(Issues.id == int(last_issue_id))
                    )
                )
                .mappings()
                .first()
            )
        if prev and prev.get("status") not in _ROUTINE_TERMINAL_ISSUE_STATUSES:
            logger.info(
                f"[scheduled_master] routine {sched_id} skipped — previous "
                f"issue {last_issue_id} still {prev.get('status')}"
            )
            return

    async with read_scope() as session:
        agent = (
            (
                await session.execute(
                    select(AiAgents.id, AiAgents.name).where(
                        AiAgents.slug == agent_slug
                    )
                )
            )
            .mappings()
            .first()
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
    issue_identifier = issue_row.get("identifier") or str(issue_id)

    # W3d narrow inbox (producer 3/3): the schedule owner gets one
    # 'autopilot_output' notification carrying the issue identifier, deep-linked
    # to the issue. notify() is best-effort — it never raises (see
    # app.services.notifications), so a notification hiccup can't kill a fire.
    from app.services.notifications import notify as _inbox_notify

    await _inbox_notify(
        user_id=str(user_id),
        kind="autopilot_output",
        title=f"Autopilot: {issue_identifier}",
        body=body["title"],
        severity="info",
        link_kind="issue",
        link_id=issue_identifier,
    )

    # Pin the workflow id now and persist it; the WORKFLOW body performs
    # the actual dispatch (same path as POST /issues/{id}/dispatch).
    # dbos_workflow_id is guarded by the mig-170 column-allowlist trigger
    # (service_role only) — and the repository writes via the app-role engine
    # too, so neither a plain ORM update nor repo.update passes. SET LOCAL
    # ROLE service_role inside write_scope() is the established pattern (see
    # issue_lifecycle.py execution-field writes).
    workflow_id = f"issue-{issue_id}-{_uuid.uuid4().hex[:12]}"
    async with write_scope() as session:
        await session.execute(text("SET LOCAL ROLE service_role"))
        await session.execute(
            update(Issues)
            .where(Issues.id == issue_id)
            .values(dbos_workflow_id=workflow_id)
        )

    # Task Center visibility: create a task_tracking row pinned to the
    # workflow id — the mirror trigger syncs phase/status as execute_issue
    # runs, so the user gets queued→running→completed in the Task Center
    # without polling issues. Best-effort: tracking must never kill a fire.
    try:
        from app.services.infra.unified_task_manager import get_task_manager

        await get_task_manager().create(
            user_id=str(user_id),
            task_type="agent_routine",
            title=f"Routine: {row.get('name') or 'Routine'}",
            subtitle=f"{agent.get('name') or agent_slug} → Issue #{issue_id}",
            dbos_workflow_id=workflow_id,
            metadata={
                "issue_id": issue_id,
                "schedule_id": str(sched_id),
                "agent_slug": agent_slug,
            },
        )
    except Exception as exc:
        logger.warning(
            f"[scheduled_master] task_tracking create failed (non-fatal): {exc}"
        )

    # Stash last_issue_id for the next fire's delivery gate (merge, never
    # replace — the payload also carries the routine's config).
    merged = {**payload, "last_issue_id": issue_id}
    async with write_scope() as session:
        await session.execute(
            update(UserSchedules)
            .where(UserSchedules.id == sched_id)
            .values(payload=merged)
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


def _resolve_zone(tz_name: str):
    """IANA tz name → tzinfo. Invalid / unknown names fall back to UTC with a
    warning so a bad timezone can never crash the scheduler."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name)
    except Exception as exc:
        logger.warning(
            f"[scheduled_master] invalid timezone {tz_name!r}: {exc} — using UTC"
        )
        return timezone.utc


def _compute_next_fire(cron_expr: str, tz_name: str = "UTC") -> datetime:
    """Return the next fire time (as UTC) after now() for the given 5-field
    cron, interpreted in ``tz_name``.

    croniter is anchored to a base localized to the schedule's timezone, so
    "0 9 * * *" means 9am *local* and stays correct across DST transitions
    (the UTC offset a 9am fire maps to shifts by an hour, but the wall-clock
    hour doesn't). The result is converted back to UTC for storage.

    Invalid timezone → UTC + warning. Invalid cron → now+1h (operator
    misconfig; loud but non-fatal)."""
    try:
        from croniter import croniter
    except Exception as exc:
        logger.opt(exception=True).warning(
            f"[scheduled_master] croniter unavailable: {exc} — falling back to +1h"
        )
        return datetime.now(timezone.utc) + timedelta(hours=1)

    tz = _resolve_zone(tz_name)
    try:
        base = datetime.now(tz)
        itr = croniter(cron_expr, base)
        # croniter with a tz-aware base yields tz-aware datetimes in the same
        # zone; normalize to UTC for storage.
        next_at = itr.get_next(datetime).astimezone(timezone.utc)
        # MIN_REFIRE_GAP_MS guard — even if cron fires immediately, push at
        # least 100ms so we don't tight-loop on a misconfigured "* * * * *".
        now_utc = datetime.now(timezone.utc)
        if (next_at - now_utc).total_seconds() * 1000 < _MIN_REFIRE_GAP_MS:
            next_at = now_utc + timedelta(milliseconds=_MIN_REFIRE_GAP_MS)
        return next_at
    except Exception as exc:
        logger.opt(exception=True).warning(
            f"[scheduled_master] invalid cron {cron_expr!r}: {exc} — "
            "falling back to +1h"
        )
        return datetime.now(timezone.utc) + timedelta(hours=1)


@DBOS.step()
async def record_routine_dispatch_error_step(sched_id: str, err: str) -> None:
    """Persist a routine dispatch failure onto its schedule row so the
    Routines UI surfaces it (the in-step error path can't see workflow-
    level dispatch failures)."""
    from sqlalchemy import update

    from app.db.session import write_scope
    from app.models import UserSchedules

    async with write_scope() as session:
        await session.execute(
            update(UserSchedules)
            .where(UserSchedules.id == sched_id)
            .values(
                fail_count=UserSchedules.fail_count + 1,
                last_error=err[:500],
            )
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

            await _dispatch_execute_issue(issue_id, workflow_id)
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
    if counters.get("fired") or counters.get("errors") or counters.get("paused"):
        logger.info(f"[scheduled_master] tick: {counters}")


__all__ = [
    "fire_due_schedules_step",
    "scheduled_master_workflow",
]
