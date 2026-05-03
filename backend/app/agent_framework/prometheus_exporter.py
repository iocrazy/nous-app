"""Prometheus text-format exporter for AgentMetrics.

Phase K (K4). Wraps the in-process AgentMetrics counters in
Prometheus expfmt 0.0.4 (text format) so a Prometheus / VictoriaMetrics
scrape job can pull them.

Why no prometheus_client lib: the dep is heavy + we already have all
state in AgentMetrics. The text format is dead simple — 5 lines per
counter. Wrapping our store + emitting that fits in <100 LOC.

Output (per counter):
    # HELP agent_<name> <description>
    # TYPE agent_<name> counter
    agent_<name>{namespace="harness"} <value>

The namespace label keeps these distinguishable from other counters
that may scrape the same endpoint.
"""
from __future__ import annotations

from app.agent_framework.telemetry import AgentMetrics, COUNTER_NAMES


# Help text per counter — ops-readable. Defaults to the counter name
# with underscores → spaces if no override.
_HELP: dict[str, str] = {
    "compaction_triggered": "Times compactor crossed the threshold and ran",
    "compaction_used_session_memory": "Compactions that swapped in cached session_memory",
    "compaction_used_fresh_summarizer": "Compactions that ran a fresh LLM summary",
    "compaction_pre_pass_pruned": "Tool result entries dropped by pre-pass dedupe+age",
    "loop_guard_observed": "Tool calls observed by the loop guard",
    "loop_guard_tripped": "Tool-call loops detected (warning injected)",
    "output_budget_tightened": "LLM calls where output budget was capped below configured max",
    "commitment_harvest_skipped_no_cues": "Chat turns where pre-filter found no commitment language",
    "commitment_harvest_extracted": "Commitments extracted by harvester LLM",
    "commitment_harvest_persisted": "Commitments successfully written to DB",
    "commitment_sweeper_fired": "Commitments delivered by per-minute sweeper",
    "commitment_sweeper_expired": "Commitments expired by sweeper before firing",
    "session_memory_update_attempted": "Session-memory updater dispatched",
    "session_memory_update_persisted": "Session-memory updates that landed in DB",
    "session_memory_update_skipped_no_trigger": "Session-memory ticks below dual-threshold",
    "memory_archived": "Memories archived by decay sweeper",
    "memory_consolidated": "Memory clusters merged into super-memories",
    "memory_superseded_by_contradiction": "Memories marked superseded by writer-time contradiction check",
    "memory_active_remember_persisted": "Memories written via active remember() tool",
    "dispatch_gate_blocked": "Dispatch refused by bounds gate",
    "dispatch_gate_passed": "Dispatch passed bounds gate",
    "hook_aborted_run": "Runs aborted by hook",
    "streaming_started": "Streaming turns initiated",
    "streaming_aborted_mid": "Streaming turns aborted mid-stream",
    "link_injection_fetched": "URLs successfully fetched by link injection",
    "link_injection_failed": "URLs link injection failed to fetch",
    "tool_cache_hit": "Tool dispatches served from idempotent-skill result cache",
    "agent_todo_replaced": "Times agent rewrote its internal todo list",
    "agent_todo_completed": "Todo items marked completed",
}


def render_prometheus(metrics: AgentMetrics) -> str:
    """Render the metrics snapshot in Prometheus text-format 0.0.4.

    Lines are sorted by counter name for deterministic diff in tests.
    Unknown (typo'd) counters are emitted under the same convention so
    they're discoverable at /metrics scrape.
    """
    lines: list[str] = []
    snap = metrics.snapshot()

    # Canonical counters first (sorted)
    for name in sorted(COUNTER_NAMES):
        value = snap.get(name, 0)
        help_text = _HELP.get(name, name.replace("_", " ").title())
        prom_name = f"agent_{name}"
        lines.append(f"# HELP {prom_name} {help_text}")
        lines.append(f"# TYPE {prom_name} counter")
        lines.append(f'{prom_name}{{namespace="harness"}} {int(value)}')

    # Unknown counters (typos that ops should notice)
    unknown = snap.get("_unknown") or {}
    for name in sorted(unknown):
        value = unknown[name]
        prom_name = f"agent_unknown_{name}"
        lines.append(f"# HELP {prom_name} Unknown counter (typo'd at call site)")
        lines.append(f"# TYPE {prom_name} counter")
        lines.append(f'{prom_name}{{namespace="harness"}} {int(value)}')

    return "\n".join(lines) + "\n"


__all__ = ["render_prometheus"]
