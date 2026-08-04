"""HighRiskCapabilityGateHook — fail-closed PreToolUse gate for destructive /
costly agent capabilities (A1, screenwriting agent layer — see
docs/superpowers/plans/2026-08-04-episode-workflow-and-agent-layer.md and
docs/superpowers/specs/2026-08-04-screenwriting-agent-layer-design.md §2).

This COEXISTS with ``capability_gate.py``'s ``CapabilityGateHook`` — it does
not replace it, and the two must not be "unified" into one posture. They
gate different risk classes on purpose:

- ``CapabilityGateHook`` (capability_gate.py): low-risk tuning knobs
  (``tool_blacklist`` / ``allowed_skills`` / ``max_parallel_delegates`` /
  ``context_budget_tokens``). Fail-OPEN by deliberate design — its own
  docstring: "a config typo must not brick the agent". Blacklist semantics:
  not listed = allowed.
- ``HighRiskCapabilityGateHook`` (this file): write-grading (read/propose/
  write), delete, media generation, cross-episode read, external publish.
  Fail-CLOSED: an unparseable profile or an ungranted capability is DENY.
  Whitelist semantics: not explicitly granted = blocked. Here, silently
  letting a destructive/costly action through on a config typo is the worse
  failure — the exact inversion of the low-risk gate's tradeoff.

Registered with ``fail_closed=True`` in ``ai_library_chat_wiring.py`` — if
this hook's own evaluation raises unexpectedly, AgentRunner's
``_safe_invoke_pre`` turns that into an ``abort`` rather than letting the
tool call through (see agent_runner.py's fail_closed contract). The
capability parsing itself (``high_risk_caps.py``) is also written to never
raise, mirroring ``agent_chat_caps.py``'s discipline — the registration-level
fail_closed is belt-and-suspenders, not the primary mechanism.

The decision point is here, in the tool executor's PreToolUse chain — NOT in
the system prompt or tool description — because a caller (including an
injected instruction inside screenplay content the agent read) cannot craft
a prompt that skips a hook registered on the runner. This includes the
``FEATURE_AGENT_MEDIA_TOOLS`` kill switch: this hook calls
``media_kill_switch_engaged()`` itself for every media call, so the
guarantee doesn't depend on ``ai_library_chat_service.py``'s tool-spec
registration also remembering to check it (that registration-time check is
only a UX nicety — don't advertise a tool the agent can't use).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.services.ai.permissions.high_risk_caps import (
    HighRiskCaps,
    high_risk_caps,
    media_kill_switch_engaged,
)
from app.services.infra.hooks import HookContext, HookResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolRequirement:
    """Declares what a tool needs from :class:`HighRiskCaps` to be allowed.

    Every field is an independent gate; a tool only needs to satisfy the
    ones it sets. A tool absent from ``TOOL_REQUIREMENTS`` has no high-risk
    requirement under THIS gate (it may still be constrained by
    ``CapabilityGateHook``'s blacklist, or — once A2 lands — by scope).
    """

    write_level: Optional[str] = None
    delete: bool = False
    media: Optional[str] = None  # "image" | "video"
    cross_episode_read: bool = False
    external_publish: bool = False


# Populate as later stages wire in more tools:
#   A4 screenwriting tools (spec §5.1): ListScenes/ReadScene -> write_level
#   "read"; CreateShot/UpdateShot -> "write"; ProposeEdit -> "propose".
# GenerateImage/GenerateVideo are the only entries A1 needs — they are the
# only high-risk tools that exist today (migrated off the
# FEATURE_AGENT_MEDIA_TOOLS global flag, see high_risk_caps.media_kill_switch_engaged).
TOOL_REQUIREMENTS: dict[str, ToolRequirement] = {
    "GenerateImage": ToolRequirement(media="image"),
    "GenerateVideo": ToolRequirement(media="video"),
}


class HighRiskCapabilityGateHook:
    """PreToolUse hook: default-deny gate for high-risk capabilities.

    One instance is constructed per chat turn (see
    ``ai_library_chat_wiring.build_agent_runner_stack``'s "HookRegistry
    per-turn" design note), so ``_media_calls_this_turn`` is naturally
    scoped to a single turn — matching the spec's "单次上限" (per-turn cap).
    """

    def __init__(self, agent: dict[str, Any] | None):
        self.caps: HighRiskCaps = high_risk_caps(agent)
        self._media_calls_this_turn = 0

    async def __call__(self, ctx: HookContext) -> HookResult:
        requirement = TOOL_REQUIREMENTS.get(ctx.tool_name)
        if requirement is None:
            return HookResult(decision="continue")

        if requirement.write_level is not None and not self.caps.meets_write_level(
            requirement.write_level
        ):
            return self._deny(
                ctx,
                f"requires '{requirement.write_level}' capability, agent "
                f"'{ctx.agent_slug}' has '{self.caps.write_level}'",
            )

        if requirement.delete and not self.caps.delete:
            return self._deny(ctx, "delete capability not granted")

        if requirement.media is not None:
            # Checked HERE, not only at tool-registration time in
            # ai_library_chat_service.py — that registration-time check is a
            # UX nicety (don't advertise a tool the agent can't use), NOT the
            # enforcement. If a GenerateImage/GenerateVideo call reaches this
            # hook by any other path (cached system prompt from a prior turn,
            # hallucination, a future caller that wires the handler
            # unconditionally), the kill switch must still bite here.
            if media_kill_switch_engaged():
                return self._deny(
                    ctx,
                    "media generation is disabled install-wide "
                    "(FEATURE_AGENT_MEDIA_TOOLS kill switch engaged)",
                )
            if not self.caps.media_allowed(requirement.media):
                return self._deny(ctx, f"'{requirement.media}' generation not granted")
            cap = self.caps.media.max_calls_per_turn
            if self._media_calls_this_turn >= cap:
                return self._deny(
                    ctx, f"media generation cap for this turn exceeded ({cap})"
                )
            self._media_calls_this_turn += 1

        if requirement.cross_episode_read and not self.caps.cross_episode_read:
            return self._deny(ctx, "cross-episode read not granted")

        if requirement.external_publish and not self.caps.external_publish:
            return self._deny(ctx, "external publish/distribution not granted")

        return HookResult(decision="continue")

    def _deny(self, ctx: HookContext, reason: str) -> HookResult:
        logger.warning(
            "[high_risk_capability_gate] denied agent=%s tool=%s: %s",
            ctx.agent_slug,
            ctx.tool_name,
            reason,
        )
        return HookResult(
            decision="abort",
            abort_reason=(
                f"Tool '{ctx.tool_name}' blocked for agent '{ctx.agent_slug}': "
                f"{reason}."
            ),
        )


__all__ = ["HighRiskCapabilityGateHook", "ToolRequirement", "TOOL_REQUIREMENTS"]
