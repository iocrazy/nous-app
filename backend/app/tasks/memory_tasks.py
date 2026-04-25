"""Celery task: write_memory_task.

Triggered by the MemoryHarvester PostToolUse hook (M1.A skeleton). The
hook returns a Celery signature that, when ``.delay()``-ed by AgentRunner,
runs this task off the chat path.

The task itself does no parsing of conversation context — it pulls the
most recent user/assistant messages for the given session_id from
ai_messages, then hands them to MemoryWriter.

Failures are non-fatal — agent_runs.cost_cents and ai_messages already
captured everything the user can see; missing memories degrade the
NEXT chat, not the current one.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from uuid import UUID

from loguru import logger

from app.celery_app import celery


# How many recent turns we feed each extractor. Cap is a cost control —
# extractor prompts grow linearly with this. M1.B default is 10; tune once
# we see real usage.
_RECENT_TURNS_PER_CHANNEL = 10


@celery.task(bind=True, name="memory.write_memory_task", max_retries=2)
def write_memory_task(
    self,
    *,
    run_id: Optional[str] = None,
    agent_id: str,
    user_id: str,
    session_id: Optional[str] = None,
    iteration: int = 0,
    tool_name: str = "",
) -> dict:
    """Sync Celery entrypoint that runs the async writer.

    Returns a small dict with row counts for observability. Errors get
    re-raised after the retry budget is gone — Celery will then move
    the task to the dead-letter queue (configured at the broker level).
    """
    try:
        return asyncio.run(
            _async_write_memory(
                run_id=UUID(run_id) if run_id else None,
                agent_id=UUID(agent_id),
                user_id=UUID(user_id),
                session_id=UUID(session_id) if session_id else None,
                iteration=iteration,
                tool_name=tool_name,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[memory_tasks] write_memory_task failed; will retry")
        raise self.retry(exc=exc, countdown=30)


async def _async_write_memory(
    *,
    run_id: Optional[UUID],
    agent_id: UUID,
    user_id: UUID,
    session_id: Optional[UUID],
    iteration: int,
    tool_name: str,
) -> dict:
    # Defer imports to keep Celery worker startup light.
    from app.db import get_async_supabase_admin
    from app.services.embedding_service import EmbeddingService
    from app.services.memory.extractor import (
        AssistantMemoryExtractor,
        UserMemoryExtractor,
    )
    from app.services.memory.writer import MemoryWriter

    if session_id is None:
        logger.debug("[memory_tasks] no session_id; nothing to extract")
        return {"rows_written": 0, "reason": "no_session_id"}

    client = await get_async_supabase_admin()

    user_msgs, asst_msgs = await _load_recent_messages(client, session_id)
    if not user_msgs and not asst_msgs:
        return {"rows_written": 0, "reason": "no_messages"}

    # Wire the extractors with a cheap LLM call. Importing here avoids
    # forcing every Celery worker to load the adapters at startup.
    llm_call = await _build_cheap_llm_call()
    user_extractor = UserMemoryExtractor(llm_call=llm_call)
    asst_extractor = AssistantMemoryExtractor(llm_call=llm_call)

    writer = MemoryWriter(
        user_extractor=user_extractor,
        assistant_extractor=asst_extractor,
        embedding_service=EmbeddingService(),
        supabase_client=client,
    )

    rows = await writer.write(
        agent_id=agent_id,
        user_id=user_id,
        run_id=run_id,
        user_messages=user_msgs,
        assistant_messages=asst_msgs,
    )
    logger.info(
        "[memory_tasks] wrote %d memory rows for agent=%s user=%s session=%s",
        rows,
        agent_id,
        user_id,
        session_id,
    )
    return {"rows_written": rows}


async def _load_recent_messages(
    client, session_id: UUID
) -> tuple[list[str], list[str]]:
    """Pull last N user msgs and last N assistant msgs from ai_messages."""
    result = (
        await client.table("ai_messages")
        .select("role, content")
        .eq("session_id", str(session_id))
        .order("created_at", desc=True)
        .limit(_RECENT_TURNS_PER_CHANNEL * 4)  # 4x to oversample then split
        .execute()
    )
    rows = result.data or []
    user_msgs: list[str] = []
    asst_msgs: list[str] = []
    for row in rows:
        role = row.get("role")
        content = row.get("content") or ""
        if role == "user" and len(user_msgs) < _RECENT_TURNS_PER_CHANNEL:
            user_msgs.append(content)
        elif role == "assistant" and len(asst_msgs) < _RECENT_TURNS_PER_CHANNEL:
            asst_msgs.append(content)
    # Re-chronological for prompt readability
    return list(reversed(user_msgs)), list(reversed(asst_msgs))


async def _build_cheap_llm_call():
    """Closure over a cheap-model adapter for extractor LLM calls.

    Defaults to Qwen-Turbo (cheapest model in our adapter set). Wire-up
    is deferred to keep this importable in unit tests without env vars.
    """
    from app.core.config import settings
    from app.services.ai_adapters import get_adapter
    from app.schemas.ai_library import ComposedSystemPrompt
    from uuid import UUID as _UUID

    cheap_model = "qwen-turbo"
    adapter = get_adapter(cheap_model, settings)

    composed = ComposedSystemPrompt(
        agent_id=_UUID(int=0),
        agent_slug="memory_extractor",
        model=cheap_model,
        temperature=0.0,
        max_tokens=1024,
        system_message="You extract facts to remember from chat history. Output JSON.",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="memory_extract_v1",
    )

    async def _call(prompt: str) -> str:
        resp = await adapter.call(composed, [{"role": "user", "content": prompt}])
        return resp["choices"][0]["message"].get("content") or ""

    return _call


__all__ = ["write_memory_task"]
