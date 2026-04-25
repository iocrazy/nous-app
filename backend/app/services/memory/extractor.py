"""Memory extractors — dual-prompt isolation (Mem Zero pattern).

Two separate extractors so the agent's self-narration never pollutes
user-facing memory:

- :class:`UserMemoryExtractor`  — looks at user messages only. Captures
  preferences, facts about the user, corrections they made, decisions
  they expressed.
- :class:`AssistantMemoryExtractor` — looks at assistant messages only.
  Captures the agent's own working notes about what it has done /
  produced for this user. Different prompt, different scope.

The split prevents "I explained the system to the user" from being
written as a fact about the user. It also lets each extractor have a
prompt tuned to its job rather than a single mega-prompt.

Output contract: each extractor returns 0..N :class:`ExtractedFact`
records. Empty list = "nothing worth saving this turn", which is the
common case. The Celery task wrapping these (write_memory_task) will
embed and persist whatever they produce.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.services.memory import ExtractedFrom

logger = logging.getLogger(__name__)


# Caller wires a real LLM call (cheap model — Qwen-Turbo / GPT-4o-mini).
# Returns raw model text. Extractors parse the JSON themselves so we can
# tighten the prompt without changing the wiring.
LLMCall = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class ExtractedFact:
    """One memory candidate produced by an extractor."""

    summary: str  # The fact, in 1 short sentence
    when_to_use: str  # When this fact would help — used for embedding
    extracted_from: ExtractedFrom


_USER_EXTRACTION_PROMPT = """You read recent USER messages from a chat with an AI agent
and extract durable facts worth remembering across sessions.

DO extract:
- Preferences ("I prefer X over Y")
- Stable facts about the user (name, role, project context)
- Style guidance the user expressed
- Corrections the user made to the agent

DO NOT extract:
- One-off task content ("write me a script about cats")
- Ephemeral context that only matters this turn
- Anything the user just asked the agent to do (that's a task, not a memory)

Return STRICT JSON: {"facts": [{"summary": "...", "when_to_use": "..."}, ...]}
"facts" may be []. Each fact:
  summary: 1 sentence stating the fact (third person, e.g. "User prefers...")
  when_to_use: 1 sentence stating the situation in which recalling this fact would help

USER MESSAGES:
{messages}

JSON only, no explanation:"""


_ASSISTANT_EXTRACTION_PROMPT = """You read recent ASSISTANT messages from a chat between
a user and an AI agent. Extract working notes the AGENT should remember next time it
picks up similar work.

DO extract:
- Decisions the agent made (tone choices, structural choices)
- Patterns that worked (or didn't) for this user
- Conventions the agent established for this user's project

DO NOT extract:
- Facts about the user (those are captured separately)
- One-off output content
- The user's questions (extract those from user channel)

Return STRICT JSON: {"facts": [{"summary": "...", "when_to_use": "..."}, ...]}
"facts" may be []. Each fact: 1-sentence summary + 1-sentence when_to_use.

ASSISTANT MESSAGES:
{messages}

JSON only, no explanation:"""


@dataclass(frozen=True)
class _BaseExtractor:
    llm_call: LLMCall

    async def _run(self, prompt: str, source: ExtractedFrom) -> list[ExtractedFact]:
        try:
            raw = await self.llm_call(prompt)
        except Exception:  # noqa: BLE001 — extractor failures are non-fatal
            logger.exception("[memory.extractor] LLM call failed; returning []")
            return []

        return _parse_facts(raw, source)


@dataclass(frozen=True)
class UserMemoryExtractor(_BaseExtractor):
    """Extracts facts from user messages only."""

    async def extract(self, user_messages: list[str]) -> list[ExtractedFact]:
        if not user_messages:
            return []
        joined = _format_message_list(user_messages)
        prompt = _USER_EXTRACTION_PROMPT.replace("{messages}", joined)
        return await self._run(prompt, ExtractedFrom.USER_MSG)


@dataclass(frozen=True)
class AssistantMemoryExtractor(_BaseExtractor):
    """Extracts working notes from assistant messages only."""

    async def extract(self, assistant_messages: list[str]) -> list[ExtractedFact]:
        if not assistant_messages:
            return []
        joined = _format_message_list(assistant_messages)
        prompt = _ASSISTANT_EXTRACTION_PROMPT.replace("{messages}", joined)
        return await self._run(prompt, ExtractedFrom.ASSISTANT_MSG)


def _format_message_list(messages: list[str]) -> str:
    """Render a numbered list of messages, truncating each to keep prompt small."""
    parts: list[str] = []
    for i, msg in enumerate(messages, start=1):
        truncated = msg[:1000] + ("..." if len(msg) > 1000 else "")
        parts.append(f"[{i}] {truncated}")
    return "\n".join(parts)


def _parse_facts(raw: str, source: ExtractedFrom) -> list[ExtractedFact]:
    """Parse the JSON-only response. Tolerant: returns [] on any malformation."""
    text = raw.strip()
    # Some models wrap JSON in markdown fences. Strip them.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(
            "[memory.extractor] non-JSON response; dropping. raw=%r", raw[:200]
        )
        return []

    facts_raw = payload.get("facts")
    if not isinstance(facts_raw, list):
        return []

    out: list[ExtractedFact] = []
    for item in facts_raw:
        if not isinstance(item, dict):
            continue
        summary = (item.get("summary") or "").strip()
        when = (item.get("when_to_use") or "").strip()
        if not summary or not when:
            continue
        out.append(
            ExtractedFact(
                summary=summary[:500],  # hard cap
                when_to_use=when[:500],
                extracted_from=source,
            )
        )
    return out


__all__ = [
    "AssistantMemoryExtractor",
    "ExtractedFact",
    "LLMCall",
    "UserMemoryExtractor",
]
