"""Cost computation module — turn provider rate + token usage → cost_snapshot.

Phase 0.5-C of Canvas + AI Infrastructure 17-week upgrade plan.

Public surface:
- :class:`TokenUsage` — input shape (one per LLM call)
- :func:`compute_cost` — main entry. Takes provider + model + usage + flags;
  returns a fully-populated :class:`CostSnapshot` ready to JSONB-serialise into
  ``agent_run_events.cost_snapshot``.
- :class:`CostSnapshot` — output shape, mirrors the schema documented in
  migration 166 header and the OpenTelemetry GenAI Semantic Conventions.

This module DOES NOT write to the database; callers (adapter wrapper /
RunRecorder) handle persistence.
"""

from __future__ import annotations

from app.services.ai.cost.snapshot import (
    BillingMetadata,
    CostBreakdown,
    CostSnapshot,
    DiscountsApplied,
    RatesUsed,
    TokenUsage,
    compute_cost,
)

__all__ = [
    "BillingMetadata",
    "CostBreakdown",
    "CostSnapshot",
    "DiscountsApplied",
    "RatesUsed",
    "TokenUsage",
    "compute_cost",
]
