"""Agent-memory consolidation service (Phase B).

Pure logic — no DB, no LLM import. The caller supplies a ``consolidator``
async callable so this module is fully testable without a model.

Public surface
--------------
MemoryDraft               frozen dataclass (title, body_md, when_to_use, kind)
make_fingerprint()        SHA1 keyed on normalised title → dedup by topic
build_consolidation_prompt()  the /dream prompt text
parse_consolidation_output()  defensive JSON parse, kind-clamp, never raises
consolidate_pair()        end-to-end: prompt → LLM → parse → dedup → cap
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Set, Tuple

from loguru import logger

_VALID_KINDS: frozenset[str] = frozenset(
    {"fact", "decision", "preference", "procedure"}
)


@dataclass(frozen=True)
class MemoryDraft:
    title: str
    body_md: str
    when_to_use: str
    kind: str


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


def make_fingerprint(
    owner_user_id: str, agent_id: str, scope: str, scope_id: str, draft: MemoryDraft
) -> str:
    """SHA1 of ``owner|agent|scope|scope_id|normalised_title`` — the dedup unit
    is *topic within a context*, so the same topic in two contexts stays
    distinct (and can be promoted independently)."""
    normalised = draft.title.strip().lower()
    raw = f"{owner_user_id}|{agent_id}|{scope}|{scope_id}|{normalised}"
    return hashlib.sha1(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a memory-consolidation assistant. Your job is to distil a raw \
conversation log into a small set of durable, reusable memory entries that \
will help an AI agent serve THIS user better in future sessions.

## Instructions

1. Read the recent activity below.
2. Extract facts, decisions, preferences, or step-by-step procedures that are \
   stable and genuinely reusable — not one-off remarks.
3. Skip anything already covered by the existing titles listed below.
4. Be compact and high-signal. Omit filler. Each entry should be worth storing \
   permanently.
5. Output a JSON array (and nothing else) where every item has exactly these \
   four string fields:
   - "title"        — short, unique topic name (≤10 words)
   - "body_md"      — Markdown paragraph; concrete and actionable
   - "when_to_use"  — one sentence describing when this memory is relevant
   - "kind"         — one of: fact | decision | preference | procedure

## Existing memory titles (skip these topics)

{existing_titles_block}

## Recent activity

{recent_activity}

## Output

Reply with a JSON array only — no prose, no markdown fences, no explanation:
[{{"title": "...", "body_md": "...", "when_to_use": "...", "kind": "..."}}]
If there is nothing new worth storing, reply with an empty array: []
"""


def build_consolidation_prompt(
    *,
    recent_activity: str,
    existing_titles: List[str],
) -> str:
    """Build the /dream prompt for the consolidator LLM.

    Parameters
    ----------
    recent_activity:
        Raw text of recent conversation turns / actions for this agent-user pair.
    existing_titles:
        Titles of memory entries already stored; the model must skip these topics.

    Returns
    -------
    str
        Complete system + user prompt ready to pass to a chat-completions call.
    """
    if existing_titles:
        block = "\n".join(f"- {t}" for t in existing_titles)
    else:
        block = "(none yet)"

    return _PROMPT_TEMPLATE.format(
        existing_titles_block=block,
        recent_activity=recent_activity.strip() or "(no recent activity)",
    )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_consolidation_output(text: str) -> List[MemoryDraft]:
    """Parse the LLM's JSON array into a list of MemoryDraft objects.

    Tolerant of:
    - Markdown code fences (``` or ```json)
    - Leading/trailing prose (we grab the first ``[…]`` bracket pair)
    - Malformed individual items (dropped silently)
    - ``kind`` values outside the enum (clamped to ``'fact'``)

    Returns an empty list on any top-level failure.
    """
    try:
        # Strip markdown fences
        fence_match = _FENCE_RE.search(text)
        if fence_match:
            text = fence_match.group(1)

        # Grab first [...] block in case there is surrounding prose
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            return []

        raw_list = json.loads(text[start : end + 1])
        if not isinstance(raw_list, list):
            return []

        drafts: List[MemoryDraft] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            body_md = item.get("body_md")
            when_to_use = item.get("when_to_use")
            kind = item.get("kind")
            # All four fields must be non-empty strings
            if not all(
                isinstance(f, str) and f for f in (title, body_md, when_to_use, kind)
            ):
                continue
            # Clamp kind to the valid set
            if kind not in _VALID_KINDS:
                kind = "fact"
            drafts.append(
                MemoryDraft(
                    title=title,
                    body_md=body_md,
                    when_to_use=when_to_use,
                    kind=kind,
                )
            )
        return drafts
    except Exception:  # noqa: BLE001 — parse must never raise
        logger.debug("[agent_memory_consolidation] parse failed — returning []")
        return []


# ---------------------------------------------------------------------------
# Consolidate pair
# ---------------------------------------------------------------------------

_Consolidator = Callable[[str], Awaitable[str]]


async def consolidate_pair(
    *,
    owner_user_id: str,
    agent_id: str,
    scope: str,
    scope_id: str,
    recent_activity: str,
    existing_titles: List[str],
    existing_fingerprints: Set[str],
    consolidator: _Consolidator,
    max_entries: int,
) -> List[Tuple[MemoryDraft, str]]:
    """Run the full consolidation pipeline for one (owner, agent) pair.

    Steps
    -----
    1. Build the /dream prompt.
    2. Await ``consolidator(prompt)`` — the caller supplies the LLM callable.
    3. Parse the raw output into MemoryDraft objects.
    4. Drop any draft whose fingerprint is already in ``existing_fingerprints``.
    5. Cap the result list at ``max_entries``.

    Returns
    -------
    list of (MemoryDraft, fingerprint) tuples — empty on any failure.

    Never raises: all exceptions are caught and logged at DEBUG level.
    """
    try:
        prompt = build_consolidation_prompt(
            recent_activity=recent_activity,
            existing_titles=existing_titles,
        )
        raw = await consolidator(prompt)
        drafts = parse_consolidation_output(raw)

        result: List[Tuple[MemoryDraft, str]] = []
        for draft in drafts:
            fp = make_fingerprint(owner_user_id, agent_id, scope, scope_id, draft)
            if fp in existing_fingerprints:
                continue
            result.append((draft, fp))
            if len(result) >= max_entries:
                break

        return result
    except Exception:  # noqa: BLE001 — consolidation must never break callers
        logger.opt(exception=True).warning(
            f"[agent_memory_consolidation] consolidate_pair failed "
            f"for user={owner_user_id} agent={agent_id}"
        )
        return []


__all__ = [
    "MemoryDraft",
    "make_fingerprint",
    "build_consolidation_prompt",
    "parse_consolidation_output",
    "consolidate_pair",
]
