# backend/app/api/temp_ttl_router.py
"""GET/PUT /api/v1/library/temp-ttl — per-scope chat temp resource TTL."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.deps import AuthDep
from app.services.library.temp_ttl_settings import (
    get_chat_temp_ttl_days,
    set_chat_temp_ttl_days,
)

router = APIRouter(prefix="/library", tags=["library"])

ScopeType = Literal["personal", "team"]


class TempTtlUpdate(BaseModel):
    scope_type: ScopeType
    scope_id: str = Field(..., min_length=1)
    ttl_days: int = Field(..., description="positive int, or -1 for never")


async def verify_scope_access(auth: object, scope_type: str, scope_id: str) -> None:
    """Plain-coroutine scope guard (testable without FastAPI DI).

    Mirrors the logic in ``app.core.scope_guards.verify_scope_access`` but
    accepts explicit arguments so route handlers — and unit tests — can call
    it directly rather than through FastAPI's Depends machinery.

    - personal: scope_id must equal the caller's own user_id.
    - team:     caller must be a member of the team (DB check).
    """
    user_id = auth.user_id  # type: ignore[attr-defined]

    if scope_type == "personal":
        if scope_id != user_id:
            raise HTTPException(
                status_code=403,
                detail="Cannot write to another user's personal scope",
            )
        return

    if scope_type == "team":
        from app.db.supabase_client import get_async_supabase_admin

        client = await get_async_supabase_admin()
        result = (
            await client.table("team_members")
            .select("team_id")
            .eq("team_id", scope_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not result.data:
            raise HTTPException(
                status_code=403,
                detail="You are not a member of this team",
            )
        return

    raise HTTPException(
        status_code=422,
        detail=f"Invalid scope_type: {scope_type!r} (expected 'personal' or 'team')",
    )


@router.get("/temp-ttl")
async def get_temp_ttl(
    auth: AuthDep,
    scope_type: ScopeType = Query(...),
    scope_id: str = Query(..., min_length=1),
) -> dict:
    """Read the current chat temp TTL for a scope. Returns ``-1`` for 'never'."""
    await verify_scope_access(auth, scope_type, scope_id)
    days = await get_chat_temp_ttl_days(scope_type, scope_id)
    return {"ttl_days": -1 if days is None else days}


@router.put("/temp-ttl")
async def put_temp_ttl(auth: AuthDep, payload: TempTtlUpdate) -> dict:
    """Set the chat temp TTL for a scope. Pass ``ttl_days=-1`` for 'never'."""
    await verify_scope_access(auth, payload.scope_type, payload.scope_id)
    if payload.ttl_days != -1 and payload.ttl_days <= 0:
        raise HTTPException(
            status_code=400,
            detail="ttl_days must be a positive int or -1 (never)",
        )
    await set_chat_temp_ttl_days(payload.scope_type, payload.scope_id, payload.ttl_days)
    return {"ttl_days": payload.ttl_days}
