"""Agent-memory promotion gate (Phase C1).

Pure logic — no DB, no LLM import. The caller supplies:
- ``draft``: a MemoryDraft to evaluate
- ``scope``: target sharing scope ('team' or 'project')
- ``evaluator``: an async callable (prompt: str) -> str

Public surface
--------------
PromotionVerdict         frozen dataclass
build_promotion_prompt() the classification/scrub prompt
parse_promotion_output() defensive JSON parse, fail-closed → None
evaluate_promotion()     end-to-end: prompt → evaluator → parse → gate
_MIN_CONFIDENCE          0.7 module constant
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from loguru import logger

from app.services.ai.memory.agent_memory_consolidation import MemoryDraft

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_CONFIDENCE: float = 0.7

# Kinds that are eligible for team/project sharing.
# 'preference' is always personal and must NEVER be shared.
_SHAREABLE_KINDS: frozenset[str] = frozenset({"fact", "decision", "procedure"})

# ---------------------------------------------------------------------------
# PromotionVerdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionVerdict:
    """Result of the LLM classification/scrub step.

    Fields
    ------
    shareable:
        True when the LLM judged the entry as durable project/team knowledge.
    confidence:
        Model's self-reported confidence (0.0–1.0).
    justification:
        Short rationale from the model.
    scrubbed_body_md:
        PII-scrubbed version of the original ``body_md`` (names, emails,
        tokens, and verbatim private text removed).
    """

    shareable: bool
    confidence: float
    justification: str
    scrubbed_body_md: str


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a memory-promotion classifier. Your task is to decide whether a \
private agent-memory entry is safe to share with a broader team or project \
audience, and to return a PII-scrubbed version of its body.

## Memory entry

Title       : {title}
Kind        : {kind}
When to use : {when_to_use}
Body (raw)  :
{body_md}

## Target sharing scope

{scope}

## Instructions

1. Decide whether this memory is **durable project/team knowledge** — facts, \
decisions, or step-by-step procedures that genuinely apply to more than one \
person. Preferences, personal notes, and single-user workarounds are NOT \
shareable.
2. Assign a confidence score between 0.0 and 1.0.
3. Write a one-sentence justification.
4. Produce a ``scrubbed_body_md``: a copy of the raw Body above with all PII \
removed — full names, email addresses, API tokens, passwords, and any \
verbatim private text must be replaced with ``[redacted]``. If nothing needs \
scrubbing, reproduce the body unchanged.

## Output format

Reply with a single JSON object and nothing else (no fences, no prose):
{{
  "shareable": <bool>,
  "confidence": <float 0.0–1.0>,
  "justification": "<one sentence>",
  "scrubbed_body_md": "<scrubbed markdown>"
}}
"""


def build_promotion_prompt(*, draft: MemoryDraft, scope: str) -> str:
    """Build the classification/scrub prompt for the promotion evaluator.

    Parameters
    ----------
    draft:
        The memory entry under review.
    scope:
        Target sharing scope string (e.g. 'team', 'project').

    Returns
    -------
    str
        Complete prompt ready to pass to the evaluator callable.
    """
    return _PROMPT_TEMPLATE.format(
        title=draft.title,
        kind=draft.kind,
        when_to_use=draft.when_to_use,
        body_md=draft.body_md,
        scope=scope,
    )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_promotion_output(text: str) -> Optional[PromotionVerdict]:
    """Parse the LLM's JSON object into a PromotionVerdict.

    Tolerant of:
    - Markdown code fences (``` or ```json)
    - Leading/trailing prose (grabs the first ``{…}`` block)

    Returns None on ANY malformed or incomplete output (fail-closed: no
    unverified proposal is ever created from bad LLM output).
    """
    try:
        if not text or not text.strip():
            return None

        # Strip markdown fences first.
        fence_match = _FENCE_RE.search(text)
        if fence_match:
            text = fence_match.group(1)

        # Grab first {...} block to tolerate surrounding prose.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return None

        obj = json.loads(text[start : end + 1])
        if not isinstance(obj, dict):
            return None

        shareable = obj.get("shareable")
        confidence = obj.get("confidence")
        justification = obj.get("justification")
        scrubbed_body_md = obj.get("scrubbed_body_md")

        # All four fields must be present with correct types.
        if not isinstance(shareable, bool):
            return None
        if not isinstance(confidence, (int, float)):
            return None
        if not isinstance(justification, str) or not justification:
            return None
        if not isinstance(scrubbed_body_md, str):
            return None

        return PromotionVerdict(
            shareable=shareable,
            confidence=float(confidence),
            justification=justification,
            scrubbed_body_md=scrubbed_body_md,
        )
    except Exception:  # noqa: BLE001 — parse must never raise
        logger.debug("[promotion_gate] parse_promotion_output failed — returning None")
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

_Evaluator = Callable[[str], Awaitable[str]]


async def evaluate_promotion(
    *,
    draft: MemoryDraft,
    scope: str,
    evaluator: _Evaluator,
) -> Optional[PromotionVerdict]:
    """Run the full promotion evaluation pipeline.

    Steps
    -----
    1. Build the classification/scrub prompt.
    2. Await ``evaluator(prompt)`` — the caller supplies the LLM callable.
    3. Parse the raw output into a PromotionVerdict.
    4. Apply the gate:
       - ``shareable`` must be True
       - ``confidence`` must be ≥ _MIN_CONFIDENCE (0.7)
       - ``draft.kind`` must be in _SHAREABLE_KINDS ('fact', 'decision',
         'procedure'); 'preference' is NEVER allowed through.

    Returns
    -------
    PromotionVerdict if all gates pass, else None.

    Never raises — any exception returns None (fail-closed).
    """
    try:
        prompt = build_promotion_prompt(draft=draft, scope=scope)
        raw = await evaluator(prompt)
        verdict = parse_promotion_output(raw)

        if verdict is None:
            return None

        if not verdict.shareable:
            return None

        if verdict.confidence < _MIN_CONFIDENCE:
            return None

        if draft.kind not in _SHAREABLE_KINDS:
            return None

        return verdict
    except Exception:  # noqa: BLE001 — promotion gate must never break callers
        logger.opt(exception=True).warning(
            f"[promotion_gate] evaluate_promotion failed for draft.title={draft.title!r}"
        )
        return None


__all__ = [
    "PromotionVerdict",
    "build_promotion_prompt",
    "parse_promotion_output",
    "evaluate_promotion",
    "_MIN_CONFIDENCE",
]
