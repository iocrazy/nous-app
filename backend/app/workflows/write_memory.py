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

from typing import Any, Optional

from dbos import DBOS
from loguru import logger

_RECENT_TURNS_PER_CHANNEL = 10


@DBOS.step()
async def load_recent_messages_step(session_id: str) -> dict[str, list[str]]:
    """Pull last N user + N assistant messages from ai_messages."""
    from app.db import engine as db_engine

    rows = await db_engine.fetch_all(
        "SELECT role, content FROM public.ai_messages WHERE session_id = :sid "
        "ORDER BY created_at DESC LIMIT :lim",
        # session_id is a BIGINT snowflake (mig 232) carried as str — asyncpg
        # rejects str binds on int8 ('str' object cannot be interpreted ...).
        {"sid": int(session_id), "lim": _RECENT_TURNS_PER_CHANNEL * 4},
    )
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


def _build_turn_episode(
    user_msgs: list[str], asst_msgs: list[str], *, max_chars: int = 6000
) -> str:
    """Render the MOST RECENT user message as a graph episode body.

    Only the user's message is ingested. Including the assistant's reply
    makes Graphiti extract facts ABOUT THE ASSISTANT ("the assistant
    congratulated the user", "the assistant described Vue as solid"),
    which pollute the user's memory graph with non-durable noise (~5 of 7
    extracted facts in prod validation were assistant-side). ``asst_msgs``
    is accepted (the caller passes the full turn) but deliberately
    excluded. The "user:" prefix is kept so Graphiti anchors first-person
    facts to a "user" entity.

    An episode ingested per turn must only carry the new turn, otherwise
    the graph re-ingests the same exchanges N times. Empty when there is
    no new user message.
    """
    if not user_msgs:
        return ""
    latest = user_msgs[-1].strip()
    if not latest:
        return ""
    return f"user: {latest}"[:max_chars]


async def _write_graph_episode(
    *,
    user_id: str,
    session_id: str,
    run_id: Optional[str],
    iteration: int,
    user_msgs: list[str],
    asst_msgs: list[str],
) -> bool:
    """Flag-gated Graphiti dual-write (Phase 4 M2). Plain function so
    tests can exercise it without a DBOS workflow context.

    Failures never propagate — the service swallows them and this
    returns False; a missed episode degrades future recall, never the
    current chat.
    """
    from app.services.ai.memory.graph_memory import get_graph_memory_service

    service = get_graph_memory_service()
    if not service.config.enabled:
        return False
    body = _build_turn_episode(user_msgs, asst_msgs)
    if not body:
        return False
    return await service.add_chat_episode(
        group_id=f"user-{user_id}",
        name=f"chat-{session_id}-{run_id or iteration}",
        body=body,
        source_description="mediahub chat turn",
    )


@DBOS.step()
async def write_graph_episode_step(
    *,
    user_id: str,
    session_id: str,
    run_id: Optional[str],
    iteration: int,
    user_msgs: list[str],
    asst_msgs: list[str],
) -> dict[str, Any]:
    """Thin DBOS wrapper around ``_write_graph_episode``. No retries:
    a missed episode degrades future recall, never the current chat,
    and double-ingesting on retry pollutes the graph instead."""
    written = await _write_graph_episode(
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        iteration=iteration,
        user_msgs=user_msgs,
        asst_msgs=asst_msgs,
    )
    return {"episode_written": written}


