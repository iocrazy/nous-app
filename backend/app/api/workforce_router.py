"""Workforce dashboard endpoints.

Read paths:
  ``GET /api/v1/workforce/board`` — fat aggregate snapshot

Admin actions (I milestone):
  ``POST /api/v1/workforce/agents/{slug}/pause`` — set paused_reason
  ``POST /api/v1/workforce/agents/{slug}/resume`` — clear paused_reason
  ``POST /api/v1/workforce/agents/{slug}/clear-inbox`` — bulk-dismiss
  ``POST /api/v1/workforce/tasks/{task_id}/cancel`` — request task cancel

Auth: any logged-in user. The persistent agents are system presets
(no user_id) and the dashboard data is aggregate, not user-private.
This matches the Runs / Usage UIs today.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from app.core.deps import get_current_user
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.agent_repository import AgentRepository
from app.repositories.agent_workforce_repository import AgentWorkforceRepository

router = APIRouter(prefix="/workforce", tags=["workforce"])


# ─── shared helpers ────────────────────────────────────────────────────


async def _resolve_persistent_agent(slug: str) -> dict[str, Any]:
    """Look up a persistent agent by slug. Raises 404 / 400 cleanly so
    the admin-action endpoints don't have to repeat the boilerplate."""
    repo = AgentRepository()
    agent = await repo.get_by_slug(slug)
    if not agent:
        raise HTTPException(status_code=404, detail=f"agent '{slug}' not found")
    if not agent.get("persistent"):
        raise HTTPException(
            status_code=400,
            detail=f"agent '{slug}' is not a persistent worker",
        )
    return agent


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


# ─── admin actions (I milestone) ───────────────────────────────────────


class PauseAgentBody(BaseModel):
    reason: Optional[str] = None  # caller-supplied; default 'manual'


@router.post("/agents/{slug}/pause")
async def pause_agent(
    slug: str,
    body: PauseAgentBody = PauseAgentBody(),
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Set ``paused_reason`` so RunRecorder.start refuses new runs.

    In-flight turns are NOT killed — they finish naturally. The pause
    only blocks NEW runs (chat or workforce) from starting.
    """
    agent = await _resolve_persistent_agent(slug)
    reason = (body.reason or "manual").strip()[:120]
    repo = AgentRepository()
    await repo.update_fields(UUID(agent["id"]), {"paused_reason": reason})
    logger.info(f"[workforce] agent '{slug}' paused (reason={reason}) by user")
    return {"slug": slug, "paused_reason": reason, "status": "paused"}


@router.post("/agents/{slug}/resume")
async def resume_agent(
    slug: str,
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Clear ``paused_reason`` so the agent accepts new runs again."""
    agent = await _resolve_persistent_agent(slug)
    repo = AgentRepository()
    await repo.update_fields(UUID(agent["id"]), {"paused_reason": None})
    logger.info(f"[workforce] agent '{slug}' resumed by user")
    return {"slug": slug, "paused_reason": None, "status": "resumed"}


@router.post("/agents/{slug}/clear-inbox")
async def clear_inbox(
    slug: str,
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Bulk-dismiss every unread/reading inbox row for this agent.

    Use when the queue gets stuck (mis-routed messages, runaway tests).
    Doesn't touch already-processed rows. Returns the count cleared.
    """
    agent = await _resolve_persistent_agent(slug)
    client = await get_async_supabase_admin()
    cleared = 0
    try:
        result = (
            await client.table("agent_inbox")
            .update({"status": "dismissed", "processed_at": "now()"})
            .eq("recipient_agent_id", agent["id"])
            .in_("status", ["unread", "reading"])
            .execute()
        )
        cleared = len(result.data or [])
    except Exception as err:
        logger.exception(f"[workforce] clear-inbox failed for {slug}: {err}")
        raise HTTPException(status_code=500, detail="clear-inbox failed")

    logger.info(f"[workforce] agent '{slug}' inbox cleared ({cleared} rows)")
    return {"slug": slug, "cleared": cleared}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: UUID,
    user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Mark an in-flight or queued task as cancelled.

    The DB transition is the source of truth — once
    ``lifecycle_status='cancelled'``, the worker checks (and the
    RunRecorder cancel poll) will refuse to keep going. Already-done
    tasks are left alone.
    """
    workforce = AgentWorkforceRepository()
    task = await workforce.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    if task.get("lifecycle_status") in ("done", "failed", "cancelled"):
        return {
            "task_id": str(task_id),
            "lifecycle_status": task["lifecycle_status"],
            "note": "task already in terminal state — no-op",
        }
    try:
        await workforce.update_task_status(
            task_id=task_id,
            lifecycle_status="cancelled",
            error_code="user_cancel",
            error_message="Cancelled via workforce admin endpoint",
        )
    except Exception as err:
        logger.exception(f"[workforce] cancel-task failed for {task_id}: {err}")
        raise HTTPException(status_code=500, detail="cancel failed")

    logger.info(f"[workforce] task {task_id} cancelled by user")
    return {"task_id": str(task_id), "lifecycle_status": "cancelled"}
