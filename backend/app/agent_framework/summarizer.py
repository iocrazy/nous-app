"""LLM-driven head summarizer for context compaction (Phase 2 of #199).

Replaces ``ContextCompactor._emergency_cap`` (a lossy character-level
truncation) with a cheap LLM call that produces a paragraph summary of
older turns. The summary is dropped in as a single ``[role=system]``
message tagged ``[Earlier conversation summary]`` so the main agent can
see it but knows it isn't original content.

Cost / quality trade-off:
    Truncate (Phase 1)    → free, loses anything past first N chars
    Summarize (Phase 2)   → ~$0.05 / 50K input tokens via Haiku 4.5,
                            preserves intent + IDs / URLs / paths

Provider selection:
    - Read ``system_settings.compaction_provider`` (default
      ``claude-haiku-4-5``)
    - Route via ``ai.adapters.factory.get_adapter`` — the same path
      AgentRunner uses for main-agent calls, so a new model added there
      automatically works here too
    - Failures fall back to the caller's ``_emergency_cap`` truncation

Prompt invariant: the summary MUST list every URL, file path, and
ID-shaped token (≥ 8 digits, snowflake-like, UUID, etc.) that appeared
in the head. Without that, the main agent loses the ability to look up
files referenced in earlier turns.
"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

from loguru import logger

DEFAULT_COMPACTION_PROVIDER = "claude-haiku-4-5"

# How many tokens the summary itself can use. Capped to a few hundred
# so even a maximally verbose Haiku output doesn't blow the budget the
# compaction was supposed to free up.
DEFAULT_SUMMARY_MAX_TOKENS = 600

# Hard timeout: a slow/hung Haiku call must not block the main agent's
# turn. Below this cap we wait; above it we raise so the caller can
# fall back to the emergency cap path.
SUMMARIZE_TIMEOUT_S = 20.0

SUMMARIZE_SYSTEM_PROMPT = (
    "You compress old conversation turns into a faithful summary so an "
    "AI agent can keep working past its context window.\n"
    "\n"
    "RULES (in priority order):\n"
    "1. Preserve verbatim every URL, absolute file path, ID, model name, "
    "    code symbol, and any number ≥ 8 digits long. Quote them as-is.\n"
    "2. Preserve any tool call the agent made and the outcome (success / "
    "    error / value returned). Mention the tool name.\n"
    "3. Preserve the user's stated intent and any constraints they "
    "    mentioned (deadlines, must-not-do, preferences).\n"
    "4. Drop chit-chat, repeated explanations, and verbose tool output "
    "    that's already reflected in step (2).\n"
    "5. Output a single paragraph, ≤ 500 tokens. No bullet points, no "
    "    section headers, no preamble like 'Here's the summary:'."
)


async def _read_provider_setting() -> str:
    """Single source of truth for which model the compactor uses.

    Resolution order (first wins):
      1. ``COMPACTION_PROVIDER`` env var — for ad-hoc CI overrides
      2. ``system_settings.compaction_provider`` — admin-configurable
      3. ``DEFAULT_COMPACTION_PROVIDER`` — sane default

    Errors here are non-fatal: if Postgres is unreachable we fall back
    to the default rather than break the agent loop. Logs the failure
    so it's visible.
    """
    env_override = os.environ.get("COMPACTION_PROVIDER")
    if env_override:
        return env_override

    try:
        from app.db.supabase_client import get_async_supabase_admin

        client = await get_async_supabase_admin()
        result = await (
            client.table("system_settings")
            .select("value")
            .eq("key", "compaction_provider")
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if rows:
            # We only honor string values; admin who stores a JSON object /
            # bool here will silently fall through to the default. Keeps
            # the failure mode "use the safe default" rather than "crash
            # the agent loop" if a future migration changes value type.
            value = rows[0].get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
    except Exception as exc:
        logger.warning(
            "[summarizer] failed to read compaction_provider from "
            "system_settings, using default: {}",
            exc,
        )

    return DEFAULT_COMPACTION_PROVIDER


def _flatten_messages_for_summary(messages: list[dict]) -> str:
    """Render a messages list into a single text block the summarizer
    can consume. Tool calls and structured content get a one-line tag
    rather than the full JSON — the summary prompt only needs intent."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role") or "?"
        content = msg.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    t = part.get("type") or ""
                    if t == "text":
                        parts.append(str(part.get("text") or ""))
                    elif t == "tool_use":
                        parts.append(
                            f"[tool_use {part.get('name')!r} args={part.get('input')!r}]"
                        )
                    elif t == "tool_result":
                        parts.append(
                            f"[tool_result tool_use_id={part.get('tool_use_id')!r}]"
                        )
                    else:
                        parts.append(f"[{t}]")
                else:
                    parts.append(str(part))
            text = " ".join(p for p in parts if p)
        else:
            text = str(content or "")
        if msg.get("tool_calls"):
            text += " " + " ".join(
                f"[tool_call {c.get('function', {}).get('name')!r}]"
                for c in msg["tool_calls"]
            )
        lines.append(f"{role}: {text}")
    return "\n\n".join(lines)