async def _write_honcho_turn(
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
    user_msgs: list[str],
    asst_msgs: list[str],
) -> bool:
    """Flag-gated Honcho dual-write (Phase 4 M5). Plain function so
    tests can exercise it without a DBOS workflow context.

    Only the latest exchange is posted — Honcho's deriver accumulates
    per-peer representations server-side, so re-sending the rolling
    window would duplicate every turn N times.
    """
    from app.services.ai.memory.honcho_memory import get_honcho_memory_service
    from app.services.ai.memory.memory_prefs import get_memory_prefs

    service = get_honcho_memory_service()
    if not service.config.enabled:
        return False
    # Per-user toggle (Claude-style "learn from my chats"). Checked after
    # the global flag so disabled deployments never pay the settings read.
    prefs = await get_memory_prefs(user_id)
    if not prefs.learn:
        return False
    user_message = user_msgs[-1] if user_msgs else ""
    assistant_message = asst_msgs[-1] if asst_msgs else ""
    if not (user_message.strip() or assistant_message.strip()):
        return False
    workspace_id = await _resolve_team_workspace(session_id)
    return await service.add_chat_turn(
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        user_message=user_message,
        assistant_message=assistant_message,
        workspace_id=workspace_id,
    )


async def _resolve_team_workspace(session_id: str) -> Optional[str]:
    """Map the session's team to a Honcho workspace (Workspace=team).

    Returns ``team-{team_id}`` when the session carries team context,
    None (→ deployment default workspace) when it doesn't or the
    lookup fails — never blocks the write on a metadata read.
    """
    try:
        from app.db import engine as db_engine

        row = await db_engine.fetch_one(
            "SELECT team_id FROM public.ai_sessions WHERE id = :sid",
            # BIGINT snowflake carried as str — coerce for asyncpg (mig 232)
            {"sid": int(session_id)},
        )
        team_id = row.get("team_id") if row else None
        return f"team-{team_id}" if team_id else None
    except Exception:  # noqa: BLE001
        logger.warning(
            "[write_memory] team lookup failed for session %s; "
            "falling back to default workspace",
            session_id,
        )
        return None


@DBOS.step()
async def write_honcho_turn_step(
    *,
    user_id: str,
    agent_id: str,
    session_id: str,
    user_msgs: list[str],
    asst_msgs: list[str],
) -> dict[str, Any]:
    """Thin DBOS wrapper around ``_write_honcho_turn``. No retries —
    same reasoning as the graph step: a missed turn degrades future
    recall only, and a retry double-ingests."""
    written = await _write_honcho_turn(
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        user_msgs=user_msgs,
        asst_msgs=asst_msgs,
    )
    return {"honcho_written": written}


@DBOS.workflow()
async def write_memory_workflow(
    *,
    agent_id: str,
    user_id: str,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    iteration: int = 0,
    tool_name: str = "",
) -> dict[str, Any]:
    """DBOS dual-write workflow: Graphiti (temporal graph) + Honcho (user
    model). The home-grown L1 ``agent_memories`` layer has been removed —
    these two layers are the durable memory now.

    All ID args are str (UUID JSON-encoded) for DBOS serializer
    compatibility.

    `iteration` and `tool_name` are kept on the boundary for parity
    with the Celery hook signature; not used downstream yet.
    """
    if not session_id:
        return {"reason": "no_session_id"}

    msgs = await load_recent_messages_step(session_id)
    if not msgs["user_msgs"] and not msgs["asst_msgs"]:
        return {"reason": "no_messages"}

    result: dict[str, Any] = {}

    # Phase 4 M2: Graphiti dual-write. Cheap flag check here skips the step
    # entirely for the (default) disabled case.
    from app.services.ai.memory.graph_memory import get_graph_memory_service

    if await get_graph_memory_service().is_enabled():
        graph = await write_graph_episode_step(
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            iteration=iteration,
            user_msgs=msgs["user_msgs"],
            asst_msgs=msgs["asst_msgs"],
        )
        result = {**result, **graph}

    # Phase 4 M5: Honcho user-model dual-write — same contract as the
    # graph step (flag-gated, failures stay in the step).
    from app.services.ai.memory.honcho_memory import get_honcho_memory_service

    if get_honcho_memory_service().config.enabled:
        honcho = await write_honcho_turn_step(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            user_msgs=msgs["user_msgs"],
            asst_msgs=msgs["asst_msgs"],
        )
        result = {**result, **honcho}

    return result
