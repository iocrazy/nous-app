"""Commitment harvester — extract "I commit to X" from agent output.

Wave 5e (E1). Sprint 4 landed agent_commitments table + repo +
primitive types but the actual capture path was empty. Today the agent
can say "I'll check back tomorrow" and that promise vanishes.

This module gives the post-turn extractor:
  1. After each agent response, scan content for commitment cues
     ("I'll", "I will", "by tomorrow", "remind you when", ...)
  2. If candidates found, ask cheap LLM to extract structured commitments
  3. Insert each into agent_commitments via existing repo
  4. Emit LifecycleBus event per insert (telemetry + UI live-update)

Pure detection layer — fire-and-forget. Failures swallowed; the agent
response was already returned to the user.

Why not run the LLM call on EVERY turn? — too expensive. The cheap
heuristic regex pre-filter cuts ~95% of turns before the LLM gets
involved (most turns don't contain any commitment language).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional


# Cheap pre-filter: look for English/Chinese commitment cues. Catches
# the obvious cases; misses subtle ones (acceptable — false negatives
# just mean "no commitment harvested", not corrupt state).
_COMMITMENT_CUES = re.compile(
    r"\b("
    r"i['’]?ll|i\s+will|i\s+can\s+(remind|check|notify|follow)|"
    r"i['’]?ll\s+(remind|check|notify|follow\s+up)|"
    r"let\s+me\s+know\s+when|"
    r"by\s+(tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next\s+week)|"
    r"in\s+\d+\s+(minute|hour|day|week|month)s?|"
    r"when\s+(this|the)\s+(job|task|build|deploy)\s+(finishes|completes|ships)"
    r")\b",
    re.IGNORECASE,
)

# Chinese cues
_COMMITMENT_CUES_ZH = re.compile(
    r"(我[会將会]|我[来來]|提醒你|稍后|稍後|明天|今晚|下周|下週|完成后|完成後|"
    r"过\d+(分钟|小时|天|周|月)|過\d+(分鐘|小時|天|週|月))"
)


def has_commitment_cues(text: str) -> bool:
    """Cheap pre-filter. True if text contains any commitment-language hint."""
    if not text or not isinstance(text, str):
        return False
    return bool(_COMMITMENT_CUES.search(text) or _COMMITMENT_CUES_ZH.search(text))


# ─── LLM extraction prompt ───────────────────────────────────────────


HARVEST_PROMPT_TEMPLATE = """\
You extract commitments the assistant made in its response. A commitment
is a promise to do something later: a reminder, a follow-up, a task
that requires action AFTER this conversation turn.

Output strictly JSON. Each commitment has:
  - description: one-line summary of the promise
  - trigger_type: one of "time", "event", "next_session"
  - trigger_at: ISO8601 timestamp (only for trigger_type=time)
  - trigger_event: short event identifier (only for trigger_type=event,
                   e.g. "build.finished:42", "pr.merged:142")

Output an empty array [] if no commitments.
DO NOT extract things that are completed in THIS turn. Only future actions.
DO NOT add commentary. Output ONLY the JSON array.

Now is: {now_iso}

Assistant response to analyze:
\"\"\"
{response}
\"\"\"

