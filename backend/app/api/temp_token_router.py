# backend/app/api/temp_token_router.py

"""
Temporary Token Router

Provides endpoints to create and validate short-lived tokens
for secure web page access (e.g. Shortcuts tag picker).

Uses Redis for storage — tokens auto-expire via TTL.

Flow:
1. Client calls POST /auth/temp-token with API Key → gets temp token
2. Client opens web page with ?token=xxx in URL
3. Web page calls GET /auth/temp-token/{token}/tags → gets tags
4. User selects tags, confirms → POST /auth/temp-token/{token}/selection
5. Shortcuts calls GET /auth/temp-token/{token}/selection → gets result
"""

import json
import secrets

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.core.deps import AuthDep
from app.core.redis import get_async_redis
from app.repositories.tags_repository import TagsRepository
from app.schemas.tags import TagListResponse

router = APIRouter(prefix="/auth/temp-token", tags=["Temp Token"])

TOKEN_TTL_SECONDS = 5 * 60  # 5 minutes
TOKEN_LENGTH = 32  # 32 bytes = 64 hex chars
REDIS_PREFIX = "temp_token:"


class TempTokenRequest(BaseModel):
    scopes: list[str] | None = None  # defaults to ["tags:read"]


class TempTokenResponse(BaseModel):
    token: str
    expires_at: str
    ttl_seconds: int


class SelectionRequest(BaseModel):
    tags: list[str]


class SelectionResponse(BaseModel):
    tags: list[str]


async def _get_token_data(token: str) -> dict:
    """Validate a temp token from Redis.

    Raises HTTPException if invalid or expired.
    """
    redis = await get_async_redis()
    raw = await redis.get(f"{REDIS_PREFIX}{token}")

    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    return json.loads(raw)


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
    scopes = (
        request.scopes if request and request.scopes else ["tags:read", "tags:write"]
    )
    token = secrets.token_hex(TOKEN_LENGTH)

    data = {
        "user_id": auth.user_id,
        "scopes": scopes,
        "selection": [],
    }

    redis = await get_async_redis()
    await redis.setex(
        f"{REDIS_PREFIX}{token}",
        TOKEN_TTL_SECONDS,
        json.dumps(data),
    )

    return TempTokenResponse(
        token=token,
        expires_at="",  # TTL-based, no fixed timestamp needed
        ttl_seconds=TOKEN_TTL_SECONDS,
    )


@router.get("/{token}/tags", response_model=TagListResponse)
async def get_tags_by_token(
    token: str,
    enabled_only: bool = Query(True, description="Only return enabled tags"),
):
    """
    Get tags using a temporary token. No auth header needed.
    """
    data = await _get_token_data(token)

    scopes = data.get("scopes", [])
    if "tags:read" not in scopes and "tags:*" not in scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not have tags:read scope",
        )

    repo = TagsRepository()
    tags = await repo.get_all_tags(data["user_id"], enabled_only=enabled_only)

    return TagListResponse(tags=tags, total=len(tags))


@router.post("/{token}/selection")
async def save_selection(token: str, request: SelectionRequest):
    """
    Save tag selection to the token. Called by the web page on confirm.
    """
    data = await _get_token_data(token)
    data["selection"] = request.tags

    redis = await get_async_redis()
    ttl = await redis.ttl(f"{REDIS_PREFIX}{token}")
    if ttl > 0:
        await redis.setex(
            f"{REDIS_PREFIX}{token}",
            ttl,
            json.dumps(data),
        )

    return {"success": True}


class CreateTagRequest(BaseModel):
    name: str
    name_zh: str | None = None
    group_id: str | None = None
    color: str | None = "#6366f1"


@router.post("/{token}/tags")
async def create_tag_by_token(token: str, request: CreateTagRequest):
    """Create a new tag using a temporary token. No auth header needed."""
    data = await _get_token_data(token)

    scopes = data.get("scopes", [])
    if "tags:write" not in scopes and "tags:*" not in scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not have tags:write scope",
        )

    repo = TagsRepository()

    # Check duplicate
    existing = await repo.get_tag_by_name(request.name, data["user_id"])
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tag '{request.name}' already exists",
        )

    created = await repo.create_tag(
        name=request.name,
        user_id=data["user_id"],
        color=request.color,
        name_zh=request.name_zh,
    )

    # Set group_id: use provided value, or default to "Uncategorized" group
    if created:
        from app.db.supabase_client import get_async_supabase_admin

        client = await get_async_supabase_admin()
        group_id = request.group_id
        if not group_id:
            # Find the Uncategorized group
            result = (
                await client.table("tag_groups")
                .select("id")
                .eq("name", "Uncategorized")
                .limit(1)
                .execute()
            )
            if result.data:
                group_id = str(result.data[0]["id"])
        if group_id:
            await client.table("tags").update({"group_id": group_id}).eq(
                "id", created["id"]
            ).execute()
            created["group_id"] = group_id

    return {"success": True, "data": created}


@router.get("/{token}/selection")
async def get_selection(
    token: str,
    format: str = Query("json", description="Response format: json or text"),
):
    """
    Retrieve saved tag selection. Called by Shortcuts after web view closes.

    Use ?format=text to get comma-separated plain text (e.g. "tag1,tag2,tag3").
    """
    from fastapi.responses import PlainTextResponse

    data = await _get_token_data(token)
    tags = data.get("selection") or []

    if format == "text":
        return PlainTextResponse(",".join(tags))

    return SelectionResponse(tags=tags)
