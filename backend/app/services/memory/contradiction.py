"""Memory contradiction resolution — writer-time checks for conflicts.

Wave 5d (M2.C). When the user says contradictory things over time
("I prefer Vue" → 6 months later → "I now use React"), both memories
get written and BOTH stay in the active set. Retrieval at cosine top-K
will return them both, leaving the model to guess which is current.

This module runs at WRITE time:
  1. Before inserting a new memory, retrieve top-K nearest existing
     memories in same namespace (cosine ≥ HIGH_THRESHOLD)
  2. For each high-similarity neighbor, ask cheap LLM:
       Does NEW replace / supplement / contradict / unrelated to OLD?
  3. If "replaces" or "contradicts": old.status = 'superseded',
     old.superseded_by = new.id (re-uses M2.B column)
  4. If "supplements" or "unrelated": both stay

This is a primitive layer — pure types + LLM prompt + decision parse.
The DB-touching wire-up (insert vs supersede) lives in writer.py
(integration follow-up for M2.C).

The cheap LLM is the bottleneck: every write triggers ~3 cheap calls
(one per high-similarity neighbor). Acceptable cost: writes are 10x
less frequent than reads in mediahub's pattern.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Optional


# Two-tier threshold:
#   HIGH (≥0.90): worth asking the LLM at all
#   ELITE (≥0.95): pre-decision sanity (very likely contradiction)
HIGH_SIMILARITY = 0.90
ELITE_SIMILARITY = 0.95


class ContradictionVerdict(str, Enum):
    """LLM-returned classification."""

    REPLACES = "replaces"          # NEW makes OLD wrong/stale
    CONTRADICTS = "contradicts"    # NEW directly conflicts with OLD
    SUPPLEMENTS = "supplements"    # Both true, complementary
    UNRELATED = "unrelated"        # LLM thinks they're not really about same fact


# Verdicts that should mark OLD as superseded.
SUPERSEDING_VERDICTS = frozenset(
    {ContradictionVerdict.REPLACES, ContradictionVerdict.CONTRADICTS}
)


@dataclass(frozen=True)
class ContradictionDecision:
    """Per-pair classification + the OLD memory id involved."""

    old_memory_id: str
    verdict: ContradictionVerdict
    raw_llm_output: str  # for audit


CONTRADICTION_PROMPT_TEMPLATE = """\
You compare two memories about a user/system and classify their
relationship. Output ONE word: replaces / contradicts / supplements / unrelated

Definitions:
- replaces: the new memory makes the old one wrong or stale (the truth changed)
- contradicts: the two cannot both be true; one must be wrong (we trust the new)
- supplements: both can be true; they describe complementary aspects
- unrelated: they are not really about the same fact

OLD memory:
{old}

NEW memory:
{new}

Classification (one word, no commentary):
"""


def build_contradiction_prompt(*, old_summary: str, new_summary: str) -> str:
    return CONTRADICTION_PROMPT_TEMPLATE.format(
        old=old_summary.strip(), new=new_summary.strip()
    )


def parse_verdict(raw: str) -> ContradictionVerdict:
    """Parse the LLM's one-word output. Tolerates whitespace, casing,
    punctuation. Unknown → UNRELATED (safest — keeps both memories)."""
    if not raw:
        return ContradictionVerdict.UNRELATED
    cleaned = raw.strip().lower()
    # Take first token (LLM may add commentary even when told not to)
    tokens = cleaned.split()
    if not tokens:
        return ContradictionVerdict.UNRELATED
    first = tokens[0].rstrip(".!?,;:'\"")
    try:
        return ContradictionVerdict(first)
    except ValueError:
        return ContradictionVerdict.UNRELATED


# Caller-injected: takes the prompt, returns the LLM's text response.
ContradictionClassifier = Callable[[str], Awaitable[str]]


async def classify_pair(
    *,
    old_summary: str,
    old_id: str,
    new_summary: str,
    classifier: ContradictionClassifier,
) -> Optional[ContradictionDecision]:
    """Run one classification. Returns None on classifier failure."""
    prompt = build_contradiction_prompt(
        old_summary=old_summary, new_summary=new_summary
    )
    try:
        raw = await classifier(prompt)
    except Exception:
        return None
    return ContradictionDecision(
        old_memory_id=old_id,
        verdict=parse_verdict(raw),
        raw_llm_output=raw,
    )


def select_supersede_targets(
    decisions: list[ContradictionDecision],
) -> list[str]:
    """Filter to ids that should be marked superseded by the new memory."""
    return [
        d.old_memory_id
        for d in decisions
        if d.verdict in SUPERSEDING_VERDICTS
    ]


__all__ = [
    "ELITE_SIMILARITY",
    "HIGH_SIMILARITY",
    "SUPERSEDING_VERDICTS",
    "ContradictionClassifier",
    "ContradictionDecision",
    "ContradictionVerdict",
    "build_contradiction_prompt",
    "classify_pair",
    "parse_verdict",
    "select_supersede_targets",
]
