"""Fail-closed parser for an agent's Team Chat capabilities (CHAT-PERM-07).

Chat permissions are stored inside ``ai_agents.capability_profile`` JSONB under
the ``chat`` key. Absent or wrong-typed config means the agent CANNOT chat: only
a literal JSON ``true`` grants a boolean capability, and ``allowed_team_ids``
defaults to an empty whitelist meaning "any team it is explicitly added to".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChatCaps:
    enabled: bool = False
    read_team_resources: bool = False
    auto_broadcast: bool = False
    allowed_team_ids: tuple[int, ...] = ()

    def allows_team(self, team_id: int | None) -> bool:
        """True if this agent may operate in a group belonging to ``team_id``.

        Requires ``enabled``. An empty ``allowed_team_ids`` means any team;
        a non-empty whitelist requires membership.
        """
        if not self.enabled:
            return False
        if not self.allowed_team_ids:
            return True
        return team_id is not None and int(team_id) in self.allowed_team_ids


def _as_bool(value: Any) -> bool:
    # Fail-closed: only a literal JSON boolean true grants the capability.
    return value is True


def _as_team_ids(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def agent_chat_caps(agent: dict[str, Any] | None) -> ChatCaps:
    """Parse chat capabilities from an agent row dict. Never raises."""
    if not agent:
        return ChatCaps()
    profile = agent.get("capability_profile")
    if not isinstance(profile, dict):
        return ChatCaps()
    chat = profile.get("chat")
    if not isinstance(chat, dict):
        return ChatCaps()
    return ChatCaps(
        enabled=_as_bool(chat.get("enabled")),
        read_team_resources=_as_bool(chat.get("read_team_resources")),
        auto_broadcast=_as_bool(chat.get("auto_broadcast")),
        allowed_team_ids=_as_team_ids(chat.get("allowed_team_ids")),
    )
