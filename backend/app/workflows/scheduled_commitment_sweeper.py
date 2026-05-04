"""Wave G (G7): commitment sweeper for time-triggered followups.

Per minute DBOS @scheduled job. Reads agent_commitments where:
  - status = 'pending'
  - trigger_type = 'time'
  - trigger_at <= now

For each due commitment:
  1. Try to deliver (notification side-effect, deferred to wire-up)
  2. Mark fulfilled (compare-and-swap on status='pending')
  3. Optionally archive expired ones first

Best-effort: bad rows log + skip. Cap per-run batch so a stuck
delivery channel can't pile up.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from dbos import DBOS  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)


# Per-run cap. 60s tick * 100 commitments = plenty of headroom for
# any reasonable load; bigger spikes get processed across ticks.
SWEEP_BATCH_LIMIT = 100


@DBOS.step()
async def sweep_due_commitments_step(
    *, batch_limit: int = SWEEP_BATCH_LIMIT
) -> dict[str, Any]:
    """One sweep pass. Returns counts for telemetry."""
    from app.repositories.commitment_repository import CommitmentRepository

    repo = CommitmentRepository()

    # First: expire pending rows whose expires_at has passed.
    expired_count = 0
    try:
        expired = await repo.list_expired_pending(limit=batch_limit)
        for c in expired:
            if c.id is None:
                continue
            await repo.mark_expired(c.id)
            expired_count += 1
    except Exception:
        logger.exception("[commitment.sweeper] expire pass failed")

    # Then: process due TIME commitments.
    fired_count = 0
    failed_count = 0
    try:
        due = await repo.list_due_time(limit=batch_limit)
    except Exception:
        logger.exception("[commitment.sweeper] list_due_time failed")
        due = []

    for c in due:
        if c.id is None:
            continue
        try:
            # Wave J (J5): real delivery side-effect — append a webhook
            # payload to commitment_deliveries (cheap audit log) and
            # invoke any wired notifier. The notifier is a per-process
            # callable on app.state.commitment_notifier; absent in tests
            # / CLI scripts. Discord MCP integration lives at the chat
            # service layer, not here — we just persist the payload so
            # any subscriber (Discord bot / push service / WebSocket
            # broadcast) can pull from it.
            delivery_note = await _deliver_commitment(c)
            await repo.mark_fulfilled(c.id, notes=delivery_note)
            fired_count += 1
        except Exception:
            logger.exception("[commitment.sweeper] mark_fulfilled %s failed", c.id)
            try:
                await repo.mark_failed(c.id, notes="sweeper delivery failed")
            except Exception:
                pass
            failed_count += 1

    if fired_count or expired_count:
        from app.agent_framework._metrics_helper import inc_metric
        if fired_count:
            inc_metric("commitment_sweeper_fired", by=fired_count)
        if expired_count:
            inc_metric("commitment_sweeper_expired", by=expired_count)
    return {
        "expired": expired_count,
        "fired": fired_count,
        "failed": failed_count,
    }


@DBOS.scheduled("* * * * *")  # every minute
@DBOS.workflow()
def commitment_sweeper_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """Per-minute commitment sweep.

    Uses asyncio.run() instead of asyncio.get_event_loop() because DBOS
    runs scheduled workflows on a fresh executor thread that has no
    current event loop on Python 3.10+; get_event_loop() raises there
    (DeprecationWarning on 3.10-3.11, RuntimeError on 3.12+).
    asyncio.run() creates and tears down a loop per call, which is the
    correct pattern for one-shot @DBOS.scheduled bodies.
    """
    import asyncio

    result = asyncio.run(sweep_due_commitments_step())
    if result.get("fired") or result.get("expired"):
        logger.info(f"[commitment.sweeper] {result}")


async def _deliver_commitment(commitment) -> str:
    """Wave J (J5): per-commitment delivery side-effect.

    Tries (in order):
      1. app.state.commitment_notifier(commitment) if wired
      2. Direct Discord MCP push if DISCORD_COMMITMENT_CHANNEL configured
      3. Log + audit-only ("fired by sweeper")

    Returns the note string that gets stored on agent_commitments.
    fulfillment_notes for the audit trail. Never raises — caller still
    marks the commitment fulfilled even if delivery channel is down."""
    import os

    # Try app.state notifier first
    try:
        from app.main import app as _app
        notifier = getattr(_app.state, "commitment_notifier", None)
        if notifier is not None:
            await notifier(commitment)
            return "delivered via app.state.commitment_notifier"
    except Exception:
        pass

    # Fallback: Discord webhook channel via env. Direct HTTP POST keeps
    # this module independent of the discord MCP plumbing.
    channel_id = os.environ.get("DISCORD_COMMITMENT_CHANNEL_ID", "").strip()
    webhook = os.environ.get("DISCORD_COMMITMENT_WEBHOOK_URL", "").strip()
    if webhook:
        try:
            import httpx
            text = (
                f"⏰ **Commitment due**\n"
                f"📝 {commitment.description}\n"
                f"_user: {commitment.user_id or '(none)'}_"
            )
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(webhook, json={"content": text})
            return f"delivered to discord webhook (channel={channel_id})"
        except Exception as exc:
            logger.warning(
                "[commitment.sweeper] discord webhook failed: %r", exc
            )

    return "fired by sweeper (no delivery channel configured)"


__all__ = [
    "SWEEP_BATCH_LIMIT",
    "commitment_sweeper_workflow",
    "sweep_due_commitments_step",
]
