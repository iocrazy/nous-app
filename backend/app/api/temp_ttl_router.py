# backend/app/api/temp_ttl_router.py
"""GET/PUT /api/v1/library/temp-ttl — per-scope chat temp resource TTL."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.deps import AuthDep
from app.core.scope_guards import verify_scope_access
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
