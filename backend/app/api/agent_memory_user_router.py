"""User-facing agent-memory endpoints.

GET  /agent-memory       — list memories visible to the caller
DELETE /agent-memory/{memory_id} — delete a memory the caller owns

Isolation guarantees:
  - team_ids come from get_user_team_ids(auth.user_id) — never a client value.
  - DELETE is owner-scoped: only the owner can delete their own row.
  - owner_user_id is never returned to the client; is_owner is computed server-side.
"""

from __future__ import annotations

from fastapi import APIRouter
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.agent_memory_repository import (
    delete_user_memory,
    get_user_team_ids,
    list_user_memories,
)
from app.schemas.agent_memory import UserMemoryItem, UserMemoryListResponse

router = APIRouter(prefix="/agent-memory", tags=["Agent Memories"])


@router.get("", response_model=UserMemoryListResponse)
async def list_my_memories(auth: AuthDep) -> UserMemoryListResponse:
    """List all memories visible to the authenticated user.

    Returns own rows (any visibility) and team-shared rows for the user's teams.
    is_owner is True only when the row belongs to the caller.
    The teammate's owner UUID is never included in the response.
    """
    team_ids = await get_user_team_ids(auth.user_id)
    rows = await list_user_memories(user_id=auth.user_id, team_ids=team_ids)
    items = [
        UserMemoryItem(
            id=r["id"],
            title=r.get("title") or "",
            body_md=r.get("body_md") or "",
            kind=r.get("kind") or "fact",
            scope=r.get("scope") or "",
            visibility=r.get("visibility") or "private",
            when_to_use=r.get("when_to_use") or "",
            created_at=str(r.get("created_at") or ""),
            is_owner=(str(r.get("owner_user_id")) == str(auth.user_id)),
        )
        for r in rows
    ]
    return UserMemoryListResponse(items=items)


@router.delete("/{memory_id}")
async def delete_my_memory(memory_id: int, auth: AuthDep) -> dict:
    """Delete a memory row.

    Only succeeds if the row is owned by the authenticated user.
    A teammate's shared row cannot be deleted by another team member.
    """
    deleted = await delete_user_memory(memory_id=memory_id, user_id=auth.user_id)
    logger.info(
        "[agent_memory] user {} delete memory {}: {}", auth.user_id, memory_id, deleted
    )
    return {"deleted": deleted}
