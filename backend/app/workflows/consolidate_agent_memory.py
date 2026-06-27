"""Weekly DBOS workflow: consolidate (user, agent) pairs into durable memory.

Mirrors the agent_cost_anomaly.py enumerate-then-process pattern.
Schedule: Mondays at 06:17 UTC ("17 6 * * 1").

Architecture
------------
- enumerate_active_pairs_step: one SQL pass → distinct (user, agent) pairs
  active in the last 7 days with >= MIN_NEW_MESSAGES recent messages.
- consolidate_pair_step: DBOS step wrapping the plain _consolidate_pair helper.
- _consolidate_pair: plain async helper (not decorated) so both the weekly
  scheduled run and the admin manual trigger (Task 4) can call it.
- consolidate_agent_memory_workflow: DBOS scheduled workflow (Mon 06:17 UTC).

Privacy: every row written is visibility='private', scope='agent_user'.
Dedup: fingerprint-keyed on normalised title (existing_fingerprints lookup).
Best-effort: per-pair failures are caught + logged, never abort the run.
DBOS steps do NOT dispatch nested workflows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from dbos import DBOS
from loguru import logger

from app.db import engine as db_engine
from app.repositories.agent_memory_repository import (
    existing_fingerprints,
    write_memory_row,
)
from app.services.ai.memory.agent_memory_consolidation import consolidate_pair
from app.services.ai.memory.agent_memory_consolidator import default_consolidator

MIN_NEW_MESSAGES = 6
MAX_ENTRIES_PER_PAIR = 10
_LOOKBACK_DAYS = 7

# Distinct active (user, agent) pairs with their recent message count.
# Filtered in Python after fetch to let MIN_NEW_MESSAGES be a module constant.
_ACTIVE_PAIRS_SQL = """
SELECT
    s.user_id::text  AS user_id,
    s.agent_id::text AS agent_id,
    COUNT(m.id)::int AS msg_count
FROM public.ai_sessions s
JOIN public.ai_messages m ON m.session_id = s.id
WHERE s.updated_at  >= now() - interval '7 days'
  AND s.agent_id    IS NOT NULL
  AND s.user_id     IS NOT NULL
  AND m.created_at  >= now() - interval '7 days'
GROUP BY s.user_id, s.agent_id
"""

# Recent messages for one (user, agent), ascending, capped at 40 to bound
# the prompt size.
_RECENT_MESSAGES_SQL = """
SELECT m.role, m.content
FROM public.ai_messages m
JOIN public.ai_sessions s ON s.id = m.session_id
WHERE s.user_id  = :user_id
  AND s.agent_id = :agent_id
  AND m.created_at >= now() - interval '7 days'
ORDER BY m.created_at ASC
LIMIT 40
"""

# Existing memory titles to supply to the /dream prompt so the model
# knows which topics are already covered.
_EXISTING_TITLES_SQL = """
SELECT title
FROM public.agent_memory
WHERE owner_user_id = :user_id
  AND agent_id      = :agent_id
  AND status        = 'active'
