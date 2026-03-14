# backend/app/api/temp_token_router.py

"""
Temporary Token Router

Provides endpoints to create and validate short-lived tokens
for secure web page access (e.g. Shortcuts tag picker).

Flow:
1. Client calls POST /auth/temp-token with API Key → gets temp token
2. Client opens web page with ?token=xxx in URL (safe, short-lived)
3. Web page calls GET /auth/temp-token/{token}/tags → gets tags (no auth needed)
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.tags_repository import TagsRepository
from app.schemas.tags import TagListResponse

router = APIRouter(prefix="/auth/temp-token", tags=["Temp Token"])

TOKEN_TTL_MINUTES = 5
TOKEN_LENGTH = 32  # 32 bytes = 64 hex chars


class TempTokenRequest(BaseModel):
    scopes: list[str] | None = None  # defaults to ["tags:read"]


class TempTokenResponse(BaseModel):
    token: str
    expires_at: str
    ttl_seconds: int


async def _validate_temp_token(token: str) -> dict:
    """Validate a temp token and return the row if valid.

    Raises HTTPException if invalid or expired.
    """
    client = await get_async_supabase_admin()

    try:
        result = (
            await client.table("temp_tokens")
            .select("*")
            .eq("token", token)
            .execute()
        )
    except Exception as e:
        logger.error(f"Failed to verify temp token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token verification failed",
        )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    row = result.data[0]
    expires_at = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))

    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )

    return row


@router.post("", response_model=TempTokenResponse)
async def create_temp_token(
    auth: AuthDep,
    request: TempTokenRequest | None = None,
):
    """
    Create a temporary token for web page access.

    Requires API Key or JWT auth. Returns a short-lived token
    that can be safely passed in URLs.
    """
    scopes = (request.scopes if request and request.scopes else ["tags:read"])
    token = secrets.token_hex(TOKEN_LENGTH)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES)

    client = await get_async_supabase_admin()

    try:
        await (
            client.table("temp_tokens")
            .insert({
                "token": token,
                "user_id": auth.user_id,
                "scopes": scopes,
                "expires_at": expires_at.isoformat(),
            })
            .execute()
        )
    except Exception as e:
        logger.error(f"Failed to create temp token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create temporary token",
        )

    return TempTokenResponse(
        token=token,
        expires_at=expires_at.isoformat(),
        ttl_seconds=TOKEN_TTL_MINUTES * 60,
    )


@router.get("/{token}/tags", response_model=TagListResponse)
async def get_tags_by_token(
    token: str,
    enabled_only: bool = Query(True, description="Only return enabled tags"),
):
    """
    Get tags using a temporary token. No auth header needed.

    This is the endpoint the Shortcuts tag picker page calls.
    """
    row = await _validate_temp_token(token)

    # Check scope
    scopes = row.get("scopes", [])
    if "tags:read" not in scopes and "tags:*" not in scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not have tags:read scope",
        )

    user_id = row["user_id"]
    repo = TagsRepository()
    tags = await repo.get_all_tags(user_id, enabled_only=enabled_only)

    return TagListResponse(tags=tags, total=len(tags))
