"""workforce_dispatch — DBOS-scheduled outbox + inbox dispatch.

PR-D8 Phase 3 — replaces the in-process WorkforceScheduler tick loop
with two DBOS @scheduled workflows. Functionally equivalent to the
legacy scheduler.py but with three structural wins:

1. **No in-process state in the gateway.** With Phase 1 split, only
   workers consume DBOS work. Removing WorkforceScheduler also removes
   one polling loop from the gateway process (when role=combined).

2. **Cluster-wide single-fire.** DBOS dedups @scheduled workflows by
   workflow_id = "sched-<name>-<iso>". With N worker pods running, only
   ONE fires the dispatch tick per scheduled time — the legacy scheduler
   ran on every replica that had role.runs_inprocess_schedulers=True,
   double-counting work in multi-worker setups.

3. **Durable retry / replay.** A dispatch tick that crashes mid-flight
   (PG hiccup, supabase 5xx) is replayed by DBOS from its checkpoint
   on the next worker start. Legacy scheduler just logged + waited for
   the next 5s tick, leaving the partial state for recovery on next
   poll.

Both ticks are wrapped in @DBOS.step() so individual ticks are durable
units. Idempotency:

* OutboxDispatcher.tick() flips delivered=true row-by-row; replay finds
  delivered=false rows again, no double-deliver.
* InboxProcessor.tick() flips inbox status=reading via CAS; replay sees
  status=reading and skips, no double-claim.

Scheduling cadence:
* outbox: 5s (keep parity with legacy scheduler.fast_tick_seconds=5)
* inbox:  10s (legacy ran inbox every 2nd outbox tick = 10s)

The 5s minimum granularity is a DBOS limitation (cron 6-field syntax).
For sub-second latency, a follow-up PR will add a PG NOTIFY trigger on
agent_outbox INSERT + LISTEN handler in workers — keeping cron as the
safety net.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict

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


@DBOS.step()
async def inbox_dispatch_tick_step() -> Dict[str, int]:
    """Drain agent_inbox via InboxProcessor.

    Returns InboxProcessor.tick()'s shape:
        {"agents_scanned": int, "tasks_enqueued": int, ...}
    """
    from app.services.workforce.inbox_processor import InboxProcessor

    processor = InboxProcessor()
    return await processor.tick()


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
    """Drain unread agent_inbox rows into the agent_workforce queue."""
    try:
        result = await inbox_dispatch_tick_step()
    except Exception as e:
        logger.exception(f"[workforce-dispatch] inbox tick crashed: {e}")
        return
    enqueued = result.get("tasks_enqueued", 0) if isinstance(result, dict) else 0
    if enqueued:
        logger.info(f"[workforce-dispatch] inbox enqueued={enqueued} {result}")
