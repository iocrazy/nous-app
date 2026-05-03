"""Tool result cache — short-window memoization for idempotent tools.

Phase K (K1). When an agent calls the same (tool, args) multiple times
in a row (research workflows, retry-after-clarification), every call
re-executes — burns latency, money, rate-limit budget. The loop_guard
catches pathological repeats; this primitive prevents the BENIGN
repeats from costing.

Two participation requirements:
  1. Skill must opt in via ``idempotent: true`` in its skill_files
     frontmatter (so destructive tools never accidentally cache).
  2. Cache window is short (default 60s) — long enough to span "user
     asks → agent fetches → user clarifies → agent fetches again"
     without giving stale data on cross-session calls.

Bounded by LRU + per-entry TTL. Per-process (multi-replica fan-out
via Redis is K3 territory, not here).

Usage from AgentRunner:
    cache = ToolResultCache()
    key = cache.key(tool_name, args)
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = await skill_tool.execute(args)
    if skill_is_idempotent(skill_slug):
        cache.put(key, result)
"""
from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional


DEFAULT_TTL_SECONDS = 60.0
DEFAULT_MAX_ENTRIES = 256


def _canonical_args(args: Any) -> str:
    """Stable hashable repr of args. Dicts sorted by key."""
    if isinstance(args, dict):
        # JSON-canonicalize via sorted-key serialization. Fall back to
        # repr() if a value isn't JSON-serializable.
        try:
            import json
            return json.dumps(args, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return repr(sorted(args.items()))
    return repr(args)


@dataclass
class _Entry:
    value: Any
    expires_at: float


class ToolResultCache:
    """LRU + TTL cache. Per-process; per-run instance preferred.

    The same instance can serve multiple turns of one agent run; sharing
    across agents is allowed but adds noise (different agents calling
    the same tool with same args is rare in practice).
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        self._ttl = ttl_seconds
        self._max = max_entries
        self._store: OrderedDict[str, _Entry] = OrderedDict()
        # Instrumentation
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    @staticmethod
    def key(tool_name: str, args: Any) -> str:
        """Stable cache key from (tool_name, canonical_args)."""
        if not tool_name:
            tool_name = "?"
        canon = f"{tool_name}\x00{_canonical_args(args)}"
        return hashlib.sha1(canon.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]

    def get(self, key: str, *, now: Optional[float] = None) -> Optional[Any]:
        """Returns cached value if present + unexpired; None otherwise.
        Touches LRU (refresh-on-read)."""
        n = now if now is not None else time.time()
        entry = self._store.get(key)
        if entry is None:
            self.misses += 1
            return None
        if entry.expires_at <= n:
            # Expired — evict + miss
            del self._store[key]
            self.misses += 1
            return None
        # Hit — move-to-end refreshes LRU position
        self._store.move_to_end(key)
        self.hits += 1
        return entry.value

    def put(self, key: str, value: Any, *, now: Optional[float] = None) -> None:
        """Insert / refresh an entry. Evicts oldest if over max_entries."""
        n = now if now is not None else time.time()
        # Evict expired entries opportunistically (cheap pass over old end)
        while self._store and len(self._store) >= self._max:
            evicted_key, evicted_entry = next(iter(self._store.items()))
            del self._store[evicted_key]
            self.evictions += 1
        self._store[key] = _Entry(value=value, expires_at=n + self._ttl)
        self._store.move_to_end(key)

    def invalidate(self, key: str) -> bool:
        """Remove a single entry; True if it existed."""
        return self._store.pop(key, None) is not None

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def stats(self) -> dict[str, int]:
        return {
            "size": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
        }


__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_TTL_SECONDS",
    "ToolResultCache",
]
