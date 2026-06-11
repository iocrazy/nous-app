"""CapabilityGate — PreToolUse hook enforcing ai_agents.capability_profile.

Phase 4.5 Week 1 (canvas plan §4.5): per-agent tool/skill access control.
Registered at priority 25 — after RateLimit's future slot (15) and
BudgetGuard (20), before routing-class hooks (30+).

Profile keys (all optional; empty/missing = that gate is open):

    tool_blacklist          list[str]  tools this agent may never call
    allowed_skills          list[str]  when non-empty, Skill() is limited
                                       to these slugs
    max_parallel_delegates  int        0 blocks Delegate entirely (the >0
                                       concurrency cap lands with M2
                                       concurrent delegate dispatch)
    context_budget_tokens   int        abort tool calls once accumulated
                                       prompt+completion tokens exceed it

Contract (same as every hook): never raises — a malformed profile fails
open with a warning, because a config typo must not brick the agent.
Abort reasons are user-facing (they surface in the chat as the abort
message), so they name the blocked thing explicitly.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.infra.hooks import HookContext, HookResult

logger = logging.getLogger(__name__)

# Tool name the skill loop dispatches on (see SkillToolService) and the
# delegation tool (see DelegateToolService).
_SKILL_TOOL = "Skill"
_DELEGATE_TOOL = "Delegate"


class CapabilityGateHook:
    """PreToolUse hook: blocks tool calls outside the agent's profile."""

    def __init__(self, profile: dict[str, Any] | None):
        self.profile: dict[str, Any] = profile if isinstance(profile, dict) else {}

    async def __call__(self, ctx: HookContext) -> HookResult:
        try:
            return self._evaluate(ctx)
        except Exception:  # noqa: BLE001 — fail open, never brick the run
            logger.exception(
                "[capability_gate] profile evaluation failed for agent %s; "
                "failing open",
                ctx.agent_slug,
            )
            return HookResult(decision="continue")

    def _evaluate(self, ctx: HookContext) -> HookResult:
        blacklist = self.profile.get("tool_blacklist") or []
        if isinstance(blacklist, list) and ctx.tool_name in blacklist:
            return HookResult(
                decision="abort",
                abort_reason=(
                    f"Tool '{ctx.tool_name}' is not available to agent "
                    f"'{ctx.agent_slug}' (capability profile)."
                ),
            )

        allowed_skills = self.profile.get("allowed_skills")
        if (
            ctx.tool_name == _SKILL_TOOL
            and isinstance(allowed_skills, list)
            and allowed_skills
        ):
            skill = str(ctx.tool_args.get("skill") or "")
            if skill not in allowed_skills:
                return HookResult(
                    decision="abort",
                    abort_reason=(
                        f"Skill '{skill}' is not in agent "
                        f"'{ctx.agent_slug}'s allowed skills."
                    ),
                )

        if ctx.tool_name == _DELEGATE_TOOL:
            max_delegates = self.profile.get("max_parallel_delegates")
            if isinstance(max_delegates, int) and max_delegates == 0:
                return HookResult(
                    decision="abort",
                    abort_reason=(
                        f"Agent '{ctx.agent_slug}' is not allowed to "
                        "delegate (capability profile)."
                    ),
                )

        budget = self.profile.get("context_budget_tokens")
        if isinstance(budget, int) and budget > 0:
            used = ctx.accumulated_prompt_tokens + ctx.accumulated_completion_tokens
            if used > budget:
                return HookResult(
                    decision="abort",
                    abort_reason=(
                        f"Agent '{ctx.agent_slug}' exceeded its context "
                        f"budget ({used} > {budget} tokens)."
                    ),
                )

        return HookResult(decision="continue")


__all__ = ["CapabilityGateHook"]
