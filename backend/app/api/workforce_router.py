"""Workforce dashboard endpoints — read-only board for the M3 runtime.

Exposes one endpoint, ``GET /api/v1/workforce/board``, that returns
everything the frontend needs to render a Workforce overview without
making N round-trips: persistent agents + their worker rows + queue
counts (inbox unread/reading, outbox undelivered) + recent runs +
recent state transitions.

Why one fat endpoint instead of N small ones:
  * The dashboard polls every ~5s; one query keeps round-trips low.
  * The data is small (handful of agents × handful of recent rows).
  * The values are mostly derived counts — assembling client-side
    means duplicating SQL across the frontend.

Auth: any logged-in user can read the board. The data exposed
(persistent agents are system presets; queue counts are aggregates)
is not user-private and matches what's already visible via Runs/Usage
pages today.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from loguru import logger

from app.core.deps import get_current_user
from app.db.supabase_client import get_async_supabase_admin

router = APIRouter(prefix="/workforce", tags=["workforce"])


@router.get("/board")
async def get_workforce_board(
    _user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Aggregate snapshot for the Workforce dashboard.

    Returns:
        {
          "agents": [
            {
              "id": str, "slug": str, "name": str, "icon": str,
              "model": str, "persistent": bool,
              "worker": {state, current_task_id, state_changed_at, heartbeat_at}|None,
              "queue": {inbox_unread, inbox_reading, outbox_undelivered},
              "recent_runs": [{id, status, started_at, ended_at, cost_cents,
                               prompt_tokens, completion_tokens, trigger}, ...]
            }, ...
          ],
          "recent_state_history": [{agent_slug, from_state, to_state, trigger,
                                     changed_at, task_id}, ...]
        }
    """
    client = await get_async_supabase_admin()

    # 1. All persistent agents.
    agents_result = (
        await client.table("ai_agents")
        .select("id,slug,name,icon,model,persistent,paused_reason")
        .eq("persistent", True)
        .order("slug")
        .execute()
    )
    agents = agents_result.data or []
    if not agents:
        return {"agents": [], "recent_state_history": []}

    agent_ids = [a["id"] for a in agents]
    agent_id_to_slug = {a["id"]: a["slug"] for a in agents}

    # 2. Worker rows for those agents (one shot via .in_).
    workers_result = (
        await client.table("agent_workers")
        .select("agent_id,state,current_task_id,state_changed_at,heartbeat_at")
        .in_("agent_id", agent_ids)
        .execute()
    )
    workers_by_agent: dict[str, dict[str, Any]] = {
        w["agent_id"]: w for w in (workers_result.data or [])
    }

    # 3. Queue depths (one query per kind, range-filtered).
    inbox_unread_by_agent = await _count_inbox(client, agent_ids, "unread")
    inbox_reading_by_agent = await _count_inbox(client, agent_ids, "reading")
    outbox_undelivered_by_agent = await _count_outbox_undelivered(client, agent_ids)

    # 4. Recent runs per agent (top 5 each, one query — order + filter, then
    #    bucket client-side. Cap to ~50 total rows pulled.)
    runs_result = (
        await client.table("agent_runs")
        .select(
            "id,agent_id,status,trigger,started_at,ended_at,cost_cents,"
            "prompt_tokens,completion_tokens,model"
        )
        .in_("agent_id", agent_ids)
        .order("started_at", desc=True)
        .limit(50)
        .execute()
    )
    runs_by_agent: dict[str, list[dict[str, Any]]] = {aid: [] for aid in agent_ids}
    for run in runs_result.data or []:
        bucket = runs_by_agent.get(run["agent_id"])
        if bucket is not None and len(bucket) < 5:
            bucket.append(run)

    # 5. Assemble response per agent.
    response_agents: list[dict[str, Any]] = []
    for agent in agents:
        aid = agent["id"]
        response_agents.append(
            {
                "id": aid,
                "slug": agent["slug"],
                "name": agent.get("name") or agent["slug"],
                "icon": agent.get("icon"),
                "model": agent.get("model"),
                "persistent": bool(agent.get("persistent")),
                "paused_reason": agent.get("paused_reason"),
                "worker": workers_by_agent.get(aid),
                "queue": {
                    "inbox_unread": inbox_unread_by_agent.get(aid, 0),
                    "inbox_reading": inbox_reading_by_agent.get(aid, 0),
                    "outbox_undelivered": outbox_undelivered_by_agent.get(aid, 0),
                },
                "recent_runs": runs_by_agent.get(aid, []),
            }
        )

    # 6. Recent state transitions (across all persistent agents).
    history_result = (
        await client.table("agent_state_history")
        .select("agent_id,from_state,to_state,trigger,task_id,changed_at")
        .in_("agent_id", agent_ids)
        .order("changed_at", desc=True)
        .limit(20)
        .execute()
    )
    recent_history = [
        {
            "agent_slug": agent_id_to_slug.get(row["agent_id"], "?"),
            "from_state": row["from_state"],
            "to_state": row["to_state"],
            "trigger": row["trigger"],
            "task_id": row.get("task_id"),
            "changed_at": row["changed_at"],
        }
        for row in (history_result.data or [])
    ]

    return {
        "agents": response_agents,
        "recent_state_history": recent_history,
    }


async def _count_inbox(client, agent_ids: list[str], status: str) -> dict[str, int]:
    """Count inbox messages in ``status`` per recipient agent."""
    try:
        result = (
            await client.table("agent_inbox")
            .select("recipient_agent_id", count="exact")
            .in_("recipient_agent_id", agent_ids)
            .eq("status", status)
            .execute()
        )
        return _bucket_count(result.data or [], "recipient_agent_id")
    except Exception as err:
        logger.warning(f"[workforce] inbox count failed (status={status}): {err}")
        return {}


async def _count_outbox_undelivered(client, agent_ids: list[str]) -> dict[str, int]:
    """Count undelivered outbox rows per sender agent."""
    try:
        result = (
            await client.table("agent_outbox")
            .select("sender_agent_id")
            .in_("sender_agent_id", agent_ids)
            .eq("delivered", False)
            .execute()
        )
        return _bucket_count(result.data or [], "sender_agent_id")
    except Exception as err:
        logger.warning(f"[workforce] outbox count failed: {err}")
        return {}


def _bucket_count(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        v = row.get(key)
        if v is None:
            continue
        out[v] = out.get(v, 0) + 1
    return out
