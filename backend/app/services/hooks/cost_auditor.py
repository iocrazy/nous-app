"""CostAuditor — PostToolUse hook that writes one row per tool call to
``agent_run_events`` (created in migration 155).

Per-iteration row captures token deltas, tool name, hook decisions snapshot,
and model used. Future Usage page + tree-aware BudgetGuard (M2) read this
table.

Design notes:
- Token/cost deltas are computed against the previous-iteration snapshot
  the hook itself maintains in a small per-run cache. RunRecorder tracks
  cumulative totals; CostAuditor cares about per-iteration deltas.
- DB write failure is non-fatal — log + return continue. Telemetry must
  never break the run (same contract as RunRecorder).
- Priority 70 — runs after BudgetGuard (20) and any future SCAN/Approval
  hooks, so the row reflects post-decision state.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

from app.services.hooks import HookContext, HookResult

logger = logging.getLogger(__name__)


_TOOL_ARGS_SUMMARY_MAX_LEN = 200


@dataclass
class CostAuditorHook:
    """Per-iteration cost-delta writer for agent_run_events.

    State design (adversarial-review fix): the previous-iteration snapshot
    is keyed by ``run_id`` in a dict — NOT plain instance fields. A
    single CostAuditorHook instance can be registered once at startup
    AND safely service many concurrent runs, because each run has its
    own UUID and gets its own delta-tracking entry.

    The per-run dict is bounded: entries are evicted when the iteration
    counter rolls back to 1 (new run starting on the same key — defensive,
    shouldn't happen since UUIDs are unique) or when the dict exceeds
    ``_MAX_TRACKED_RUNS`` entries (LRU drop).

    Without this design, two concurrent users sharing a uvicorn worker
    would see deltas computed against each other's accumulated counters
    — random negative-clamped-to-0 cost rows. Bad data, hard to debug.
    """

    name: str = "cost_auditor"
    priority: int = 70

    _last: dict[str, dict[str, float]] = field(default_factory=dict, init=False)

    _MAX_TRACKED_RUNS: int = field(default=1024, init=False, repr=False)

    async def __call__(
        self, ctx: HookContext, tool_result: dict[str, Any]
    ) -> HookResult:
        run_key = str(ctx.run_id)
        prev = self._last.get(run_key)

        if prev is None or ctx.iteration == 1:
            # New run, or first iteration — deltas are the full amount.
            prompt_delta = ctx.accumulated_prompt_tokens
            completion_delta = ctx.accumulated_completion_tokens
            cost_delta = ctx.accumulated_cost_cents
        else:
            prompt_delta = max(
                0, ctx.accumulated_prompt_tokens - int(prev["prompt"])
            )
            completion_delta = max(
                0, ctx.accumulated_completion_tokens - int(prev["completion"])
            )
            cost_delta = max(0.0, ctx.accumulated_cost_cents - prev["cost"])

        self._last[run_key] = {
            "prompt": ctx.accumulated_prompt_tokens,
            "completion": ctx.accumulated_completion_tokens,
            "cost": ctx.accumulated_cost_cents,
        }
        # Bounded LRU-ish eviction — drop oldest entry if we get too big.
        if len(self._last) > self._MAX_TRACKED_RUNS:
            oldest_key = next(iter(self._last))
            self._last.pop(oldest_key, None)

        await self._write_event(
            run_id=ctx.run_id,
            iteration=ctx.iteration,
            tool_name=ctx.tool_name,
            tool_args_summary=_summarise_args(ctx.tool_args),
            prompt_tokens_delta=prompt_delta,
            completion_tokens_delta=completion_delta,
            cost_cents_delta=cost_delta,
            tool_result=tool_result,
        )

        return HookResult(decision="continue")

    async def _write_event(
        self,
        *,
        run_id: UUID,
        iteration: int,
        tool_name: str,
        tool_args_summary: str,
        prompt_tokens_delta: int,
        completion_tokens_delta: int,
        cost_cents_delta: float,
        tool_result: dict[str, Any],
    ) -> None:
        """Insert a single agent_run_events row. Failures are logged + swallowed."""
        # Defer import so test paths that don't init Supabase still work.
        try:
            from app.db import get_async_supabase_admin
        except ImportError:
            logger.warning(
                "[CostAuditor] supabase admin unavailable; skipping event write"
            )
            return

        # Skip the all-zero placeholder run_id used in test paths without a
        # real RunRecorder. Production paths always pass a real UUID.
        if run_id == UUID(int=0):
            logger.debug("[CostAuditor] sentinel run_id, skipping DB write")
            return

        payload = {
            "run_id": str(run_id),
            "iteration": iteration,
            "tool_name": tool_name,
            "tool_args_summary": tool_args_summary,
            "prompt_tokens_delta": prompt_tokens_delta,
            "completion_tokens_delta": completion_tokens_delta,
            "cost_cents_delta": cost_cents_delta,
            "metadata_json": {
                "tool_result_keys": sorted(list(tool_result.keys())),
            },
        }

        try:
            client = await get_async_supabase_admin()
            await client.table("agent_run_events").insert(payload).execute()
        except Exception:  # noqa: BLE001
            logger.exception(
                "[CostAuditor] insert failed for run %s iter %d; run continues",
                run_id,
                iteration,
            )


def _summarise_args(args: dict[str, Any]) -> str:
    """Render a short, log-safe summary of tool args.

    Truncates to ``_TOOL_ARGS_SUMMARY_MAX_LEN`` chars. Full args land in
    ``agent_runs.metadata_json`` if needed for forensics.
    """
    if not args:
        return ""
    parts = []
    for key, value in args.items():
        rendered = str(value) if not isinstance(value, str) else value
        if len(rendered) > 60:
            rendered = rendered[:57] + "..."
        parts.append(f"{key}={rendered}")
    summary = " ".join(parts)
    if len(summary) > _TOOL_ARGS_SUMMARY_MAX_LEN:
        summary = summary[: _TOOL_ARGS_SUMMARY_MAX_LEN - 3] + "..."
    return summary


__all__ = ["CostAuditorHook"]
