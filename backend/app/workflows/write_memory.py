"""write_memory DBOS workflow — port of legacy
`memory_tasks.write_memory_task`.

Note: the _DEFERRED_TASKS.md ledger marked this as "multiple sub-tasks"
but the actual file has a single Celery task with two phases — message
load and extract/persist. We port it as one workflow with two steps.

Failures are non-fatal at the chat path level (the user already saw
their reply via agent_runs/ai_messages); a missing memory degrades the
NEXT chat, not the current one. DBOS retry policy is conservative
(max_attempts=2) to match the Celery `max_retries=2` budget.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from uuid import UUID

from dbos import DBOS
from loguru import logger


_RECENT_TURNS_PER_CHANNEL = 10


@DBOS.step()
def load_recent_messages_step(session_id: str) -> dict[str, list[str]]:
    """Pull last N user + N assistant messages from ai_messages."""
    from app.db import get_async_supabase_admin

    async def _load() -> dict[str, list[str]]:
        client = await get_async_supabase_admin()
        result = (
            await client.table("ai_messages")
            .select("role, content")
            .eq("session_id", session_id)
            .order("created_at", desc=True)
            .limit(_RECENT_TURNS_PER_CHANNEL * 4)
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
        return {
            "user_msgs": list(reversed(user_msgs)),
            "asst_msgs": list(reversed(asst_msgs)),
        }

    return asyncio.run(_load())


@DBOS.step(retries_allowed=True, max_attempts=2)
def extract_and_persist_memories_step(
    *,
    run_id: Optional[str],
    agent_id: str,
    user_id: str,
    user_msgs: list[str],
    asst_msgs: list[str],
) -> dict[str, Any]:
    """Run extractor LLM calls + persist memory rows. workflow_id
    memoization keeps replay safe (same run_id+inputs = same rows)."""
    from app.db import get_async_supabase_admin
    from app.services.embedding_service import EmbeddingService
    from app.services.memory.extractor import (
        AssistantMemoryExtractor,
        UserMemoryExtractor,
    )
    from app.services.memory.writer import MemoryWriter

    async def _do() -> dict[str, Any]:
        client = await get_async_supabase_admin()
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
            agent_id=UUID(agent_id),
            user_id=UUID(user_id),
            run_id=UUID(run_id) if run_id else None,
            user_messages=user_msgs,
            assistant_messages=asst_msgs,
        )
        return {"rows_written": rows}

    result = asyncio.run(_do())
    logger.info(
        f"[write_memory] wrote {result['rows_written']} rows "
        f"agent={agent_id} user={user_id}"
    )
    return result


async def _build_cheap_llm_call():
    """Cheap-model extractor LLM closure. Mirrors memory_tasks._build_cheap_llm_call."""
    from app.core.config import settings
    from app.schemas.ai_library import ComposedSystemPrompt
    from app.services.ai_adapters import get_adapter

    cheap_model = "qwen-turbo"
    adapter = get_adapter(cheap_model, settings)

    composed = ComposedSystemPrompt(
        agent_id=UUID(int=0),
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


@DBOS.workflow()
def write_memory_workflow(
    *,
    agent_id: str,
    user_id: str,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    iteration: int = 0,
    tool_name: str = "",
) -> dict[str, Any]:
    """DBOS port of write_memory_task.

    All ID args are str (UUID JSON-encoded) for DBOS serializer
    compatibility. Conversion to UUID happens inside the step.

    `iteration` and `tool_name` are kept on the boundary for parity
    with the Celery hook signature; not used downstream yet.
    """
    if not session_id:
        return {"rows_written": 0, "reason": "no_session_id"}

    msgs = load_recent_messages_step(session_id)
    if not msgs["user_msgs"] and not msgs["asst_msgs"]:
        return {"rows_written": 0, "reason": "no_messages"}

    return extract_and_persist_memories_step(
        run_id=run_id,
        agent_id=agent_id,
        user_id=user_id,
        user_msgs=msgs["user_msgs"],
        asst_msgs=msgs["asst_msgs"],
    )
