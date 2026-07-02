"""Group-agent conversation memory (Phase 1.5).

Read side — build_memory_block(): rolling summary (conversation_memory) +
agent_memory recall, rendered as a markdown block the agent turn appends to
its request instructions. Write side — maybe_compact(): after agent turns,
summarize the un-summarized head (keeping the newest COMPACT_KEEP_TAIL
messages verbatim, since recent_messages already feeds those to the turn)
via a cheap LLM, and advance last_seq_summarized.

Privacy (user decision 2026-07-02): group recall reuses the 1:1 shape —
summoner-owned OR team-shared memories. The summoner @-mentioned the agent;
that is treated as consent for their own memories to inform a group-visible
reply.

Every public function is flag-gated on FEATURE_GROUP_AGENT_MEMORY and NEVER
raises — a memory failure must not break a summon.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.core.config import settings
from app.repositories.conversation_memory_repository import (
    get_conversation_memory_repository,
)
from app.repositories.conversation_repository import get_conversation_repository
from app.services.ai.memory.agent_memory import (  # noqa: F401 (re-exported for tests: svc.MemoryHit)
    MemoryContext,
    MemoryHit,
    recall,
)

COMPACT_TRIGGER = 30  # un-summarized messages (beyond the tail) that trigger compaction
COMPACT_KEEP_TAIL = 20  # newest messages stay verbatim (= recent_messages window)
_SUMMARY_MODEL = "qwen-turbo"
_SUMMARY_MAX_TOKENS = 700

_SUMMARY_SYSTEM = (
    "You maintain a rolling summary of a team chat conversation. Merge the "
    "PREVIOUS SUMMARY with the NEW MESSAGES into one concise markdown summary "
    "(<= 300 words): decisions, open questions, facts, who said what that "
    "still matters. Drop chit-chat. Output ONLY the summary markdown."
)


def _render_line(msg: dict[str, Any]) -> str:
    body = msg.get("body") or {}
    mtype = msg.get("type", "text")
    if mtype == "text":
        content = str(body.get("text", ""))
    else:
        content = f"[{mtype}]"
    return f"[{msg.get('sender_type', 'user')}] {content}"


async def _fresh_conversation(conversation_id: int) -> Optional[dict[str, Any]]:
    """Re-read the conversation for a fresh last_seq (the dict the summon path
    holds was loaded before the turn wrote replies)."""
    return await get_conversation_repository().get_conversation(
        conversation_id=conversation_id
    )


async def _summarize(previous: str, transcript: str) -> str:
    """Cheap-LLM rolling summary. '' on any failure / missing key
    (precedent: _harvest_summarizer in ai_library_chat_service)."""
    try:
        from uuid import uuid4

        from app.schemas.ai_library import ComposedSystemPrompt
        from app.services.ai.providers.ai_provider import QwenAdapter

        api_key = getattr(settings, "DASHSCOPE_API_KEY", None) or getattr(
            settings, "QWEN_API_KEY", None
        )
        if not api_key:
            logger.info("[conv_memory] no qwen key — skipping compaction")
            return ""
        adapter = QwenAdapter(api_key=api_key, model=_SUMMARY_MODEL)
        cs = ComposedSystemPrompt(
            agent_id=uuid4(),
            agent_slug="conversation_compactor",
            model=_SUMMARY_MODEL,
            temperature=0.0,
            max_tokens=_SUMMARY_MAX_TOKENS,
            system_message=_SUMMARY_SYSTEM,
            tools=[],
            skill_manifest=[],
            cache_fingerprint="conversation_compactor_v1",
        )
        prompt = (
            f"PREVIOUS SUMMARY:\n{previous or '(none)'}\n\n"
            f"NEW MESSAGES:\n{transcript}"
        )
        resp = await adapter.call(cs, [{"role": "user", "content": prompt}])
        return (resp.get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[conv_memory] summarize failed: {exc!r}")
        return ""


async def build_memory_block(
    *,
    conversation: dict[str, Any],
    user_query: str,
    summoner_user_id: str,
    agent: dict[str, Any],
) -> str:
    if not settings.FEATURE_GROUP_AGENT_MEMORY:
        return ""
    try:
        cid = int(conversation["id"])
        scope_id = int(conversation["scope_id"])
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(f"[conv_memory] malformed conversation dict: {exc!r}")
        return ""
    parts: list[str] = []
    try:
        mem = await get_conversation_memory_repository().load(cid)
        if mem and (mem.get("summary_md") or "").strip():
            parts.append(
                "## Conversation summary (older messages)\n" + mem["summary_md"]
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[conv_memory] summary load failed conv={cid}: {exc!r}")
    try:
        if settings.FEATURE_AGENT_MEMORY:
            ctx = MemoryContext(
                user_id=summoner_user_id,
                team_ids=(scope_id,),
                agent_id=str(agent.get("id")) if agent.get("id") else None,
            )
            hits = await recall(ctx, user_query, limit=5)
            if hits:
                parts.append(
                    "## Relevant memories\n"
                    + "\n".join(f"- {h.kind}: {h.title} — {h.body_md}" for h in hits)
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[conv_memory] recall failed conv={cid}: {exc!r}")
    return "\n\n".join(parts)


async def maybe_compact(
    *, conversation: dict[str, Any], agent_id: Optional[str] = None
) -> None:
    if not settings.FEATURE_GROUP_AGENT_MEMORY:
        return
    try:
        cid = int(conversation["id"])
        fresh = await _fresh_conversation(cid)
        if not fresh:
            return
        last_seq = int(fresh.get("last_seq") or 0)
        mem = await get_conversation_memory_repository().load(cid)
        done = int(mem["last_seq_summarized"]) if mem else 0
        if last_seq - done < COMPACT_TRIGGER + COMPACT_KEEP_TAIL:
            return
        to_seq = last_seq - COMPACT_KEEP_TAIL
        msgs = await get_conversation_repository().messages_in_range(
            conversation_id=cid, from_seq=done + 1, to_seq=to_seq
        )
        if not msgs:
            return
        transcript = "\n".join(_render_line(m) for m in msgs)
        previous = (mem or {}).get("summary_md") or ""
        summary = await _summarize(previous, transcript)
        if not summary:
            return
        await get_conversation_memory_repository().upsert(
            conversation_id=cid,
            summary_md=summary,
            last_seq_summarized=to_seq,
            model=_SUMMARY_MODEL,
        )
        logger.info(f"[conv_memory] compacted conv={cid} through seq={to_seq}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[conv_memory] compaction failed: {exc!r}")
