"""Memory replay testing — "what if this memory didn't exist?"

Phase R (R2). Operators / power users sometimes need to validate the
impact of a specific memory: "is the agent answering this way BECAUSE
of memory X, or would it answer the same without it?" This primitive
enables A/B replay:

  1. Pick a query + recall the agent would normally do
  2. Build TWO recall outputs:
     a. baseline: full recall (current behavior)
     b. ablated: recall with N target memories EXCLUDED
  3. Run agent on both → compare assistant outputs
  4. Diff highlights what the memory contributed

Use cases:
  - Debug "why did agent say X" — was it a memory or hallucination?
  - Validate consolidation/decay quality — does removing the merged
    super-memory change behavior?
  - Audit: did this memory actually influence anything?

Pure layer:
  - ReplayDecision dataclass (baseline vs ablated)
  - filter_excluding(memories, excluded_ids) — pure list op
  - diff_response(a, b) — character-level diff, returns ReplayDiff
  - score_influence(diff) — heuristic 0..1: how much did the memory
    matter? (0 = identical responses; 1 = totally different)

DB-touching pieces (load full + ablated agent runs) deferred — this
module is the algorithm + diff layer.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class ReplayDiff:
    """Comparison between baseline + ablated agent responses."""

    baseline_text: str
    ablated_text: str
    char_similarity: float  # 0..1; 1 = identical
    word_similarity: float  # 0..1
    differing_tokens: tuple[str, ...] = field(default_factory=tuple)


def filter_excluding(memory_ids: Iterable[str], excluded: Iterable[str]) -> list[str]:
    """Return memory_ids with any id in ``excluded`` removed."""
    excl = set(excluded)
    return [mid for mid in memory_ids if mid not in excl]


def diff_response(baseline: str, ablated: str) -> ReplayDiff:
    """Compute char + word similarity + a sample of differing tokens.

    char_similarity uses SequenceMatcher.ratio() (insertion-cost
    aware). word_similarity does the same on space-tokenized lists.
    differing_tokens is the first ~10 tokens that appear in only one
    of the responses — useful for surfacing the specific memory
    contribution.
    """
    if baseline == ablated:
        return ReplayDiff(
            baseline_text=baseline,
            ablated_text=ablated,
            char_similarity=1.0,
            word_similarity=1.0,
        )

    char_sim = difflib.SequenceMatcher(None, baseline, ablated).ratio()

    base_words = baseline.split()
    abl_words = ablated.split()
    word_sim = (
        difflib.SequenceMatcher(None, base_words, abl_words).ratio()
        if (base_words or abl_words)
        else 1.0
    )

    base_set = set(base_words)
    abl_set = set(abl_words)
    diff_tokens = sorted((base_set ^ abl_set))[:10]

    return ReplayDiff(
        baseline_text=baseline,
        ablated_text=ablated,
        char_similarity=char_sim,
        word_similarity=word_sim,
        differing_tokens=tuple(diff_tokens),
    )


def score_influence(diff: ReplayDiff) -> float:
    """Composite "how much did the memory matter?" score in 0..1.

    Uses 1 - mean(char_sim, word_sim). Higher = memory had bigger
    impact on the response.
    """
    avg_sim = (diff.char_similarity + diff.word_similarity) / 2
    return max(0.0, min(1.0, 1.0 - avg_sim))


def categorize_influence(score: float) -> str:
    """Human-readable label for an influence score."""
    if score < 0.05:
        return "negligible"
    if score < 0.20:
        return "minor"
    if score < 0.50:
        return "moderate"
    return "major"


__all__ = [
    "ReplayDiff",
    "categorize_influence",
    "diff_response",
    "filter_excluding",
    "score_influence",
]