ORDER BY created_at DESC
LIMIT 50
"""


async def _consolidate_pair(user_id: str, agent_id: str) -> dict[str, Any]:
    """Load → consolidate → write for one (user, agent) pair.

    Plain async helper (not DBOS-decorated) so both the weekly DBOS step
    and the admin manual trigger (Task 4) can call it without wrapping in
    a nested workflow (a DBOS anti-pattern).

    Returns {"written": n, "skipped": m}.
      written  = rows successfully inserted into agent_memory
      skipped  = write failures (write_memory_row returned False)
      (dedup is handled by consolidate_pair before reaching the write loop)

    Best-effort: any exception is caught, logged, and returns zeros so the
    caller never aborts.
    """
    try:
        # 1. Load recent messages (cost guard applied immediately).
        messages = await db_engine.fetch_all(
            _RECENT_MESSAGES_SQL,
            {"user_id": user_id, "agent_id": agent_id},
        )
        if len(messages) < MIN_NEW_MESSAGES:
            logger.debug(
                f"[consolidate_agent_memory] pair user={user_id} agent={agent_id} "
                f"has only {len(messages)} messages — skipping (threshold={MIN_NEW_MESSAGES})"
            )
            return {"written": 0, "skipped": 0}

        # 2. Build the recent-activity text for the /dream prompt.
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = (msg.get("content") or "").strip()
            if content:
                parts.append(f"[{role}]: {content}")
        recent_activity = "\n".join(parts)

        # 3. Existing memory titles (prompt context: skip these topics).
        title_rows = await db_engine.fetch_all(
            _EXISTING_TITLES_SQL,
            {"user_id": user_id, "agent_id": agent_id},
        )
        existing_titles = [r["title"] for r in title_rows if r.get("title")]

        # 4. Existing fingerprints (dedup lookup).
        fps = await existing_fingerprints(owner_user_id=user_id, agent_id=agent_id)

        # 5. Consolidate: prompt → LLM call → parse → dedup → cap at max_entries.
        pairs = await consolidate_pair(
            owner_user_id=user_id,
            agent_id=agent_id,
            recent_activity=recent_activity,
            existing_titles=existing_titles,
            existing_fingerprints=fps,
            consolidator=default_consolidator,
            max_entries=MAX_ENTRIES_PER_PAIR,
        )

        # 6. Write each non-dup entry as a private agent_user memory row.
        written = 0
        skipped = 0
        for draft, fp in pairs:
            ok = await write_memory_row(
                owner_user_id=user_id,
                agent_id=agent_id,
                scope="agent_user",
                kind=draft.kind,
                title=draft.title,
                body_md=draft.body_md,
                when_to_use=draft.when_to_use,
                fingerprint=fp,
            )
            if ok:
                written += 1
            else:
                skipped += 1  # write failure (best-effort)

        logger.info(
            f"[consolidate_agent_memory] pair user={user_id} agent={agent_id}: "
            f"written={written} skipped={skipped}"
        )
        return {"written": written, "skipped": skipped}

    except Exception:  # noqa: BLE001 — per-pair errors must not abort the run
        logger.opt(exception=True).warning(
            f"[consolidate_agent_memory] pair failed "
            f"user={user_id} agent={agent_id}"
        )
        return {"written": 0, "skipped": 0}


@DBOS.step()
async def consolidate_pair_step(user_id: str, agent_id: str) -> dict[str, Any]:
    """DBOS step wrapper — delegates to the plain _consolidate_pair helper.

    Kept thin so the admin endpoint (Task 4) can call _consolidate_pair
    directly without entering a DBOS step context.
    """
    return await _consolidate_pair(user_id, agent_id)


@DBOS.step()
async def enumerate_active_pairs_step() -> list[dict]:
    """Distinct (user_id, agent_id) pairs with >= MIN_NEW_MESSAGES in the window.

    Fetches all active pairs then filters in Python so the MIN_NEW_MESSAGES
    constant stays in this module rather than embedded in SQL.
    """
    rows = await db_engine.fetch_all(_ACTIVE_PAIRS_SQL)
    return [r for r in rows if (r.get("msg_count") or 0) >= MIN_NEW_MESSAGES]


@DBOS.scheduled("17 6 * * 1")  # weekly Monday at 06:17 UTC
@DBOS.workflow()
async def consolidate_agent_memory_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """Weekly consolidation: distil each active (user, agent) pair's recent
    sessions into durable private memory via the governed LLM.

    All written rows are visibility='private', scope='agent_user'.
    FEATURE_AGENT_MEMORY recall flag stays off until Phase C.
    """
    pairs = await enumerate_active_pairs_step()
    logger.info(
        f"[consolidate_agent_memory] consolidating {len(pairs)} active pairs "
        f"(scheduled={scheduled_time.isoformat()})"
    )

    total_written = 0
    total_skipped = 0
    for pair in pairs:
        result = await consolidate_pair_step(pair["user_id"], pair["agent_id"])
        total_written += result.get("written", 0)
        total_skipped += result.get("skipped", 0)

    logger.info(
        f"[consolidate_agent_memory] done — "
        f"written={total_written} skipped={total_skipped} pairs={len(pairs)}"
    )


__all__ = [
    "_consolidate_pair",
    "consolidate_pair_step",
    "consolidate_agent_memory_workflow",
    "enumerate_active_pairs_step",
    "MAX_ENTRIES_PER_PAIR",
    "MIN_NEW_MESSAGES",
]
