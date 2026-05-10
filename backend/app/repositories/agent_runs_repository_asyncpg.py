"""asyncpg + Supavisor implementation of AgentRunsRepository.

Phase 2 pilot of the supabase-py → asyncpg migration. API parity with
the legacy ``AgentRunsRepository`` so the factory in
``agent_runs_repository.py`` can swap implementations behind a feature
flag (``USE_ASYNCPG_AGENT_RUNS``) without touching call sites.

Why ``agent_runs`` first:
  - Medium complexity (6 methods, mix of CRUD + bulk update + aggregate)
  - Not in user-facing hot path → a regression here doesn't break
    parse / download flows
  - Already touches the new ``parent_run_id`` column from #199
    Phase 3a / 3b → migration also exercises that schema add
  - Sweeper writes via this repo (heartbeat-lost), which historically
    triggers the supabase-py path many times — Bug C exposure point

Behavioural parity vs legacy:
  - Same authz pattern (user_id filter at WHERE clause; missing rows
    read as 404 / empty rather than 403)
  - Same return shapes (list of dict for list_*, dict | None for
    get_by_id, bool for request_cancel, int for mark_heartbeat_lost)
  - Same error handling (log + return None / [] / 0 / False)
  - Same date handling (UTC ISO strings)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger

from app.db.repository_base import AsyncpgRepository


class AgentRunsRepositoryAsyncpg(AsyncpgRepository):
    """asyncpg-backed AgentRunsRepository. Same public API as the
    supabase-py version — see ``agent_runs_repository.py`` for the
    method-level docstrings (kept terse here to avoid drift)."""

    TABLE = "agent_runs"

    # ── Reads ───────────────────────────────────────────────────────

    async def list_by_agent(
        self,
        *,
        agent_id: UUID,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Paginated runs for one agent, scoped to the caller. Returns
        {"items": [...], "total": N}. Two queries — count + page —
        matching the supabase-py shape rather than collapsing into a
        single window-function query."""
        try:
            total = await self.fetch_value(
                "SELECT count(*) FROM agent_runs "
                "WHERE agent_id = $1 AND user_id = $2",
                agent_id,
                user_id,
            )
            items = await self.fetch_all(
                "SELECT * FROM agent_runs "
                "WHERE agent_id = $1 AND user_id = $2 "
                "ORDER BY started_at DESC "
                "LIMIT $3 OFFSET $4",
                agent_id,
                user_id,
                limit,
                offset,
            )
            return {"items": items, "total": int(total or 0)}
        except Exception as e:
            logger.error(f"Failed to list runs (agent={agent_id}, user={user_id}): {e}")
            return {"items": [], "total": 0}

    async def get_by_id(
        self, run_id: UUID, *, user_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """Single run; None if not found OR not owned (no existence
        leak)."""
        try:
            return await self.fetch_one(
                "SELECT * FROM agent_runs WHERE id = $1 AND user_id = $2",
                run_id,
                user_id,
            )
        except Exception as e:
            logger.error(f"Failed to get run {run_id}: {e}")
            return None

    async def list_children(
        self,
        parent_run_id: UUID,
        *,
        user_id: UUID,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Phase 4 of #199 — direct children of one parent run."""
        try:
            return await self.fetch_all(
                "SELECT * FROM agent_runs "
                "WHERE parent_run_id = $1 AND user_id = $2 "
                "ORDER BY started_at DESC "
                "LIMIT $3",
                parent_run_id,
                user_id,
                limit,
            )
        except Exception as e:
            logger.error(f"Failed to list children for parent={parent_run_id}: {e}")
            return []

    # ── Writes ──────────────────────────────────────────────────────

    async def request_cancel(self, run_id: UUID, *, user_id: UUID) -> bool:
        """Set cancel_requested=true. Idempotent. Only acts on running,
        owned rows. Returns True iff a row was actually updated."""
        try:
            updated = await self.fetch_one(
                "UPDATE agent_runs "
                "SET cancel_requested = true "
                "WHERE id = $1 AND user_id = $2 AND status = 'running' "
                "RETURNING id",
                run_id,
                user_id,
            )
            return updated is not None
        except Exception as e:
            logger.error(f"Failed to request cancel for run {run_id}: {e}")
            return False

    # ── Sweeper ─────────────────────────────────────────────────────

    async def mark_heartbeat_lost(self, *, stale_before: datetime) -> int:
        """Bulk-flip stuck running rows → heartbeat_lost. Returns row
        count for telemetry. Caller (sweeper cron) holds an advisory
        lock so this is serialized."""
        try:
            # asyncpg's timestamp codec wants datetime objects, NOT
            # isoformat strings — passing str raises DataError.
            # Caught by tests/integration/test_asyncpg_repos.py.
            rows = await self.fetch_all(
                "UPDATE agent_runs "
                "SET status = 'heartbeat_lost', "
                "    ended_at = $1, "
                "    error_code = 'heartbeat_lost', "
                "    error_message = 'No heartbeat for >2 minutes' "
                "WHERE status = 'running' AND heartbeat_at < $2 "
                "RETURNING id",
                datetime.now(timezone.utc),
                stale_before,
            )
            return len(rows)
        except Exception as e:
            logger.error(f"Failed to mark heartbeat_lost: {e}")
            return 0

    # ── Aggregate ───────────────────────────────────────────────────

    async def monthly_usage_by_agent(
        self,
        *,
        month_start: datetime,
        month_end: datetime,
    ) -> List[Dict[str, Any]]:
        """Raw rows for [month_start, month_end). Caller groups in
        Python (matches legacy behaviour — moving aggregation to SQL
        is a Phase 5+ optimization, intentionally out of scope here
        to keep the migration mechanical)."""
        try:
            # datetime objects, not isoformat — see mark_heartbeat_lost.
            return await self.fetch_all(
                "SELECT agent_id, user_id, team_id, project_id, status, "
                "       prompt_tokens, completion_tokens, total_tokens, "
                "       cost_cents "
                "FROM agent_runs "
                "WHERE started_at >= $1 AND started_at < $2",
                month_start,
                month_end,
            )
        except Exception as e:
            logger.error(f"Failed to load monthly usage: {e}")
            return []


__all__ = ["AgentRunsRepositoryAsyncpg"]
