"""Memory kind classifier — declarative / procedural / episodic.

Phase M (M3.C). Today every memory is treated the same at retrieval.
But three different kinds need different ranking strategies:

  declarative — "X is true" (user prefers Vue, the API is at /v1/...)
                Stable across time; high cosine match should win.
  procedural — "how to do X" (deploy steps, debug recipe)
                Re-used in similar future contexts; cosine + recency.
  episodic   — "what happened on date Y" (the failed deployment)
                Time-bound; recency multiplier.

Classification uses cheap rule-based heuristics with optional LLM
escalation. No DB required; pure functions.

The retriever (M3.D wire-up) reads ``kind`` and adjusts the salience
formula per row.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Awaitable, Callable, Optional


class MemoryKind(str, Enum):
    DECLARATIVE = "declarative"
    PROCEDURAL = "procedural"
    EPISODIC = "episodic"


# Heuristics — cheap pre-classifier. ~80% accurate on typical patterns.
# English uses \b for word boundaries; Chinese matched separately
# (CJK chars don't have word boundaries the same way).

_PROCEDURAL_EN = re.compile(
    r"(?i)\b("
    r"how\s+to|"
    r"step\s+\d|"
    r"first[,]?\s+then|"
    r"first[,]?\s+second|"
    r"to\s+(deploy|run|install|build|debug|fix|configure)|"
    r"recipe|"
    r"workflow|"
    r"procedure"
    r")\b"
)
_PROCEDURAL_ZH = re.compile(r"(运行|部署|安装|配置|步骤)")

_EPISODIC_EN = re.compile(
    r"(?i)\b("
    r"yesterday|today|last\s+(week|month|year|friday|monday|tuesday|wednesday|thursday|saturday|sunday)|"
    r"on\s+\d{4}-\d{2}-\d{2}|"
    r"in\s+(january|february|march|april|may|june|july|august|september|october|november|december)|"
    r"failed|crashed|broke|happened|occurred|deployed|shipped|launched"
    r")\b"
)
_EPISODIC_ZH = re.compile(r"(昨天|今天|上周|上个月|去年|发生|崩溃|失败|崩了)")


def _match_either(en_re, zh_re, text: str):
    return en_re.search(text) or zh_re.search(text)


def classify_heuristic(summary: str) -> MemoryKind:
    """Cheap rule-based classifier. Returns the best guess."""
    if not summary or not isinstance(summary, str):
        return MemoryKind.DECLARATIVE

    proc_match = _match_either(_PROCEDURAL_EN, _PROCEDURAL_ZH, summary)
    epi_match = _match_either(_EPISODIC_EN, _EPISODIC_ZH, summary)

    if proc_match and not epi_match:
        return MemoryKind.PROCEDURAL
    if epi_match and not proc_match:
        return MemoryKind.EPISODIC
    if proc_match and epi_match:
        # Both fired — pick the one with longer match (more specific).
        # Tie → episodic wins (past-tense + temporal anchors are
        # stronger signals than verb mention; "yesterday's deploy
        # failed" describes a past event, not a deploy procedure).
        if len(proc_match.group(0)) > len(epi_match.group(0)):
            return MemoryKind.PROCEDURAL
        return MemoryKind.EPISODIC
    # Default — most user facts are declarative
    return MemoryKind.DECLARATIVE


CLASSIFY_PROMPT_TEMPLATE = """\
Classify the following memory into ONE of these kinds:
  declarative — a fact / preference / state that's true ("user uses Vue")
  procedural  — a how-to / recipe / step-by-step ("to deploy: 1. ...")
  episodic    — something that happened at a specific time ("the build failed yesterday")

Output ONE word, no commentary:

Memory:
{summary}

Kind:"""


async def classify_with_llm(
    summary: str, llm_call: Callable[[str], Awaitable[str]]
) -> MemoryKind:
    """LLM-based classifier for borderline cases. Falls back to
    heuristic on LLM failure."""
    if not summary:
        return MemoryKind.DECLARATIVE
    try:
        raw = await llm_call(CLASSIFY_PROMPT_TEMPLATE.format(summary=summary[:500]))
    except Exception:
        return classify_heuristic(summary)
    cleaned = (raw or "").strip().lower()
    first = cleaned.split()[0].rstrip(".!?,;:") if cleaned.split() else ""
    try:
        return MemoryKind(first)
    except ValueError:
        return classify_heuristic(summary)


# Per-kind weight modifiers for the retriever's salience formula.
# Multiplied into the base composite score.
KIND_WEIGHT_MODIFIERS: dict[MemoryKind, float] = {
    # Declarative facts: standard weighting
    MemoryKind.DECLARATIVE: 1.0,
    # Procedural: slight boost — "how to do X" is high-utility
    MemoryKind.PROCEDURAL: 1.1,
    # Episodic: discount older episodes more aggressively (decay
    # multiplier in score_with_decay handles most of this; a small
    # additional penalty here biases toward declarative facts when
    # both match the query)
    MemoryKind.EPISODIC: 0.9,
}


def score_modifier_for(kind: Optional[MemoryKind]) -> float:
    """Returns the multiplier to apply to a memory's composite score.
    Returns 1.0 for None (un-classified legacy rows)."""
    if kind is None:
        return 1.0
    return KIND_WEIGHT_MODIFIERS.get(kind, 1.0)


__all__ = [
    "KIND_WEIGHT_MODIFIERS",
    "MemoryKind",
    "classify_heuristic",
    "classify_with_llm",
    "score_modifier_for",
]
