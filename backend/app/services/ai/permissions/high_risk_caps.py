"""Fail-closed parser for an agent's HIGH-RISK capabilities (A1, screenwriting
agent layer — docs/superpowers/plans/2026-08-04-episode-workflow-and-agent-layer.md).

Stored inside ``ai_agents.capability_profile`` JSONB under the ``capabilities``
key — a sibling of ``chat`` (see ``agent_chat_caps.py``, the house pattern this
module follows) and of the low-risk tuning keys (``tool_blacklist`` /
``allowed_skills`` / ``max_parallel_delegates`` / ``context_budget_tokens``),
which this module does NOT touch.

Fail-closed discipline (same posture as ``agent_chat_caps.py``, the OPPOSITE
of ``capability_gate.py``'s deliberately fail-open low-risk gate — see the
docstring there and in ``high_risk_capability_gate.py`` for why the two
postures differ and must stay that way):

- Absent/malformed profile, absent ``capabilities`` key, or a wrong-typed
  value anywhere inside it → every field defaults to its most restrictive
  value. Nothing is granted unless explicitly, correctly declared.
- Booleans only grant on a literal JSON ``true`` (mirrors ``_as_bool`` in
  ``agent_chat_caps.py``) — ``1``, ``"true"``, ``"yes"`` etc. do NOT grant.
- ``write_level`` only grants on one of the exact literal strings
  ``"read"`` / ``"propose"`` / ``"write"``; anything else (including no
  value at all) is ``"none"`` — an agent with no write_level configured
  cannot even call a "read" tier tool under this gate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# Ordinal ranking for write-grading. "none" is the fail-closed default: an
# agent with no (or malformed) write_level cannot satisfy ANY tier check,
# including "read" — read-only screenwriting tools (ListScenes/ReadScene,
# A4) must still be explicitly granted, they are not a free baseline.
_WRITE_LEVEL_ORDER: dict[str, int] = {"none": 0, "read": 1, "propose": 2, "write": 3}

# Spec §2 / §5.1: "screenplay-ish tools default to `propose`" when a tool
# doesn't declare its own required tier. This is a default for TOOL
# metadata (what a tool demands), not for agent grants (what an agent has) —
# the two must not be confused. Not yet consumed anywhere in A1 since no
# screenwriting tools exist yet; A4 wires this into ToolRequirement entries
# in high_risk_capability_gate.py.
DEFAULT_SCREENPLAY_WRITE_LEVEL = "propose"

# Media generation is granted (image/video, separately) AND capped per turn.
# A grant with no explicit numeric cap does NOT mean "unlimited" — that would
# defeat the fail-closed premise of this whole module — it falls back to
# this conservative constant instead. Per-turn because HighRiskCapabilityGateHook
# is instantiated fresh per chat turn (see ai_library_chat_wiring.py's
# "HookRegistry per-turn" design note), which is what "单次上限" refers to.
_DEFAULT_MEDIA_CAP_PER_TURN = 4


def _as_bool(value: Any) -> bool:
    # Fail-closed: only a literal JSON boolean true grants the capability.
    return value is True


def _as_write_level(value: Any) -> str:
    if isinstance(value, str) and value in _WRITE_LEVEL_ORDER:
        return value
    return "none"


def _as_media_cap(value: Any) -> int:
    # bool is an int subclass in Python — explicitly reject it so
    # ``max_calls_per_turn: true`` doesn't silently become 1.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return _DEFAULT_MEDIA_CAP_PER_TURN
    return value


@dataclass(frozen=True)
class MediaCaps:
    image: bool = False
    video: bool = False
    max_calls_per_turn: int = _DEFAULT_MEDIA_CAP_PER_TURN

    def allows(self, kind: str) -> bool:
        if kind == "image":
            return self.image is True
        if kind == "video":
            return self.video is True
        return False


def _as_media(value: Any) -> MediaCaps:
    if not isinstance(value, dict):
        return MediaCaps()
    return MediaCaps(
        image=_as_bool(value.get("image")),
        video=_as_bool(value.get("video")),
        max_calls_per_turn=_as_media_cap(value.get("max_calls_per_turn")),
    )


@dataclass(frozen=True)
class HighRiskCaps:
    write_level: str = "none"
    delete: bool = False
    media: MediaCaps = field(default_factory=MediaCaps)
    cross_episode_read: bool = False
    external_publish: bool = False

    def meets_write_level(self, required: str) -> bool:
        """True if this agent's granted tier is >= ``required``.

        An unrecognized ``required`` value fails closed (treated as the
        highest tier, "write") rather than silently passing — a typo in a
        future tool's declared requirement must not become an open door.
        """
        granted = _WRITE_LEVEL_ORDER.get(self.write_level, 0)
        needed = _WRITE_LEVEL_ORDER.get(required, _WRITE_LEVEL_ORDER["write"])
        return granted >= needed

    def media_allowed(self, kind: str) -> bool:
        return self.media.allows(kind)


def high_risk_caps(agent: dict[str, Any] | None) -> HighRiskCaps:
    """Parse high-risk capabilities from an agent row dict. Never raises."""
    if not agent:
        return HighRiskCaps()
    profile = agent.get("capability_profile")
    if not isinstance(profile, dict):
        return HighRiskCaps()
    caps = profile.get("capabilities")
    if not isinstance(caps, dict):
        return HighRiskCaps()
    return HighRiskCaps(
        write_level=_as_write_level(caps.get("write_level")),
        delete=_as_bool(caps.get("delete")),
        media=_as_media(caps.get("media")),
        cross_episode_read=_as_bool(caps.get("cross_episode_read")),
        external_publish=_as_bool(caps.get("external_publish")),
    )


def media_kill_switch_engaged() -> bool:
    """Install-wide OFF-only override for agent media generation.

    ``FEATURE_AGENT_MEDIA_TOOLS`` used to be the sole gate for GenerateImage/
    GenerateVideo (all-or-nothing across every agent). A1 migrates
    authorization onto the per-agent ``capabilities.media`` grant above —
    but the env var is kept as a kill switch that can only SUBTRACT, never
    grant: an explicit falsy value force-disables media tools install-wide
    (e.g. an incident response lever), while unset or truthy values are a
    no-op and leave the decision entirely to each agent's capability_profile.
    This is the inverse of the old semantics (where unset meant "disabled"),
    so do not resurrect the old truthy-check helper — see
    ai_library_chat_service.py's media tool registration block and
    tests/test_chat_media_tools_injection.py.
    """
    raw = os.environ.get("FEATURE_AGENT_MEDIA_TOOLS", "").strip().lower()
    return raw in ("0", "false", "no", "off")


__all__ = [
    "DEFAULT_SCREENPLAY_WRITE_LEVEL",
    "HighRiskCaps",
    "MediaCaps",
    "high_risk_caps",
    "media_kill_switch_engaged",
]
