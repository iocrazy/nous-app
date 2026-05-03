"""In-process telemetry counters for agent harness primitives.

Wave I (I3). Cheap monotonic counters for the new harness components
so admin / debug / health endpoints can answer:

  - How many compactions ran this hour?
  - How many tool-call loops did the loop_guard catch?
  - What's the commitment harvest hit rate?
  - How often does session_memory updater actually fire?

Lightweight by design — no Prometheus client dep, no histograms, no
labels with cardinality risk. Just per-process atomic counters that
admin endpoints read via .snapshot() and emit as JSON. A future
Prometheus exporter can wrap this without changing call sites.

Thread/asyncio-safety: increments use a simple `+=`. Python's GIL
makes int += int atomic for CPython; the values are eventually
consistent across asyncio tasks. We don't care about lost updates
under contention — the counters are advisory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Canonical counter names. Frozen to keep call sites consistent + admin
# UI labels stable. Add new ones by appending here.
COUNTER_NAMES: tuple[str, ...] = (
    # Compaction
    "compaction_triggered",
    "compaction_used_session_memory",
    "compaction_used_fresh_summarizer",
    "compaction_pre_pass_pruned",
    # Loop guard
    "loop_guard_observed",
    "loop_guard_tripped",
    # Output budget
    "output_budget_tightened",
    # Commitments
    "commitment_harvest_skipped_no_cues",
    "commitment_harvest_extracted",
    "commitment_harvest_persisted",
    "commitment_sweeper_fired",
    "commitment_sweeper_expired",
    # Session memory
    "session_memory_update_attempted",
    "session_memory_update_persisted",
    "session_memory_update_skipped_no_trigger",
    # Memory M2
    "memory_archived",
    "memory_consolidated",
    "memory_superseded_by_contradiction",
    "memory_active_remember_persisted",
    # Bounds dispatch gate
    "dispatch_gate_blocked",
    "dispatch_gate_passed",
    # Hooks
    "hook_aborted_run",
    # Streaming
    "streaming_started",
    "streaming_aborted_mid",
    # Link injection
    "link_injection_fetched",
    "link_injection_failed",
    # Phase L (L1): tool result cache
    "tool_cache_hit",
    # Phase L (L2): agent todo
    "agent_todo_replaced",
    "agent_todo_completed",
    # R3: commitment fuzzy time → next_session demotion
    "commitment_demoted_fuzzy_time",
    # R4: stream_turn auto-recorder use
    "stream_turn_auto_recorder",
    # G1+G5: per-user MCP registry built at chat start
    "chat_mcp_registry_built",
    # Q5+G3: outbound MCP tool injection + dispatch
    "mcp_tools_injected",
    "mcp_tool_call",
    "mcp_tool_call_error",
    "mcp_tool_call_transport_error",
)


@dataclass
class AgentMetrics:
    """In-process counter store. Per-process; multi-replica aggregation
    deferred (would need redis HINCRBY or a real metrics backend)."""

    counters: dict[str, int] = field(default_factory=lambda: {n: 0 for n in COUNTER_NAMES})

    def inc(self, name: str, *, by: int = 1) -> None:
        """Increment ``name`` by ``by`` (default 1).

        Unknown names are accepted but logged-once via __unknown_warned
        so a typo'd counter doesn't silently drop forever — surfaced in
        snapshot() under '_unknown' key.
        """
        if not name:
            return
        if name not in self.counters:
            # Store anyway to surface in snapshot — typo gets caught
            # by the next admin glance instead of silently lost.
            self.counters[name] = 0
        self.counters[name] += int(by)

    def get(self, name: str) -> int:
        return int(self.counters.get(name, 0))

    def snapshot(self) -> dict[str, Any]:
        """JSON-friendly view: all canonical counters + any unknown ones
        collected since process start. Sorted keys for stable display."""
        canonical = {n: self.counters.get(n, 0) for n in COUNTER_NAMES}
        unknown = {
            k: v for k, v in self.counters.items() if k not in COUNTER_NAMES
        }
        out: dict[str, Any] = dict(sorted(canonical.items()))
        if unknown:
            out["_unknown"] = dict(sorted(unknown.items()))
        return out

    def reset(self) -> None:
        """Wipe all counters. Mostly for tests."""
        self.counters = {n: 0 for n in COUNTER_NAMES}


__all__ = ["AgentMetrics", "COUNTER_NAMES"]
