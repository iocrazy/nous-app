"""Objective heat scoring for hotspots.

Heat is evidence that an item is *actually* popular — independent of the LLM's
subjective relevance score. It is derived from the ``rank_timeline``: the
board positions an item held across successive fetches.

Signals blended into a 0..1 heat:
- best rank reached (a #1 is hotter than a #9)
- average rank quality across observations (consistently high vs a one-off spike)
- persistence (how many fetches it stayed on the board)

Items with no usable rank (e.g. RSS feeds, which are not ranked boards) fall
back to a weak persistence-only heat so they never out-rank a genuine #1.
"""

from __future__ import annotations

from typing import Any

# A board's "top" — ranks beyond this contribute ~no rank signal.
_TOP_N = 10
# Persistence saturates here: staying on board this many fetches = max signal.
_PERSISTENCE_CAP = 12


def _rank_quality(rank: int) -> float:
    """#1 -> 1.0, #10 -> ~0.1, beyond top-N -> 0.0."""
    if rank <= 0:
        return 0.0
    return max(0.0, (_TOP_N + 1 - rank) / _TOP_N) if rank <= _TOP_N else 0.0


def _valid_ranks(timeline: list[dict[str, Any]]) -> list[int]:
    out: list[int] = []
    for point in timeline or []:
        r = point.get("rank")
        if isinstance(r, int) and r > 0:
            out.append(r)
    return out


def compute_heat(timeline: list[dict[str, Any]]) -> float:
    """Map a rank_timeline into a 0..1 heat score. Pure; never raises."""
    if not timeline:
        return 0.0
    persistence = min(len(timeline), _PERSISTENCE_CAP) / _PERSISTENCE_CAP
    ranks = _valid_ranks(timeline)
    if not ranks:
        # No board rank (unranked source): persistence-only, capped low so it
        # cannot compete with genuinely ranked hotspots.
        return round(0.3 * persistence, 4)
    best_quality = _rank_quality(min(ranks))
    avg_quality = sum(_rank_quality(r) for r in ranks) / len(ranks)
    heat = 0.5 * best_quality + 0.3 * avg_quality + 0.2 * persistence
    return round(min(1.0, heat), 4)


def best_rank(timeline: list[dict[str, Any]]) -> int | None:
    """Best (lowest) board position ever held, for display. None if unranked."""
    ranks = _valid_ranks(timeline)
    return min(ranks) if ranks else None
