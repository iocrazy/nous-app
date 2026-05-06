"""Memory provenance verification — periodically check "is this still true?"

Phase R (R1). Memories are written based on what the user said at one
point in time. Six months later many become stale: "user lives in NYC"
when they've moved, "user uses Python 3.10" when they've upgraded.
The decay sweeper (M2.A) is age-based; this primitive is FACT-based.

Pure layer:
  - VerificationVerdict enum: still_true / outdated / unverifiable
  - parse_verdict() tolerant LLM-output parser
  - build_verification_prompt(): asks cheap LLM to validate one memory
    against optional "current context" (recent user msgs / recent
    facts) so it can spot contradictions

The DB-touching sweeper that batches verification + flips outdated to
status='superseded' is R1.5 (deferred — needs new agent_memories
column for last_verified_at).

This module is fully testable without DB or LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Optional


class VerificationVerdict(str, Enum):
    STILL_TRUE = "still_true"
    OUTDATED = "outdated"
    UNVERIFIABLE = "unverifiable"


# UNVERIFIABLE = LLM can't tell from the context. Caller treats this
# as "leave alone" — no auto-supersede on uncertain calls.


VERIFY_PROMPT_TEMPLATE = """\
You verify whether a previously-stored fact is STILL true based on the
user's recent activity.

OUTPUT exactly ONE word from: still_true / outdated / unverifiable

Stored memory:
{memory}

Recent user activity (newest last):
{context}

Rules:
- still_true: nothing in the recent activity contradicts the memory
- outdated: recent activity directly contradicts (user explicitly
  changed their mind / moved / upgraded / etc)
- unverifiable: the recent activity doesn't give enough signal either
  way — DEFAULT TO THIS when uncertain. Wrong "outdated" calls cost
  more than wrong "unverifiable" ones.

Your verdict (one word, no commentary):"""


def build_verification_prompt(*, memory: str, context: str) -> str:
    """Build the LLM prompt. ``context`` is recent user messages /
    summary; pass empty string when no context available — the LLM
    will return UNVERIFIABLE."""
    return VERIFY_PROMPT_TEMPLATE.format(
        memory=memory.strip()[:500],
        context=(context or "").strip()[:2000] or "(none)",
    )


def parse_verdict(raw: str) -> VerificationVerdict:
    """Tolerant parser. Default: UNVERIFIABLE (safest for ambiguous
    LLM output — never wrongly mark a memory outdated)."""
    if not isinstance(raw, str) or not raw.strip():
        return VerificationVerdict.UNVERIFIABLE
    cleaned = raw.strip().lower()
    tokens = cleaned.split()
    if not tokens:
        return VerificationVerdict.UNVERIFIABLE
    first = tokens[0].rstrip(".!?,;:'\"")
    try:
        return VerificationVerdict(first)
    except ValueError:
        return VerificationVerdict.UNVERIFIABLE


@dataclass(frozen=True)
class VerificationDecision:
    memory_id: str
    verdict: VerificationVerdict
    raw_llm_output: str


# Caller-injected: takes prompt → LLM text response.
VerificationClassifier = Callable[[str], Awaitable[str]]


async def verify_one(
    *,
    memory_id: str,
    memory_summary: str,
    context: str,
    classifier: VerificationClassifier,
) -> Optional[VerificationDecision]:
    """Run one verification. Returns None on classifier failure (caller
    skips this memory — leaves status alone)."""
    prompt = build_verification_prompt(memory=memory_summary, context=context)
    try:
        raw = await classifier(prompt)
    except Exception:
        return None
    return VerificationDecision(
        memory_id=memory_id,
        verdict=parse_verdict(raw),
        raw_llm_output=raw or "",
    )


def select_outdated(
    decisions: list[VerificationDecision],
) -> list[str]:
    """Filter to ids that the LLM marked outdated. UNVERIFIABLE rows
    are intentionally NOT in this list — caller leaves them alone."""
    return [
        d.memory_id
        for d in decisions
        if d.verdict == VerificationVerdict.OUTDATED
    ]


__all__ = [
    "VerificationClassifier",
    "VerificationDecision",
    "VerificationVerdict",
    "build_verification_prompt",
    "parse_verdict",
    "select_outdated",
    "verify_one",
]
