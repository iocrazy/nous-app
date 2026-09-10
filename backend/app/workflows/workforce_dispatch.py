"""workforce_dispatch — DBOS-scheduled outbox + inbox dispatch.

PR-D8 Phase 3 replaced the in-process WorkforceScheduler tick loop with two
DBOS @scheduled workflows; harness 2b-2 T3 deleted that scheduler's module and
finished wiring the dispatch it never performed. Three structural wins over
the loop it replaced:

1. **No in-process state in the gateway.** With Phase 1 split, only
   workers consume DBOS work. Dropping the scheduler also removed
   one polling loop from the gateway process (when role=combined).

2. **Cluster-wide single-fire.** DBOS dedups @scheduled workflows by
   workflow_id = "sched-<name>-<iso>". With N worker pods running, only
   ONE fires the dispatch tick per scheduled time — the legacy scheduler
   ran on every replica that had role.runs_inprocess_schedulers=True,
   double-counting work in multi-worker setups.

3. **Durable retry / replay.** A dispatch tick that crashes mid-flight
   (PG hiccup, supabase 5xx) is replayed by DBOS from its checkpoint
   on the next worker start. The legacy scheduler just logged + waited
   for the next 5s tick, leaving the partial state for recovery on next
   poll.

Both ticks are wrapped in @DBOS.step() so individual ticks are durable
units. Idempotency:

* OutboxDispatcher.tick() flips delivered=true row-by-row; replay finds
  delivered=false rows again, no double-deliver.
* InboxProcessor.tick() flips inbox status=reading via CAS; replay sees
  status=reading and skips, no double-claim.
* The inbox tick's dispatch loop lives in the workflow BODY, and its step
  result (orders included) is memoized — so a replay re-runs the loop over
  the SAME orders, at the SAME ``dispatch_attempt``, producing the SAME
  workflow ids. DBOS declines those, which is exactly what a replay should
  get. ⚠️ Do not read that as general idempotency: DBOS declining an id it
  already holds means NEVER AGAIN, not "runs once". A task that legitimately
  needs a second run gets a fresh id from an incremented attempt counter —
  see ``dbos_pool.workflow_id_for``. The claim that gates execution is the PG
  row CAS in ``AgentWorkforceRepository.claim_task``; the ``dispatched_at``
  stamp is a cheap first filter, not the correctness boundary.

Scheduling cadence:
* outbox: 5s (parity with the legacy scheduler's fast_tick_seconds=5)
* inbox:  10s (the legacy loop ran inbox every 2nd outbox tick = 10s)

The 5s minimum granularity is a DBOS limitation (cron 6-field syntax).
For sub-second latency, a follow-up PR will add a PG NOTIFY trigger on
agent_outbox INSERT + LISTEN handler in workers — keeping cron as the
safety net.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from dbos import DBOS
from loguru import logger


@DBOS.step()
async def outbox_dispatch_tick_step() -> Dict[str, int]:
    """Drain agent_outbox via OutboxDispatcher.

    Returns OutboxDispatcher.tick() shape so the scheduled workflow can
    log it for observability:
        {"scanned": int, "delivered": int, "errors": int}
    """
    from app.services.workforce.outbox_dispatcher import OutboxDispatcher

    dispatcher = OutboxDispatcher()
    return await dispatcher.tick()


# ── Module-level indirection ────────────────────────────────────────
#
# These four thin wrappers exist so the chain is testable at its own seams:
# without them a test can only reach the repository singleton and the DBOS
# queue by patching deep inside two other modules. They are also the line the
# route-C source guard reads — ``_pool()`` is deliberately NOT reachable from
# any ``@DBOS.step`` body.


def _inbox_processor() -> Any:
    from app.services.workforce.inbox_processor import InboxProcessor

    return InboxProcessor()


def _pool() -> Any:
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    return DbosAgentWorkforcePool()


async def _list_undispatched() -> List[Dict[str, Any]]:
    from app.repositories.agent_workforce_repository import (
        get_agent_workforce_repository,
    )

    return await get_agent_workforce_repository().list_undispatched_queued_tasks()


async def _mark_dispatched(task_id: str, *, workflow_id: str, attempt: int) -> None:
    from app.repositories.agent_workforce_repository import (
        get_agent_workforce_repository,
    )

    await get_agent_workforce_repository().mark_dispatched(
        task_id, workflow_id=workflow_id, attempt=attempt
    )


def _order_envelope(task: Dict[str, Any]) -> Dict[str, Any]:
    """The three fields ``DbosAgentWorkforcePool.dispatch`` reads, and nothing
    else.

    The step's return value is checkpointed into ``dbos.operation_outputs``
    on every tick, so whole task rows — prompt text included, up to 100 per
    tick, every 10 seconds — would accumulate there on top of the copy DBOS
    already keeps as the workflow input. The worker reads the authoritative row
    back when it claims it (``claim_task`` returns the full shape), so it also
    stops acting on a snapshot taken at dispatch time."""
    return {
        "id": task.get("id"),
        "agent_id": task.get("agent_id"),
        "dispatch_attempt": int(task.get("dispatch_attempt") or 0),
    }


@DBOS.step()
async def inbox_dispatch_tick_step() -> Dict[str, Any]:
    """Drain agent_inbox into task rows, then SHAPE the dispatch queue.

    Returns InboxProcessor.tick()'s counters plus one key of our own:

        {"agents_processed": int, "tasks_created": int, "errors": int,
         "orders": [{"id", "agent_id", "dispatch_attempt"}, ...]}

    ``orders`` is every queued agent_task carrying no ``metadata.dispatched_at``
    — NOT merely the rows this tick created. A task minted outside the inbox
    path (a background sub-agent) is queued by its creator and picked up here.

    This step does NOT enqueue anything. Starting or enqueuing a workflow from
    inside a ``@DBOS.step`` is forbidden (CLAUDE.md route C) — the dispatch
    lives in the workflow BODY below.
    """
    processor = _inbox_processor()
    stats: Dict[str, Any] = dict(await processor.tick())
    stats["orders"] = [_order_envelope(t) for t in await _list_undispatched()]
    return stats


@DBOS.scheduled("*/5 * * * * *")  # every 5 seconds
@DBOS.workflow()
async def outbox_dispatch_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """Drain undelivered agent_outbox rows.

    Workflow_id is auto-set by DBOS to
    ``sched-outbox_dispatch_workflow-<iso>`` which dedups across the
    cluster — only one worker fires per scheduled tick.
    """
    try:
        result = await outbox_dispatch_tick_step()
    except Exception as e:
        # Step retries handle transient failures; if we still get here,
        # log loud and let the next tick try again. Don't raise — DBOS
        # would mark the workflow failed and the cron would still fire
        # next tick anyway.
        logger.exception(f"[workforce-dispatch] outbox tick crashed: {e}")
        return
    delivered = result.get("delivered", 0) if isinstance(result, dict) else 0
    if delivered:
        logger.info(f"[workforce-dispatch] outbox delivered={delivered} {result}")


@DBOS.scheduled("*/10 * * * * *")  # every 10 seconds
@DBOS.workflow()
async def inbox_dispatch_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """Drain unread agent_inbox rows, then enqueue every undispatched task.

    The enqueue happens HERE, in the workflow body, never in the step above:
    DBOS refuses to start a workflow from inside a step (the empty-string
    AssertionError of PR #495), and route C makes that a standing rule rather
    than a detail of one call site.
    """
    try:
        result = await inbox_dispatch_tick_step()
    except Exception as e:
        logger.exception(f"[workforce-dispatch] inbox tick crashed: {e}")
        return
    if not isinstance(result, dict):
        return
    orders = result.get("orders") or []
    pool = _pool()
    dispatched = 0
    for order in orders:
        try:
            record = await pool.dispatch(order)
            if record is None:
                # Nothing reached the queue. Leave the row unstamped so the
                # next tick sees it again — stamping a dispatch that did not
                # happen hides the task from every later tick while no
                # workflow exists to run it.
                logger.warning(
                    f"[workforce-dispatch] order {order.get('id')} not enqueued"
                )
                continue
            await _mark_dispatched(
                str(order["id"]),
                workflow_id=record.workflow_id,
                attempt=record.attempt,
            )
            dispatched += 1
        except Exception as e:  # one bad order never starves the rest
            logger.exception(f"[workforce-dispatch] order {order.get('id')}: {e}")
    created = result.get("tasks_created", 0)
    if created or orders:
        logger.info(
            f"[workforce-dispatch] inbox created={created} "
            f"dispatched={dispatched}/{len(orders)}"
        )
