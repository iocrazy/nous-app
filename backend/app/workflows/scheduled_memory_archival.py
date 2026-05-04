"""Wave F (F5): scheduled archival of decayed memories.

Weekly DBOS job. Reads agent_memories.status='active' rows whose
decay_score has dropped below DEFAULT_ARCHIVE_THRESHOLD and flips them
to status='archived'. Archived rows are excluded from retrieval (the
M2.A migration adds `idx_agent_memories_active_namespace` partial
index that excludes status != 'active').

Why scheduled (not real-time): decay is a slow signal — checking each
memory at retrieve time would burn CPU on a hot path; sweeping nightly
is more than fast enough.

Conservative: archives in batches of N (default 500) per agent_user
namespace per run, with no failure cascading. A bad row logs + skips.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from dbos import DBOS  # type: ignore[import-not-found]

from app.services.memory.decay import (
    DEFAULT_ARCHIVE_THRESHOLD,
    DEFAULT_HALF_LIFE_DAYS,
    MemoryDecayInput,
    should_archive,
)

logger = logging.getLogger(__name__)


# Tunable: how many rows we touch per sweep run. Cap so a single run
# can't lock the table for minutes if a million rows suddenly qualify.
SWEEP_BATCH_LIMIT = 5000


@DBOS.step()
async def archive_decayed_memories_step(
    *,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    archive_threshold: float = DEFAULT_ARCHIVE_THRESHOLD,
    batch_limit: int = SWEEP_BATCH_LIMIT,
) -> dict[str, Any]:
    """One sweep pass. Returns counts for telemetry."""
    from app.db import get_async_supabase_admin

    sb = await get_async_supabase_admin()
    now = datetime.now(timezone.utc)

    try:
        # Pull candidate batch — partial index makes this cheap
        result = await (
            sb.table("agent_memories")
            .select("id, created_at, last_recalled_at, reinforcement_count")
            .eq("status", "active")
            .order("last_recalled_at", desc=False, nullsfirst=True)
            .order("created_at", desc=False)
            .limit(batch_limit)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[memory.archival] candidate fetch failed: %s", exc)
        return {"checked": 0, "archived": 0, "errors": 1}

    rows = result.data or []
    archive_ids: list[str] = []
    for row in rows:
        try:
            mem = MemoryDecayInput(
                created_at=_parse_ts(row.get("created_at")),
                last_recalled_at=_parse_ts(row.get("last_recalled_at")),
                reinforcement_count=int(row.get("reinforcement_count") or 0),
            )
            if should_archive(
                mem,
                now=now,
                half_life_days=half_life_days,
                threshold=archive_threshold,
            ):
                archive_ids.append(row["id"])
        except (KeyError, ValueError, TypeError):
            continue  # one bad row shouldn't sink the batch

    if not archive_ids:
        return {"checked": len(rows), "archived": 0}

    archived = 0
    for mem_id in archive_ids:
        try:
            await (
                sb.table("agent_memories")
                .update(
                    {
                        "status": "archived",
                        "archived_at": now.isoformat(),
                    }
                )
                .eq("id", mem_id)
                .execute()
            )
            archived += 1
        except Exception:  # noqa: BLE001 — single-row failure shouldn't stop sweep
            logger.exception("[memory.archival] failed to archive %s", mem_id)

    if archived:
        from app.agent_framework._metrics_helper import inc_metric
        inc_metric("memory_archived", by=archived)
    return {"checked": len(rows), "archived": archived}


@DBOS.scheduled("0 3 * * 0")  # Sunday 03:00 UTC
@DBOS.workflow()
def memory_archival_workflow(scheduled_time: datetime, actual_time: datetime) -> None:
    """Weekly memory archival pass."""
    import asyncio

    result = asyncio.run(
        archive_decayed_memories_step()
    )
    logger.info(f"[memory.archival] sweep complete: {result}")


def _parse_ts(value: Any) -> datetime:
    """Tolerant ISO8601 parse → UTC-aware datetime. Defaults to NOW
    when value is missing (fresh row, treat as just-created)."""
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


__all__ = [
    "SWEEP_BATCH_LIMIT",
    "archive_decayed_memories_step",
    "memory_archival_workflow",
]
