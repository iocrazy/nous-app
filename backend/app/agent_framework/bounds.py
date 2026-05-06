"""Bounds discovery — workers advertise what they can handle.

Sprint 5 (D10-1) primitive. When the gateway and worker run as
separate containers, the gateway shouldn't dispatch a workflow whose
``@DBOS.workflow`` decorator was never imported on any live worker —
the job sits in the PG queue forever (or until manual cleanup).

A bound is a self-description a worker publishes at startup:
  - which workflow names it can run (e.g. 'ai_transcription.run')
  - which agent slugs it has loaded (script_ai, summarize, ...)
  - which model providers/adapters it has credentials for
  - max parallel jobs per lane (from LaneQueue config)

Gateway holds a ``BoundsRegistry`` (in-process). Worker reports its
bound at startup via ``BoundsAdvertisement.to_dict()``. The registry
deduplicates by ``worker_id`` and times out stale entries (worker died
without unregistering).

This module is pure data + types. The transport (HTTP push /
WebSocket / direct DB write) is wired by the service layer in a
follow-up. For Sprint 5 we land:
  - the value object + registry primitive (this file)
  - the role primitive (role.py)
  - tests
  - main.py respects role to skip DBOS.launch()

Transport layer + actual gateway-side dispatch gating are deferred to
Sprint 5.5 once we have one shipping worker container to test against.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class BoundsAdvertisement:
    """A worker's self-description. Immutable — workers re-publish on
    capability change rather than mutating an existing bound."""

    worker_id: str
    role: str  # ProcessRole.value, kept as str so this module doesn't
    # import role (avoid circularity / let bounds be usable on its own)
    workflows: frozenset[str] = field(default_factory=frozenset)
    agents: frozenset[str] = field(default_factory=frozenset)  # slugs
    providers: frozenset[str] = field(
        default_factory=frozenset
    )  # 'qwen' / 'openai' / ...
    lane_capacity: dict[str, int] = field(
        default_factory=dict
    )  # lane name → max parallel
    version: Optional[str] = None  # commit sha or release tag
    started_at: float = field(default_factory=time.time)

    def can_handle_workflow(self, workflow_name: str) -> bool:
        return workflow_name in self.workflows

    def can_handle_agent(self, agent_slug: str) -> bool:
        return agent_slug in self.agents


@dataclass
class _StoredBound:
    bound: BoundsAdvertisement
    last_seen: float


class BoundsRegistry:
    """In-process registry of currently-known worker bounds.

    Per-process (gateway-side). Workers publish their advertisement at
    startup + periodic heartbeat. Stale entries (no heartbeat within
    ``stale_after_s``) are pruned on read so the registry doesn't hand
    out dead routes.

    Thread-safety: not synchronized. Use one registry per asyncio loop
    (typical FastAPI gateway). For multi-loop / multi-thread, wrap in a
    lock — but the typical access pattern is "register at startup,
    heartbeat every 30s, lookup on dispatch" which is naturally
    serialized in the gateway's request loop.
    """

    def __init__(self, *, stale_after_s: float = 90.0) -> None:
        self._stored: dict[str, _StoredBound] = {}
        self._stale_after = stale_after_s

    def register(self, bound: BoundsAdvertisement) -> None:
        """Insert or replace a bound by worker_id."""
        self._stored[bound.worker_id] = _StoredBound(bound=bound, last_seen=time.time())

    def heartbeat(self, worker_id: str) -> bool:
        """Refresh last_seen for a known worker. Returns False if the
        worker_id isn't in the registry (caller should re-register)."""
        stored = self._stored.get(worker_id)
        if stored is None:
            return False
        stored.last_seen = time.time()
        return True

    def unregister(self, worker_id: str) -> None:
        """Worker is shutting down cleanly — remove immediately."""
        self._stored.pop(worker_id, None)

    def _prune(self, *, now: Optional[float] = None) -> None:
        """Drop bounds with no heartbeat within stale_after_s."""
        cutoff = (now or time.time()) - self._stale_after
        dead = [
            wid for wid, stored in self._stored.items() if stored.last_seen < cutoff
        ]
        for wid in dead:
            del self._stored[wid]

    def live_bounds(self, *, now: Optional[float] = None) -> list[BoundsAdvertisement]:
        """All currently-live bounds (post-prune)."""
        self._prune(now=now)
        return [s.bound for s in self._stored.values()]

    def workers_for_workflow(
        self, workflow_name: str, *, now: Optional[float] = None
    ) -> list[BoundsAdvertisement]:
        """Live workers that can run the given workflow name."""
        return [
            b for b in self.live_bounds(now=now) if b.can_handle_workflow(workflow_name)
        ]

    def workers_for_agent(
        self, agent_slug: str, *, now: Optional[float] = None
    ) -> list[BoundsAdvertisement]:
        return [b for b in self.live_bounds(now=now) if b.can_handle_agent(agent_slug)]

    def can_dispatch_workflow(
        self, workflow_name: str, *, now: Optional[float] = None
    ) -> bool:
        """True if at least one live worker can handle this workflow.

        Gateway uses this as a pre-flight gate: if no worker advertises
        the capability, fail dispatch fast with a clear error rather
        than letting the job sit in the queue forever.
        """
        return bool(self.workers_for_workflow(workflow_name, now=now))

    def snapshot(self, *, now: Optional[float] = None) -> dict[str, dict]:
        """Admin / debug view: all live bounds, JSON-friendly."""
        self._prune(now=now)
        out: dict[str, dict] = {}
        for wid, stored in self._stored.items():
            b = stored.bound
            out[wid] = {
                "role": b.role,
                "version": b.version,
                "workflows": sorted(b.workflows),
                "agents": sorted(b.agents),
                "providers": sorted(b.providers),
                "lane_capacity": dict(b.lane_capacity),
                "started_at": b.started_at,
                "last_seen_age_s": round((now or time.time()) - stored.last_seen, 1),
            }
        return out


__all__ = ["BoundsAdvertisement", "BoundsRegistry"]
