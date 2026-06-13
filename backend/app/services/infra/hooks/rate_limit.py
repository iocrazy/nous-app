"""RateLimit — PreToolUse hook bounding tool-call rate via a Redis bucket.

Phase 4.5 Week 2 (canvas plan §4.5): the real-time Layer-2 budget gate.
BudgetGuard (priority 20) caps accumulated per-run COST, but cost lags —
a runaway tool loop can fire dozens of calls before the spend registers.
This hook caps the CALL RATE per (user, agent) per minute, in Redis, so
the cap holds across concurrent runs and uvicorn workers.

Registered at priority 15 — before BudgetGuard (20) and CapabilityGate
(25): the cheapest check that can stop a runaway loop goes first.

Limit resolution (wiring, not the hook): the agent's
``capability_profile.rate_limit_tool_calls_per_min`` overrides the
``AGENT_TOOL_CALLS_PER_MIN`` env default; 0/absent disables the hook
entirely (it is then never registered — zero overhead).

Algorithm: fixed one-minute windows via ``INCR`` + ``EXPIRE`` (the
classic cheap bucket — two round trips worst case, one after the first
call of a window). Window boundaries are epoch-minute aligned, so the
worst-case burst is 2× the limit straddling a boundary; acceptable for
a runaway-loop brake (this is not billing enforcement — BudgetGuard is).

Contract (same as every hook): Redis being down fails OPEN with a
warning — a cache outage must not take chat down with it.
"""

from __future__ import annotations

import logging
import time

from app.services.infra.hooks import HookContext, HookResult

logger = logging.getLogger(__name__)

# Keep the window TTL comfortably above the window length so a clock-skewed
# EXPIRE never lets a counter linger forever, while dead windows still vanish.
_WINDOW_TTL_S = 120


class RateLimitHook:
    """PreToolUse hook: per-(user, agent) tool-calls-per-minute brake."""

    def __init__(self, *, limit_per_min: int):
        self.limit_per_min = int(limit_per_min)

    async def __call__(self, ctx: HookContext) -> HookResult:
        if self.limit_per_min <= 0:
            return HookResult(decision="continue")
        try:
            from app.core.redis import get_async_redis

            redis = await get_async_redis()
            window = int(time.time() // 60)
            key = f"rate:tools:{ctx.user_id}:{ctx.agent_id}:{window}"
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, _WINDOW_TTL_S)
            if count > self.limit_per_min:
                return HookResult(
                    decision="abort",
                    abort_reason=(
                        f"Agent '{ctx.agent_slug}' hit its tool-call rate "
                        f"limit ({self.limit_per_min}/min). Wait a moment "
                        "and try again."
                    ),
                )
            return HookResult(decision="continue")
        except Exception:  # noqa: BLE001 — fail open, never brick the run
            logger.warning(
                "[rate_limit] Redis check failed for agent %s; failing open",
                ctx.agent_slug,
                exc_info=True,
            )
            return HookResult(decision="continue")


__all__ = ["RateLimitHook"]
