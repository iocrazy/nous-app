"""Memory decay — half-life forgetting + salience re-scoring.

Wave 5d (M2.A). Today the memory retriever scores candidates as
``0.7·cosine + 0.3·log(1+reinforce_count)``. Reinforcement only goes
up — there's no decay. A memory written 6 months ago that hasn't been
recalled since is weighted the same as one written yesterday.

This module gives:
  - ``decay_score(memory, now)``: e^(-Δt/half_life) where Δt is time
    since last activity (recall if any, else creation)
  - ``score_with_decay(cosine, reinforce, decay_value, recency_weight=...)``:
    composite salience that downweights stale items
  - ``should_archive(memory, now, threshold=0.05)``: archival decision
    used by the weekly sweeper

The retriever wires this in (M2.A.b). The sweeper (M2.A.c) calls
should_archive in batch and flips status='archived' on hits.

Half-life calibration: 30-day default. Conversational facts
("user prefers dark mode") fade slow; transient project state
("deploying tonight") fades fast. M2.B consolidation later allows
per-memory half_life override.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# Default half-life: memory loses half its weight after 30 days of
# no activity. 30 days picked because most of mediahub's facts (user
# preferences, project context) are stable across weeks but stale at
# month boundaries.
DEFAULT_HALF_LIFE_DAYS = 30.0

# Default archival threshold. decay_score below this → archive on next
# sweeper pass. 0.05 corresponds to ~130 days at 30-day half-life
# (e^(-130/30) ≈ 0.05).
DEFAULT_ARCHIVE_THRESHOLD = 0.05


@dataclass(frozen=True)
class MemoryDecayInput:
    """Minimal subset of agent_memories row needed for decay scoring."""

    created_at: datetime
    last_recalled_at: Optional[datetime] = None
    reinforcement_count: int = 0


def _last_activity(mem: MemoryDecayInput) -> datetime:
    """Most recent of (created_at, last_recalled_at). Recall counts as
    activity that resets the decay clock."""
    if mem.last_recalled_at is None:
        return mem.created_at
    return max(mem.created_at, mem.last_recalled_at)


def decay_score(
    mem: MemoryDecayInput,
    *,
    now: Optional[datetime] = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float:
    """Exponential half-life decay. Returns value in (0.0, 1.0].

    Just-active memory → 1.0. After half_life_days → 0.5. After 2·half_life
    → 0.25. After 4·half_life → ~0.06.
    """
    if half_life_days <= 0:
        raise ValueError("half_life_days must be > 0")
    activity = _last_activity(mem)
    if activity.tzinfo is None:
        activity = activity.replace(tzinfo=timezone.utc)
    n = now or datetime.now(timezone.utc)
    if n.tzinfo is None:
        n = n.replace(tzinfo=timezone.utc)
    delta_days = max(0.0, (n - activity).total_seconds() / 86400.0)
    # Half-life formula: 0.5^(Δt / half_life)
    return math.pow(0.5, delta_days / half_life_days)


def score_with_decay(
    *,
    cosine: float,
    reinforcement_count: int,
    decay: float,
    cosine_weight: float = 0.6,
    salience_weight: float = 0.25,
    recency_weight: float = 0.15,
) -> float:
    """Composite ranking score for retrieval.

    Replaces the legacy 0.7·cosine + 0.3·log(1+reinforce). The new
    formulation:
      - 60% similarity (still primary signal)
      - 25% reinforced-salience MULTIPLIED by decay (fading reinforce)
      - 15% pure recency

    Decay multiplier on the salience term is critical: a memory with
    reinforce=10 from a year ago should NOT outscore a fresh memory
    with reinforce=2.
    """
    # Clamp inputs defensively
    cosine = max(0.0, min(1.0, cosine))
    decay = max(0.0, min(1.0, decay))
    salience_term = math.log(1 + max(0, reinforcement_count)) * decay
    return (
        cosine_weight * cosine
        + salience_weight * salience_term
        + recency_weight * decay
    )


def should_archive(
    mem: MemoryDecayInput,
    *,
    now: Optional[datetime] = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    threshold: float = DEFAULT_ARCHIVE_THRESHOLD,
) -> bool:
    """True iff this memory's decay has dropped below ``threshold``.
    Sweeper flips matching rows to status='archived'."""
    return decay_score(mem, now=now, half_life_days=half_life_days) < threshold


__all__ = [
    "DEFAULT_ARCHIVE_THRESHOLD",
    "DEFAULT_HALF_LIFE_DAYS",
    "MemoryDecayInput",
    "decay_score",
    "score_with_decay",
    "should_archive",
]
