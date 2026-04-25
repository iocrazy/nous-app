"""Delegate tool — cross-agent dispatch primitive.

The agent calls ``Delegate(agent_slug=..., prompt=...)`` to hand a
sub-task to another persistent agent. The tool:

    1. Resolves the target agent by slug (must exist, must be persistent).
    2. Rejects self-dispatch and cycles (parent chain check).
    3. Enforces a max delegation depth (cuts pathological recursion).
    4. Writes one inbox row for the target + one outbox row for the
       caller (audit + Realtime). Both link to the caller's run via
       parent_run_id so cost rollups stay tree-aware.
    5. Returns a status payload to the LLM — fire-and-forget by default;
       awaiting the result is M3 work (wait/notify on agent_outbox).

The actual execution of the delegated task happens later, when the
target agent's worker processes its inbox in the next dispatch tick.
This is intentional asynchrony — the caller doesn't block; instead it
sees the dispatch confirmation, decides whether to wait, and either
finishes the turn or asks for a status_query.

Notes on design choices:
- We reject self-delegate hard (same caller_agent_id == target_agent_id).
  Same-agent recursion needs explicit task spawning, not Delegate.
- M2 cycle protection is depth-only (``agent_depth >= MAX_DELEGATION_DEPTH``).
  This catches runaway recursion within a single dispatch tree but does NOT
  detect ping-pong cycles like A→B→A→B that stay below the depth cap. Full
  parent-chain walk + agent-membership check is M3 work; tracked as P0
  follow-up TODO.
- ``await=true`` is parsed but not yet honoured — M3's wait primitive
  will hook in here. For now the field is forwarded into the inbox
  payload so the target agent sees it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from loguru import logger

from app.repositories.agent_repository import AgentRepository
from app.repositories.agent_workforce_repository import AgentWorkforceRepository

# Maximum delegation depth from a single root run. 3 levels is enough for
# meaningful workforce composition (planner → executor → critic) without
# letting a buggy agent runaway-recurse into the inbox.
MAX_DELEGATION_DEPTH = 3


class DelegateToolService:
    """Per-turn service: caller context is baked in at construction.

    The chat-service wiring layer constructs one of these per agent turn,
    populating the caller's identity from the active run, then injects
    it into the AgentRunner. The runner dispatches ``Delegate`` calls to
    ``execute()``.
    """

    def __init__(
        self,
        *,
        caller_agent_id: UUID,
        caller_user_id: UUID,
        parent_run_id: Optional[UUID],
        agent_depth: int = 0,
        agent_repo: Optional[AgentRepository] = None,
        workforce_repo: Optional[AgentWorkforceRepository] = None,
    ) -> None:
        self.caller_agent_id = caller_agent_id
        self.caller_user_id = caller_user_id
        self.parent_run_id = parent_run_id
        self.agent_depth = agent_depth
        self.agent_repo = agent_repo or AgentRepository()
        self.workforce_repo = workforce_repo or AgentWorkforceRepository()

    async def execute(self, args: Dict[str, Any]) -> Dict[str, Any]:
        slug = (args.get("agent_slug") or args.get("agent") or "").strip()
        prompt = (args.get("prompt") or "").strip()
        if not slug:
            return {"error": "agent_slug required"}
        if not prompt:
            return {"error": "prompt required"}

        # Depth check first — cheap, no DB roundtrip needed.
        if self.agent_depth >= MAX_DELEGATION_DEPTH:
            return {
                "error": (
                    f"max delegation depth ({MAX_DELEGATION_DEPTH}) reached; "
                    "this would create a >3-level dispatch chain"
                ),
                "depth": self.agent_depth,
            }

        target = await self.agent_repo.get_by_slug(slug)
        if not target:
            return {"error": f"unknown agent slug: {slug}"}

        target_agent_id = UUID(target["id"])

        if target_agent_id == self.caller_agent_id:
            return {
                "error": "cannot delegate to self; spawn a sub-task instead",
            }

        if not target.get("persistent"):
            return {
                "error": (
                    f"agent '{slug}' is not configured as a persistent worker "
                    "(ai_agents.persistent=false). Persist it first or pick "
                    "a different agent."
                )
            }

        # Cycle detection: walk parent_run_id chain to root and reject
        # if any ancestor run's agent already appears in the chain.
        # Without this, A→B→A→B can stay under MAX_DELEGATION_DEPTH and
        # ping-pong indefinitely. We also catch broken/loopy data via a
        # hard cap on walk depth.
        cycle = await self._detect_cycle(target_agent_id=target_agent_id)
        if cycle is not None:
            return {
                "error": (
                    f"cycle detected: target '{slug}' already appears in the "
                    f"delegation chain (ancestor run {cycle})"
                ),
                "agent_slug": slug,
                "cycle_run_id": str(cycle),
            }

        # Forwarded options
        title = args.get("title")
        priority = int(args.get("priority") or 5)
        dedup_key = args.get("dedup_key")
        await_result = bool(args.get("await") or False)

        payload = {
            "title": title,
            "prompt": prompt,
            "delegated_by": str(self.caller_agent_id),
            "delegated_at_depth": self.agent_depth,
            "await": await_result,
            "parent_run_id": str(self.parent_run_id) if self.parent_run_id else None,
        }

        inbox_row = await self.workforce_repo.enqueue_inbox(
            recipient_agent_id=target_agent_id,
            sender_kind="agent",
            sender_user_id=self.caller_user_id,
            sender_agent_id=self.caller_agent_id,
            message_type="task",
            payload=payload,
            priority=priority,
            dedup_key=dedup_key,
        )

        outbox_row = await self.workforce_repo.enqueue_outbox(
            sender_agent_id=self.caller_agent_id,
            recipient_kind="agent",
            recipient_agent_id=target_agent_id,
            recipient_user_id=self.caller_user_id,
            message_type="task",
            payload=payload,
        )

        if not inbox_row:
            return {
                "error": "failed to enqueue inbox message",
                "agent_slug": slug,
            }

        logger.info(
            f"[delegate] {self.caller_agent_id} → {target_agent_id} "
            f"(slug={slug}, depth={self.agent_depth + 1}, "
            f"inbox={inbox_row.get('id')}, outbox={(outbox_row or {}).get('id')})"
        )

        return {
            "delegated_to": slug,
            "agent_id": str(target_agent_id),
            "inbox_message_id": inbox_row.get("id"),
            "outbox_message_id": (outbox_row or {}).get("id"),
            "status": "queued",
            "depth": self.agent_depth + 1,
            "await": await_result,
            "note": (
                "Task is queued. The target agent will pick it up on its "
                "next dispatch tick. Use a status_query message or read "
                "the outbox to track completion."
                if not await_result
                else "Awaited delegation is queued; M3 will block this turn until "
                "the target completes. For now the call returns immediately."
            ),
        }

    # ────────────────────────────────────────────────────────────
    # Cycle detection
    # ────────────────────────────────────────────────────────────

    # Walk depth cap. ``MAX_DELEGATION_DEPTH`` already bounds well-formed
    # chains; this is a defense-in-depth limit so a corrupt parent_run_id
    # cycle (data loop) doesn't loop the walker forever.
    MAX_CHAIN_WALK_DEPTH = 16

    async def _detect_cycle(self, *, target_agent_id: UUID):
        """Return the ancestor run id where the target agent first
        appears in the chain, or None if no cycle.

        Walk = follow agent_runs.parent_run_id starting from
        ``self.parent_run_id`` toward the root. At each hop, if
        ``agent_id == target_agent_id`` we've found a cycle (target
        already running upstream). Caller's own agent is also checked
        — caller_agent_id == target_agent_id is filtered earlier as
        self-delegate, but a delegation that would re-enter ANY agent
        already on the chain is a cycle.
        """
        if self.parent_run_id is None:
            return None

        try:
            from app.db.supabase_client import get_async_supabase_admin

            client = await get_async_supabase_admin()
        except Exception as err:
            logger.warning(f"[delegate] cycle-walk: admin client unavailable ({err})")
            return None

        current = self.parent_run_id
        for _ in range(self.MAX_CHAIN_WALK_DEPTH):
            if current is None:
                return None
            try:
                row = (
                    await client.table("agent_runs")
                    .select("id,agent_id,parent_run_id")
                    .eq("id", str(current))
                    .maybe_single()
                    .execute()
                )
            except Exception as err:
                # Best-effort: if the lookup fails, fall through and
                # let the rest of the dispatch continue. The depth cap
                # still protects against runaway recursion.
                logger.warning(f"[delegate] cycle-walk lookup failed at {current}: {err}")
                return None

            data = row.data if row and row.data else None
            if not data:
                return None

            if str(data.get("agent_id")) == str(target_agent_id):
                return data["id"]

            parent = data.get("parent_run_id")
            current = UUID(parent) if parent else None

        # Walked the cap without resolution — treat as cycle to be safe.
        logger.warning(
            f"[delegate] cycle-walk hit MAX_CHAIN_WALK_DEPTH "
            f"({self.MAX_CHAIN_WALK_DEPTH}) — refusing dispatch"
        )
        return self.parent_run_id
