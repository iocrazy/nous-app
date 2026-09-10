"""agent_runs_sweeper — DBOS @scheduled port of the legacy
`tasks.agent_runs_sweeper.sweep` Celery beat job.

The legacy task ran every 60s under celery-beat, fenced by a Postgres
advisory lock so multiple beat workers wouldn't double-execute. DBOS
handles that natively: the scheduler generates a deterministic
workflow_id (`sched-<name>-<iso>`) per cron tick, and the system DB
rejects duplicate workflow_ids — so only one DBOS worker actually
runs each tick. The advisory_lock indirection is dropped.

Two jobs (unchanged from the Celery version):
  1. mark zombie `status='running'` rows (heartbeat_at < now-2min)
     as `heartbeat_lost`
  2. recompute monthly token/cost spend per agent → flip
     ai_agents.paused_reason='budget' on overrun, clear it on
     undershoot (without clobbering manual pauses)

Why steps + workflow are ALL ``async def``
------------------------------------------
Originally written as ``def + asyncio.run(_do())`` because supabase-py
worked fine across short-lived event loops — every call recreated its
httpx client. asyncpg's pool is the opposite: it BINDS to the loop
where it was first awaited. Each ``asyncio.run()`` opens a new loop,
runs the coroutine, and closes the loop — but a cached asyncpg
connection still points at the (now-dead) first-loop pool. Second
tick onwards crashes with ``Event loop is closed`` and
``cannot perform operation: another operation is in progress``.

DBOS supports ``async def`` workflows + steps natively. Awaiting from
the same loop the executor owns means the asyncpg pool stays bound to
a long-lived loop — the symptom disappears at the source.

Same fix shape as PR #227 (workflow_health_sweeper) — see that file's
header for the longer cascade explanation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dbos import DBOS
from loguru import logger

HEARTBEAT_STALENESS_SECONDS = 120


def _agents_budget_scan_stmt(agent_ids):
    """Column-level select of the budget fields for a batch of agent ids.

    ``select(*AiAgents.__table__.c)`` — NOT ``select(AiAgents)``. The
    entity-level form maps each result row to a single 'AiAgents' key (the
    ORM instance), not one key per column, so every ``agent.get('paused_
    reason')``/``agent['id']`` consumer below would silently return None /
    raise KeyError instead of the legacy column-keyed dict shape (see
    tests/test_orm_b5_task2_row_shape_e2e.py for the real-engine proof)."""
    from sqlalchemy import select

    from app.models import AiAgents

    return select(*AiAgents.__table__.c).where(AiAgents.id.in_(agent_ids))


def _agent_pause_stmt(agent_id, paused_reason):
    """UPDATE ai_agents.paused_reason for one agent (budget pause/unpause)."""
    from sqlalchemy import update

    from app.models import AiAgents

    return (
        update(AiAgents)
        .where(AiAgents.id == agent_id)
        .values(paused_reason=paused_reason)
    )


@DBOS.step()
async def mark_heartbeat_lost_step() -> int:
    """Flip running rows whose heartbeat is older than 2 minutes, then close
    each flipped run's transcript with ``turn_end{reason:interrupted}`` so
    the event log stays replay-complete (spec §2 primitive ①). A run whose
    transcript already carries a turn_end is left alone."""
    from app.repositories.agent_runs_repository import get_agent_runs_repository
    from app.services.ai.runner.interrupted_turn import close_interrupted_runs

    runs_repo = get_agent_runs_repository()
    stale_before = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(
        seconds=HEARTBEAT_STALENESS_SECONDS
    )
    run_ids = await runs_repo.mark_heartbeat_lost_ids(stale_before=stale_before)
    await close_interrupted_runs(run_ids)
    return len(run_ids)


INBOX_ORPHAN_SECONDS = 24 * 3600
RECONCILE_GRACE_SECONDS = 10 * 60


@DBOS.step()
async def reconcile_issue_execution_state_step() -> int:
    """MH-1: an ``in_progress`` issue whose last run ended (any terminal
    status) ≥10 min ago while ``execution_state.turn`` still says a turn is
    on → merge ``agent_outcome="interrupted"`` (+ reason, reconciled_at) so
    the decoration stops claiming a run that is gone. issue.rollup already
    derives phase from the runs; this keeps the column honest for the
    readers that still look at it. Returns how many issues were stamped."""
    from app.repositories.agent_runs_repository import get_agent_runs_repository
    from app.repositories.issue_repository import issue_repository
    from app.services.issues.execution_state import merge_execution_state

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=RECONCILE_GRACE_SECONDS)
    stamped = 0
    for issue in await issue_repository.list_in_progress_without_live_run():
        session_id = issue.get("ai_session_id")
        runs = await get_agent_runs_repository().list_for_issue(
            issue_id=int(issue["id"]),
            conversation_id=int(session_id) if session_id else None,
            limit=1,
        )
        latest = runs[0] if runs else None
        ended_at = (latest or {}).get("ended_at")
        if latest is None or ended_at is None or ended_at > cutoff:
            continue
        await merge_execution_state(
            int(issue["id"]),
            {
                "agent_outcome": "interrupted",
                "outcome_reason": (
                    f"run {latest['id']} ended ({latest.get('status')}) "
                    "without a status transition"
                ),
                "reconciled_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        stamped += 1
    return stamped


@DBOS.step()
async def expire_orphan_inbox_step() -> int:
    """Mark unclaimed agent_run_inbox items older than a day as expired
    (spec §1-③: an item whose run ended before the next step boundary is an
    orphan; it is never deleted, so the thread still shows it was sent)."""
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )

    older_than = datetime.now(timezone.utc) - timedelta(seconds=INBOX_ORPHAN_SECONDS)
    # Phase 2a: items queued on a PAUSED issue wait for resume — never orphans.
    return await get_agent_run_inbox_repository().expire_stale(
        older_than=older_than, skip_paused_issues=True
    )


#: Issues drained per tick. The sweep runs every minute, so what does not fit
#: is picked up next minute — the cap bounds one tick, not the backlog.
INBOX_DRAIN_LIMIT = 20


@DBOS.step()
async def drain_idle_inbox_step() -> int:
    """Dispatch idle issues that are still holding an unclaimed inbox item.

    Items are claimed at STEP boundaries only (``InboxClaimHook``). Between a
    run's last boundary and its row going terminal there is a window with no
    boundary left, and anything that lands there is stranded: the 2026-09-10
    acceptance watched a ``subagent_result`` and a scheduled ``steer`` sit
    unclaimed for 15 and 8.8 minutes, with the schedule row reporting a clean
    ``fire_count=1``. ``issue_lifecycle`` now drains before it ends, bounded;
    this is the backstop for everything that misses that window — a run that
    crashed, a dispatch that never started, a stream past the bound.

    The busy question is NOT re-implemented here. ``deliver_or_dispatch``
    already owns all three signals (a running root run, ``paused_at``, the
    in-flight ``dispatching`` marker) plus the terminal/hidden check, so this
    calls it with ``already_enqueued=True``: on busy it returns without writing
    a second row, on idle it starts the turn on the continuation nudge.

    Returns how many issues were actually dispatched. One issue's failure is
    logged and skipped — the rest of the tick still runs.
    """
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )
    from app.services.issues import inbox_or_dispatch as deliver_mod

    repo = get_agent_run_inbox_repository()
    try:
        targets = await repo.pending_issue_targets(limit=INBOX_DRAIN_LIMIT)
    except Exception as err:  # noqa: BLE001 — a probe that cannot read has
        # not proved there is nothing to drain; say so and try again next tick.
        logger.error(f"[sweeper] idle-drain could not list pending targets: {err}")
        return 0

    dispatched = 0
    for target in targets:
        issue_id = int(target["target_id"])
        count = int(target.get("count") or 0)
        try:
            items = await repo.list_for_target(
                target_kind="issue", target_id=issue_id, pending_only=True, limit=1
            )
            if not items:
                # claimed between the two reads — nothing stranded after all
                continue
            item = items[0]
            result = await deliver_mod.deliver_or_dispatch(
                issue_id,
                kind=str(item.get("kind") or "steer"),
                content={},
                user_id=str(item["user_id"]),
                already_enqueued=True,
            )
        except Exception as err:  # noqa: BLE001 — one issue must not sink the tick
            logger.exception(f"[sweeper] idle-drain failed for issue {issue_id}: {err}")
            continue
        if result.mode == "dispatched":
            dispatched += 1
            logger.info(
                f"[sweeper] issue {issue_id}: {count} stranded inbox item(s) — "
                f"dispatched {result.workflow_id}"
            )
        else:
            logger.info(
                f"[sweeper] issue {issue_id}: {count} pending inbox item(s) left "
                f"in place ({result.mode}/{result.reason})"
            )
    return dispatched


@DBOS.step()
async def recompute_monthly_budgets_step() -> int:
    """Sum this month's spend per agent, flip paused_reason='budget' on
    overrun. Returns count of agents whose paused_reason transitioned."""
    from uuid import UUID

    from app.db import engine as db_engine
    from app.db.session import read_scope, write_scope
    from app.repositories.agent_runs_repository import get_agent_runs_repository

    if not db_engine.is_configured():
        return 0

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    runs_repo = get_agent_runs_repository()
    rows = await runs_repo.monthly_usage_by_agent(
        month_start=month_start,
        month_end=now.replace(microsecond=0),
    )

    totals: dict[str, dict[str, float]] = {}
    for r in rows:
        aid = r["agent_id"]
        bucket = totals.setdefault(aid, {"tokens": 0, "cost_cents": 0.0})
        bucket["tokens"] += int(r.get("total_tokens") or 0)
        if r.get("cost_cents") is not None:
            bucket["cost_cents"] += float(r["cost_cents"])

    if not totals:
        return 0

    # agent_runs.agent_id is a uuid column round-tripped as str (see the
    # VALUE-TYPE PARITY note at the top of agent_runs_repository.py) —
    # AiAgents.id is a native Uuid column, so bind real uuid.UUID objects.
    agent_ids = [UUID(aid) for aid in totals.keys()]
    async with read_scope() as session:
        agents = (
            (await session.execute(_agents_budget_scan_stmt(agent_ids)))
            .mappings()
            .all()
        )

    transitions = 0
    for agent in agents:
        aid = str(agent["id"])
        t = totals.get(aid, {"tokens": 0, "cost_cents": 0.0})
        token_budget = agent.get("monthly_token_budget")
        cost_budget = agent.get("monthly_cost_cents_budget")
        paused_reason = agent.get("paused_reason")

        over_tokens = token_budget is not None and t["tokens"] > int(token_budget)
        over_cost = cost_budget is not None and t["cost_cents"] > float(cost_budget)
        should_pause = over_tokens or over_cost

        if should_pause and paused_reason != "budget":
            # Don't clobber a manual pause.
            if paused_reason is None:
                async with write_scope() as session:
                    await session.execute(_agent_pause_stmt(agent["id"], "budget"))
                transitions += 1
                logger.info(
                    f"[sweeper] agent {aid} paused_by_budget "
                    f"(tokens={t['tokens']}, cost={t['cost_cents']})"
                )
        elif not should_pause and paused_reason == "budget":
            async with write_scope() as session:
                await session.execute(_agent_pause_stmt(agent["id"], None))
            transitions += 1
            logger.info(f"[sweeper] agent {aid} unpaused (budget cleared)")

    return transitions


@DBOS.scheduled("* * * * *")  # every minute
@DBOS.workflow()
async def agent_runs_sweeper_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """Run a single sweeper tick. DBOS dedup via workflow_id =
    `sched-agent_runs_sweeper_workflow-<iso>` ensures only one worker
    fires per cron tick across the cluster."""
    heartbeat_lost = await mark_heartbeat_lost_step()
    transitions = await recompute_monthly_budgets_step()
    # Drain BEFORE expiring: an item that is both stranded and a day old should
    # get its turn rather than be thrown away by the step running beside it.
    drained = await drain_idle_inbox_step()
    expired_inbox = await expire_orphan_inbox_step()
    reconciled = await reconcile_issue_execution_state_step()
    if heartbeat_lost or transitions or drained or expired_inbox or reconciled:
        logger.info(
            f"[sweeper] heartbeat_lost={heartbeat_lost} "
            f"budget_transitions={transitions} inbox_drained={drained} "
            f"expired_inbox={expired_inbox} reconciled_issues={reconciled}"
        )
