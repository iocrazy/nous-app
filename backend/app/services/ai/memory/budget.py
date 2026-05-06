"""Memory injection budget — per-agent top_n resolver.

Phase M (M3.D). Reads ``ai_agents.memory_injection_top_n`` (nullable)
and falls back to the code default when unset. Pure helper — caller
(MemoryRetriever wiring) passes in the agent row.
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.ai.memory.retriever import DEFAULT_TOP_N_FINAL


# Hard upper bound — admins can override per agent up to this cap. Beyond
# 20 the prompt becomes noise + cost balloons.
MAX_INJECTION_TOP_N = 20


def resolve_top_n(
    agent_row: Optional[dict[str, Any]],
    *,
    code_default: int = DEFAULT_TOP_N_FINAL,
    upper_bound: int = MAX_INJECTION_TOP_N,
) -> int:
    """Returns the top_n to use for this agent's memory recall.

    Precedence: agent_row.memory_injection_top_n (if set + valid)
                → code_default (5)
    Always clamped to [0, upper_bound].
    """
    if agent_row is None:
        return code_default
    raw = agent_row.get("memory_injection_top_n")
    if raw is None:
        return code_default
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return code_default
    if n < 0:
        return 0
    if n > upper_bound:
        return upper_bound
    return n


__all__ = ["MAX_INJECTION_TOP_N", "resolve_top_n"]
