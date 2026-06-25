"""Memory provider abstraction (Phase 1 — Nous/Hermes pluggable-provider model).

mediahub runs two memory *slots* simultaneously:

  L2 — user model (today Honcho): "who is this user / what do they prefer".
  L3 — knowledge graph (today Graphiti): "what facts/entities were discussed".

Each slot's active provider is chosen from system_settings; a provider is a
thin adapter over the underlying service. Unlike Hermes (one external provider
at a time) the two slots are independent and both active.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class MemoryLayer(str, Enum):
    L2 = "l2"
    L3 = "l3"


@dataclass(frozen=True)
class MemoryTurn:
    """One chat exchange to persist, in provider-neutral form. Each adapter
    derives its own provider-specific shape (Honcho workspace / Graphiti
    group_id, episode body, etc.) from these raw fields."""

    user_id: str
    agent_id: str
    session_id: str
    run_id: Optional[str]
    iteration: int
    user_msgs: List[str]
    asst_msgs: List[str]


class MemoryProvider(ABC):
    """Adapter over one memory backend for one slot."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short id, e.g. 'honcho', 'graphiti'."""

    @property
    @abstractmethod
    def layer(self) -> MemoryLayer:
        """Which slot this provider serves."""

    @abstractmethod
    def enabled(self) -> bool:
        """Cheap, sync global-flag gate. Checked BEFORE any settings read so a
        disabled deployment never pays for one."""

    @abstractmethod
    async def is_operative(self) -> bool:
        """enabled AND reachable/configured (read path uses this)."""

    @abstractmethod
    async def record_turn(self, turn: MemoryTurn) -> bool:
        """Persist one exchange. Returns True on success, False on any failure
        or when not operative. Never raises."""

    @abstractmethod
    async def get_context(
        self,
        *,
        user_id: str,
        query: str = "",
        workspace_id: Optional[str] = None,
        group_ids: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Return a memory-context block to inject, or None."""

    @abstractmethod
    async def reload(self) -> None:
        """Drop cached client/config so the NEXT call re-reads current config.
        For in-process providers (Graphiti, future Mem0) this is how an admin
        config change takes effect without a process restart. Never raises."""

    @abstractmethod
    async def health(self) -> bool:
        """Liveness for the admin control plane's green/red dot. Phase 1 returns
        is_operative(); later phases may do a real probe. Never raises."""


__all__ = ["MemoryLayer", "MemoryTurn", "MemoryProvider"]
