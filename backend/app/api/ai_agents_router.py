# backend/app/api/ai_agents_router.py

"""
AI Agent API Router — agents, sessions, chat, and usage endpoints.

Route groups:
  /ai/agents              — CRUD for AI agents (preset + custom)
  /ai/agents/{id}/call   — Single agent invocation
  /ai/sessions            — Session lifecycle
  /ai/sessions/{id}/chat — Chat within an existing session
  /ai/usage               — Token usage statistics
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger

from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.schemas.ai import (
    AgentCallRequest,
    AgentCreate,
    AgentOut,
    AgentUpdate,
    ChatRequest,
    SessionCreate,
)
from app.services.agent_service import AgentService
from app.services.ai_session_service import AISessionService

# Persona injection-attempt keywords — blocked on create/update
_INJECTION_KEYWORDS: List[str] = [
    "ignore previous",
    "ignore all",
    "disregard",
    "system prompt",
    "jailbreak",
    "act as",
    "pretend you are",
    "you are now",
    "do anything now",
    "dan mode",
]

router = APIRouter(prefix="/ai", tags=["AI Agents"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_persona_injection(persona: str) -> None:
    """Raise 422 if the persona contains known prompt-injection patterns."""
    lowered = persona.lower()
    for keyword in _INJECTION_KEYWORDS:
        if keyword in lowered:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Persona contains disallowed content: '{keyword}'",
            )


def _agent_row_to_out(row: dict) -> AgentOut:
    """Convert a Supabase ai_agents row dict to AgentOut."""
    return AgentOut(
        id=str(row["id"]),
        name=row["name"],
        description=row.get("description"),
        persona=row.get("persona", ""),
        model=row.get("model", "qwen-max"),
        temperature=float(row.get("temperature", 0.7)),
        max_tokens=int(row.get("max_tokens", 4096)),
        rules=row.get("rules") or [],
        enabled=bool(row.get("enabled", True)),
    )


# ---------------------------------------------------------------------------
# Agents — CRUD
# ---------------------------------------------------------------------------


@router.get("/agents", response_model=List[AgentOut], summary="List available agents")
async def list_agents(
    project_id: Optional[str] = Query(default=None),
    user: AuthDep = None,
) -> List[AgentOut]:
    """Return preset agents (no owner) plus agents owned by the current user.

    When *project_id* is supplied, project-scoped agents are also included.
    Results are de-duplicated by agent id.
    """
    supabase = await get_async_supabase_admin()

    # 1. Preset (global) agents
    preset_resp = (
        await supabase.table("ai_agents")
        .select("*")
        .is_("created_by", "null")
        .eq("enabled", True)
        .execute()
    )
    agents: list[dict] = list(preset_resp.data or [])
    seen_ids: set[str] = {str(a["id"]) for a in agents}

    # 2. Project-scoped agents (if requested)
    if project_id:
        proj_resp = (
            await supabase.table("ai_agents")
            .select("*")
            .eq("project_id", project_id)
            .eq("enabled", True)
            .execute()
        )
        for row in proj_resp.data or []:
            if str(row["id"]) not in seen_ids:
                agents.append(row)
                seen_ids.add(str(row["id"]))

    # 3. User's own agents (regardless of enabled state)
    user_resp = (
        await supabase.table("ai_agents")
        .select("*")
        .eq("created_by", user.user_id)
        .execute()
    )
    for row in user_resp.data or []:
        if str(row["id"]) not in seen_ids:
            agents.append(row)
            seen_ids.add(str(row["id"]))

    return [_agent_row_to_out(a) for a in agents]


@router.post(
    "/agents",
    response_model=AgentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a custom agent",
)
async def create_agent(body: AgentCreate, user: AuthDep = None) -> AgentOut:
    """Create a custom AI agent owned by the requesting user.

    Validates the persona against known injection patterns before persisting.
    """
    _check_persona_injection(body.persona)

    supabase = await get_async_supabase_admin()
    row: Dict[str, Any] = {
        "name": body.name,
        "persona": body.persona,
        "model": body.model,
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
        "rules": body.rules,
        "created_by": user.user_id,
        "enabled": True,
    }
    if body.description is not None:
        row["description"] = body.description
    if body.project_id is not None:
        row["project_id"] = body.project_id

    resp = await supabase.table("ai_agents").insert(row).execute()
    if not resp.data:
        logger.error(f"Failed to create agent for user {user.user_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create agent",
        )
    return _agent_row_to_out(resp.data[0])


@router.patch(
    "/agents/{agent_id}",
    response_model=AgentOut,
    summary="Update a custom agent",
)
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    user: AuthDep = None,
) -> AgentOut:
    """Update a user-created agent.

    Preset agents (created_by IS NULL) are read-only and return 403.
    Only the owning user may update their own agents.
    """
    supabase = await get_async_supabase_admin()

    existing_resp = (
        await supabase.table("ai_agents")
        .select("*")
        .eq("id", agent_id)
        .maybe_single()
        .execute()
    )
    agent = existing_resp.data
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if agent.get("created_by") is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Preset agents cannot be modified",
        )
    if str(agent["created_by"]) != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this agent",
        )

    updates: Dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.description is not None:
        updates["description"] = body.description
    if body.persona is not None:
        _check_persona_injection(body.persona)
        updates["persona"] = body.persona
    if body.model is not None:
        updates["model"] = body.model
    if body.temperature is not None:
        updates["temperature"] = body.temperature
    if body.max_tokens is not None:
        updates["max_tokens"] = body.max_tokens
    if body.rules is not None:
        updates["rules"] = body.rules
    if body.enabled is not None:
        updates["enabled"] = body.enabled

    if not updates:
        return _agent_row_to_out(agent)

    resp = (
        await supabase.table("ai_agents").update(updates).eq("id", agent_id).execute()
    )
    if not resp.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update agent",
        )
    return _agent_row_to_out(resp.data[0])


@router.delete(
    "/agents/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a custom agent",
)
async def delete_agent(agent_id: str, user: AuthDep = None) -> None:
    """Permanently delete a user-owned agent.

    Preset agents (created_by IS NULL) cannot be deleted and return 403.
    """
    supabase = await get_async_supabase_admin()

    existing_resp = (
        await supabase.table("ai_agents")
        .select("id, created_by")
        .eq("id", agent_id)
        .maybe_single()
        .execute()
    )
    agent = existing_resp.data
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if agent.get("created_by") is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Preset agents cannot be deleted",
        )
    if str(agent["created_by"]) != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this agent",
        )

    await supabase.table("ai_agents").delete().eq("id", agent_id).execute()


# ---------------------------------------------------------------------------
# Agent call — single invocation
# ---------------------------------------------------------------------------


@router.post(
    "/agents/{agent_id}/call",
    summary="Call an agent (auto-creates session if none provided)",
)
async def call_agent(
    agent_id: str,
    body: AgentCallRequest,
    user: AuthDep = None,
) -> dict:
    """Invoke an agent with an arbitrary context payload.

    - If *session_id* is omitted a new session is created automatically.
    - The ``message`` key inside *context* is used as the user message.
    - *action* describes the intent (e.g. "rewrite", "expand") but is not
      forwarded to the LLM — include it in *context* if the agent needs it.
    """
    message = str(body.context.get("message", ""))
    svc = AgentService()
    try:
        result = await svc.call_agent(
            agent_id=agent_id,
            message=message,
            user_id=user.user_id,
            session_id=body.session_id,
            project_id=body.project_id,
            context=body.context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except RuntimeError as exc:
        logger.error(f"Agent call failed for agent {agent_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM service unavailable, please retry later",
        )
    return result


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=List[dict], summary="List user's sessions")
async def list_sessions(
    project_id: Optional[str] = Query(default=None),
    user: AuthDep = None,
) -> list:
    """Return all active sessions owned by the current user, newest first."""
    svc = AISessionService()
    return await svc.list_sessions(user.user_id, project_id)


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new chat session",
)
async def create_session(body: SessionCreate, user: AuthDep = None) -> dict:
    """Create a fresh AI chat session and return the session record."""
    svc = AISessionService()
    return await svc.create_session(
        user_id=user.user_id,
        project_id=body.project_id,
        title=body.title,
        context_type=body.context_type,
        context_id=body.context_id,
    )


@router.get("/sessions/{session_id}", summary="Get session detail with messages")
async def get_session(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: AuthDep = None,
) -> dict:
    """Return session metadata together with a paginated message list."""
    svc = AISessionService()
    session = await svc.get_session(session_id, user.user_id)
    messages = await svc.get_messages(
        session_id, user.user_id, limit=limit, offset=offset
    )
    return {"session": session, "messages": messages}


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a session and all its messages",
)
async def delete_session(session_id: str, user: AuthDep = None) -> dict:
    """Hard-delete a session.  Cascade-deletes all messages via FK constraint."""
    svc = AISessionService()
    await svc.delete_session(session_id, user.user_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Chat — send message in existing session
# ---------------------------------------------------------------------------

_DEFAULT_AGENT_NAME = "Writer"


async def _resolve_default_agent_id() -> Optional[str]:
    """Return the id of the first enabled preset 'Writer' agent, or None."""
    try:
        supabase = await get_async_supabase_admin()
        resp = (
            await supabase.table("ai_agents")
            .select("id")
            .is_("created_by", "null")
            .eq("name", _DEFAULT_AGENT_NAME)
            .eq("enabled", True)
            .limit(1)
            .execute()
        )
        if resp.data:
            return str(resp.data[0]["id"])
    except Exception as exc:
        logger.warning(f"Could not resolve default agent: {exc}")
    return None


@router.post(
    "/sessions/{session_id}/chat",
    summary="Send a message in an existing session",
)
async def chat(
    session_id: str,
    body: ChatRequest,
    user: AuthDep = None,
) -> dict:
    """Send *message* inside *session_id* and receive an AI reply.

    When *agent_id* is not provided the default 'Writer' preset is used.
    Returns 404 if no agent can be resolved.
    """
    agent_id = body.agent_id
    if not agent_id:
        agent_id = await _resolve_default_agent_id()
    if not agent_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No agent_id provided and no default agent found",
        )

    svc = AgentService()
    try:
        result = await svc.call_agent(
            agent_id=agent_id,
            message=body.message,
            user_id=user.user_id,
            session_id=session_id,
            context=body.context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except RuntimeError as exc:
        logger.error(f"Chat call failed in session {session_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM service unavailable, please retry later",
        )
    return result


# ---------------------------------------------------------------------------
# Usage statistics
# ---------------------------------------------------------------------------


@router.get("/usage", summary="Get token usage statistics")
async def get_usage(
    project_id: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    user: AuthDep = None,
) -> dict:
    """Return aggregated token usage for the requesting user.

    Response shape::

        {
            "total_tokens": int,
            "total_cost": float,          # always 0.0 (pricing config not yet wired)
            "by_agent": {agent_id: int, ...},
            "by_action": {model: int, ...},
            "daily_trend": [
                {"date": "YYYY-MM-DD", "tokens": int},
                ...
            ]
        }
    """
    supabase = await get_async_supabase_admin()

    query = (
        supabase.table("ai_usage_logs")
        .select(
            "agent_id, model, total_tokens, prompt_tokens, completion_tokens, created_at"
        )
        .eq("user_id", user.user_id)
        .gte("created_at", f"now() - interval '{days} days'")
    )
    if project_id:
        query = query.eq("project_id", project_id)

    resp = await query.execute()
    rows: list[dict] = resp.data or []

    total_tokens = 0
    by_agent: dict[str, int] = {}
    by_model: dict[str, int] = {}
    daily: dict[str, int] = {}

    for row in rows:
        tokens: int = row.get("total_tokens") or 0
        total_tokens += tokens

        aid = str(row.get("agent_id") or "unknown")
        by_agent[aid] = by_agent.get(aid, 0) + tokens

        model = str(row.get("model") or "unknown")
        by_model[model] = by_model.get(model, 0) + tokens

        created_at = str(row.get("created_at", ""))
        date_key = created_at[:10] if created_at else "unknown"
        daily[date_key] = daily.get(date_key, 0) + tokens

    daily_trend = [
        {"date": date, "tokens": t} for date, t in sorted(daily.items())
    ]

    return {
        "total_tokens": total_tokens,
        "total_cost": 0.0,
        "by_agent": by_agent,
        "by_action": by_model,
        "daily_trend": daily_trend,
    }
