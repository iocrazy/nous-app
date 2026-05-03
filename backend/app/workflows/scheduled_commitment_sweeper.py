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
            # TODO: real delivery side-effect (Discord ping / push
            # notification / agent re-prompt). For now we just mark
            # fulfilled — the row + run_id audit trail is enough for
            # admin to see "this fired".
            note = "fired by sweeper"
            await repo.mark_fulfilled(c.id, notes=note)
            fired_count += 1
        except Exception:
            logger.exception("[commitment.sweeper] mark_fulfilled %s failed", c.id)
            try:
                await repo.mark_failed(c.id, notes="sweeper delivery failed")
            except Exception:
                pass
            failed_count += 1

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
    """Per-minute commitment sweep."""
    import asyncio

    result = asyncio.get_event_loop().run_until_complete(sweep_due_commitments_step())
    if result.get("fired") or result.get("expired"):
        logger.info(f"[commitment.sweeper] {result}")


__all__ = [
    "SWEEP_BATCH_LIMIT",
    "commitment_sweeper_workflow",
    "sweep_due_commitments_step",
]