async def summarize(
    messages: list[dict],
    *,
    provider_override: Optional[str] = None,
    max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS,
) -> str:
    """Compress ``messages`` into a single paragraph summary.

    Returns the summary text (NOT a wrapped message dict — caller
    decides how to embed it). Raises ``RuntimeError`` on any provider
    failure or timeout; caller is responsible for the emergency-cap
    fallback so context_compactor stays in control of the policy.
    """
    if not messages:
        return ""

    provider = provider_override or await _read_provider_setting()
    head_text = _flatten_messages_for_summary(messages)

    # Lazy imports — adapters pull in heavyweight SDKs we don't want
    # to load at module import time (this file is reachable from the
    # tokenizer hot path via context_compactor).
    from app.core.config import settings
    from app.schemas.ai_library import ComposedSystemPrompt
    from app.services.ai.adapters.factory import get_adapter

    try:
        adapter = get_adapter(provider, settings)
    except ValueError as exc:
        raise RuntimeError(
            f"[summarizer] unsupported provider {provider!r}: {exc}"
        ) from exc

    # ComposedSystemPrompt was designed for full agents (cache fingerprints,
    # skill manifest, agent UUID). For a one-shot summary call we need a
    # minimal-valid envelope — stub UUIDs / fingerprints / empty arrays.
    from uuid import UUID

    composed = ComposedSystemPrompt(
        agent_id=UUID(int=0),
        agent_slug="__compaction_summarizer__",
        model=provider,
        temperature=0.0,
        max_tokens=max_tokens,
        system_message=SUMMARIZE_SYSTEM_PROMPT,
        tools=[],
        skill_manifest=[],
        cache_fingerprint="",
    )
    user_message = {
        "role": "user",
        "content": (
            "Summarize the following conversation, preserving the rules in "
            "the system prompt:\n\n" + head_text
        ),
    }

    try:
        resp = await asyncio.wait_for(
            adapter.call(composed, [user_message]),
            timeout=SUMMARIZE_TIMEOUT_S,
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"[summarizer] {provider} timed out after {SUMMARIZE_TIMEOUT_S}s"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"[summarizer] {provider} call failed: {exc}"
        ) from exc

    text = (resp or {}).get("content") or ""
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError(
            f"[summarizer] {provider} returned empty content; resp shape={list((resp or {}).keys())}"
        )

    return text.strip()


__all__ = [
    "DEFAULT_COMPACTION_PROVIDER",
    "DEFAULT_SUMMARY_MAX_TOKENS",
    "SUMMARIZE_SYSTEM_PROMPT",
    "SUMMARIZE_TIMEOUT_S",
    "summarize",
]