Commitments JSON array:
"""


def build_harvest_prompt(*, response_text: str, now: Optional[datetime] = None) -> str:
    n = now or datetime.now(timezone.utc)
    return HARVEST_PROMPT_TEMPLATE.format(
        now_iso=n.isoformat(),
        response=response_text.strip(),
    )


# ─── Parse + persist ─────────────────────────────────────────────────


@dataclass(frozen=True)
class HarvestedCommitment:
    """One extracted commitment ready to feed into CommitmentRepository.create."""

    description: str
    trigger_type: str  # 'time' / 'event' / 'next_session'
    trigger_at: Optional[datetime] = None
    trigger_event: Optional[str] = None


def parse_harvest_output(raw: str) -> list[HarvestedCommitment]:
    """Parse the LLM's JSON output. Tolerates trailing commentary +
    markdown code-fence wrap. Skips malformed entries silently — the
    user's commitments matter more than perfect-LLM output."""
    import json

    if not raw:
        return []
    text = raw.strip()
    # Strip markdown fence if present (LLM may ignore "no commentary")
    if text.startswith("```"):
        # Drop the fence + optional language tag + trailing fence
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    # Take only up to the first valid JSON close bracket — strip trailing prose
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON array within the text
        match = re.search(r"(\[.*\])", text, re.DOTALL)
        if not match:
            return []
        try:
            decoded = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []
    if not isinstance(decoded, list):
        return []

    out: list[HarvestedCommitment] = []
    for item in decoded:
        if not isinstance(item, dict):
            continue
        desc = item.get("description")
        ttype = item.get("trigger_type")
        if not desc or ttype not in {"time", "event", "next_session"}:
            continue
        ts = item.get("trigger_at")
        evt = item.get("trigger_event")
        parsed_ts = None
        if ts:
            try:
                parsed_ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                # If trigger_type=time but trigger_at malformed → skip the row
                if ttype == "time":
                    continue
        # trigger-data invariant (matches DB CHECK on agent_commitments)
        if ttype == "time" and parsed_ts is None:
            continue
        if ttype == "event" and not evt:
            continue
        out.append(
            HarvestedCommitment(
                description=str(desc).strip(),
                trigger_type=ttype,
                trigger_at=parsed_ts,
                trigger_event=evt,
            )
        )
    return out


# ─── Orchestrator ─────────────────────────────────────────────────────


# Caller-injected: takes the prompt, returns the LLM's text output.
HarvestSummarizer = Callable[[str], Awaitable[str]]
# Caller-injected: takes one HarvestedCommitment + run context, persists.
CommitmentPersistor = Callable[
    [HarvestedCommitment, "HarvestContext"], Awaitable[Optional[str]]
]


@dataclass(frozen=True)
class HarvestContext:
    """Identity + run info passed through to the persistor."""

    agent_id: str
    user_id: Optional[str]
    session_id: Optional[str]
    run_id: Optional[str] = None


@dataclass(frozen=True)
class HarvestResult:
    """Outcome of a harvest pass."""

    candidate_text_had_cues: bool
    commitments_extracted: int = 0
    commitments_persisted: int = 0
    commitment_ids: list[str] = field(default_factory=list)
    error: Optional[str] = None


async def harvest_commitments(
    *,
    response_text: str,
    context: HarvestContext,
    summarizer: HarvestSummarizer,
    persistor: CommitmentPersistor,
    now: Optional[datetime] = None,
) -> HarvestResult:
    """End-to-end: pre-filter → LLM extract → persist each.

    Always returns HarvestResult (never raises). Pre-filter miss returns
    early with candidate_text_had_cues=False; downstream telemetry can
    aggregate "% of turns that triggered LLM call" cheaply.
    """
    if not has_commitment_cues(response_text):
        return HarvestResult(candidate_text_had_cues=False)

    prompt = build_harvest_prompt(response_text=response_text, now=now)
    try:
        raw = await summarizer(prompt)
    except Exception as exc:
        return HarvestResult(
            candidate_text_had_cues=True,
            error=f"summarizer failed: {type(exc).__name__}: {exc}",
        )

    extracted = parse_harvest_output(raw)
    persisted_ids: list[str] = []
    for commitment in extracted:
        try:
            cid = await persistor(commitment, context)
        except Exception:
            continue  # one bad commitment shouldn't sink the rest
        if cid:
            persisted_ids.append(str(cid))

    return HarvestResult(
        candidate_text_had_cues=True,
        commitments_extracted=len(extracted),
        commitments_persisted=len(persisted_ids),
        commitment_ids=persisted_ids,
    )


__all__ = [
    "CommitmentPersistor",
    "HarvestContext",
    "HarvestResult",
    "HarvestSummarizer",
    "HarvestedCommitment",
    "build_harvest_prompt",
    "harvest_commitments",
    "has_commitment_cues",
    "parse_harvest_output",
]
