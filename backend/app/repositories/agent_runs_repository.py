"""Repository for agent_runs table — read paths only (writes go through RunRecorder).

Phase 2 of the supabase-py → asyncpg migration installs a
``get_agent_runs_repository()`` factory that returns either the
legacy supabase-py implementation (this file) or the new asyncpg
one (``agent_runs_repository_asyncpg.py``) depending on the
``USE_ASYNCPG_AGENT_RUNS`` flag. Call sites import the factory
instead of the class so the swap is invisible to them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from loguru import logger

from app.core.config import settings
from app.db.supabase_client import get_async_supabase_admin


class AgentRunsRepository:
    """Data access for agent_runs. Writes are exclusively owned by RunRecorder;
    this repo exposes the read paths, the cancel trigger, and sweeper helpers.

    RLS on the table already restricts rows to the calling user's scope when
    a user-scoped client is used. We use the admin client here for service
    reads (list/detail) and let the router layer enforce user-scope via
    ``auth.user_id`` filters in the query. The cancel path verifies ownership
    before flipping the flag.
    """

    TABLE = "agent_runs"

    async def _get_client(self):
        return await get_async_supabase_admin()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_by_agent(
        self,
        *,
        agent_id: UUID,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Paginated runs for one agent, scoped to the caller.

        Returns {"items": [...], "total": N}. Admin client + explicit user_id
        filter substitutes for RLS at this layer (admin client bypasses RLS).
        """
        try:
            client = await self._get_client()
            # Count first
            count_result = (
                await client.table(self.TABLE)
                .select("id", count="exact", head=True)
                .eq("agent_id", str(agent_id))
                .eq("user_id", str(user_id))
                .execute()
            )
            total = count_result.count or 0

            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("agent_id", str(agent_id))
                .eq("user_id", str(user_id))
                .order("started_at", desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )
            return {"items": result.data or [], "total": total}
        except Exception as e:
            logger.error(f"Failed to list runs (agent={agent_id}, user={user_id}): {e}")
            return {"items": [], "total": 0}

    async def get_by_id(
        self, run_id: UUID, *, user_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """Single run detail; returns None if not found OR not owned by user.

        The second clause doubles as authz: a stray run_id from another user
        reads as 404, not 403, to avoid leaking existence.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("id", str(run_id))
                .eq("user_id", str(user_id))
                .maybe_single()
                .execute()
            )
            return result.data if result and result.data else None
        except Exception as e:
            logger.error(f"Failed to get run {run_id}: {e}")
            return None

    async def list_children(
        self,
        parent_run_id: UUID,
        *,
        user_id: UUID,
        limit: int = 100,
    ) -> list[Dict[str, Any]]:
        """Phase 4 of #199: direct children of one parent run.

        Direct only — caller asks recursively if they want a full tree
        (this avoids surprise N+1 explosions and keeps the per-call
        cost predictable). The user_id filter doubles as authz: a
        stray parent_run_id from another user reads as empty rather
        than leaking existence.

        Ordered by started_at DESC so the newest sub-spawn is at top —
        matches Runs UI's "newest first" convention.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select("*")
                .eq("parent_run_id", str(parent_run_id))
                .eq("user_id", str(user_id))
                .order("started_at", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to list children for parent={parent_run_id}: {e}")
            return []

    # ------------------------------------------------------------------
    # Cancel (flip flag; runner observes via RunRecorder.check_cancelled)
    # ------------------------------------------------------------------

    async def request_cancel(self, run_id: UUID, *, user_id: UUID) -> bool:
        """Set cancel_requested=true. Idempotent. Only acts on running, owned rows.

        Returns True when the flag was flipped (or already pending). False if
        the run doesn't exist, is already in a terminal state, or is not owned
        by the caller.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .update({"cancel_requested": True})
                .eq("id", str(run_id))
                .eq("user_id", str(user_id))
                .eq("status", "running")
                .execute()
            )
            return bool(result.data)
        except Exception as e:
            logger.error(f"Failed to request cancel for run {run_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # Sweeper helpers
    # ------------------------------------------------------------------

    async def mark_heartbeat_lost(self, *, stale_before: datetime) -> int:
        """Flip all running rows with heartbeat_at < stale_before → heartbeat_lost.

        Returns the number of rows updated. Caller is the sweeper cron; advisory
        lock is held one level up so this call is assumed serialized.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .update(
                    {
                        "status": "heartbeat_lost",
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                        "error_code": "heartbeat_lost",
                        "error_message": "No heartbeat for >2 minutes",
                    }
                )
                .eq("status", "running")
                .lt("heartbeat_at", stale_before.isoformat())
                .execute()
            )
            return len(result.data or [])
        except Exception as e:
            logger.error(f"Failed to mark heartbeat_lost: {e}")
            return 0

    async def monthly_usage_by_agent(
        self,
        *,
        month_start: datetime,
        month_end: datetime,
    ) -> List[Dict[str, Any]]:
        """Aggregate tokens + cost grouped by agent for [month_start, month_end).

        Used by the sweeper to compare against each agent's monthly budget,
        and by the usage endpoint for the AI Usage dashboard. Scope filtering
        (user / team / project) layers on top at the endpoint.

        Returns raw rows; the caller groups in Python. For production scale
        this should move to a SQL function, but for phase-1 volumes (hundreds
        of runs/month) client-side aggregation is fine.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE)
                .select(
                    "agent_id,user_id,team_id,project_id,status,"
                    "prompt_tokens,completion_tokens,total_tokens,cost_cents"
                )
                .gte("started_at", month_start.isoformat())
                .lt("started_at", month_end.isoformat())
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to load monthly usage: {e}")
            return []


# ─── Factory ──────────────────────────────────────────────────────────


def get_agent_runs_repository() -> (
    Union[AgentRunsRepository, "AgentRunsRepositoryAsyncpg"]
):
    """Return the active AgentRunsRepository implementation.

    Routing:
      - ``settings.USE_ASYNCPG_AGENT_RUNS=True`` AND Supavisor configured
        → asyncpg + Supavisor implementation
      - else → legacy supabase-py implementation (this file)

    Both classes expose the same public method signatures, so call sites
    just do ``repo = get_agent_runs_repository()`` and use it the same
    way regardless of backend.
    """
    if settings.USE_ASYNCPG_AGENT_RUNS:
        from app.db.engine import is_configured

        if is_configured():
            from app.repositories.agent_runs_repository_asyncpg import (
                AgentRunsRepositoryAsyncpg,
            )

            return AgentRunsRepositoryAsyncpg()
        # Flag on but URL missing — log once + fall back so a
        # half-configured deploy doesn't crash.
        logger.warning(
            "USE_ASYNCPG_AGENT_RUNS=true but SUPAVISOR_DATABASE_URL "
            "is empty — falling back to supabase-py path"
        )
    return AgentRunsRepository()
