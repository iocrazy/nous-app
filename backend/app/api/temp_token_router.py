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
from pydantic import BaseModel, Field

from app.api.row_guard import require_row
from app.core.api_key_scopes import check_scope_permission
from app.core.deps import AuthContext, AuthDep
from app.core.redis import get_async_redis
from app.repositories.tags_repository import get_tags_repository
from app.schemas.auth_responses import AuthAck, TempTokenCreatedTagResponse
from app.schemas.tags import GroupIdIn, TagListResponse

router = APIRouter(prefix="/auth/temp-token", tags=["Temp Token"])

TOKEN_TTL_SECONDS = 5 * 60  # 5 minutes
DEFAULT_SCOPES = ("tags:read", "tags:write")
TOKEN_LENGTH = 32  # 32 bytes = 64 hex chars
REDIS_PREFIX = "temp_token:"


class TempTokenRequest(BaseModel):
    scopes: list[str] | None = None  # defaults to ["tags:read"]


class TempTokenResponse(BaseModel):
    token: str
    expires_at: str
    ttl_seconds: int


class SelectionOptions(BaseModel):
    """选择页上的四项单选（spec 2026-09-10）。旧令牌没有 options 键时按此默认读。"""

    rating: int | None = Field(None, ge=0, le=5)
    transcribe: bool = False
    summarize: bool = False
    analyze: bool = False


class SelectionRequest(SelectionOptions):
    tags: list[str]


class SelectionResponse(SelectionOptions):
    tags: list[str]


SELECTION_FIELDS = ("rating", "transcribe", "summarize", "analyze")


def _granted_scopes(auth: AuthContext, requested: list[str] | None) -> list[str]:
    """Scopes the new temp token carries: never more than the caller holds.

    A JWT session holds everything, so it gets what it asked for (default:
    read + write). An API key only reaches this route with ``tags:read``; the
    token used to default to ``tags:write`` regardless, so a read-only key
    could mint a token and create tags with it. Now an API key gets the
    default narrowed to the scopes it holds, and an explicit request for a
    scope it does not hold is refused.
    """
    if auth.auth_type != "api_key":
        return list(requested or DEFAULT_SCOPES)
    held = auth.scopes or []
    if requested:
        missing = [s for s in requested if not check_scope_permission([s], held)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "scope_not_held",
                    "message": "The API key does not hold: " + ", ".join(missing),
                },
            )
        return list(requested)
    return [s for s in DEFAULT_SCOPES if check_scope_permission([s], held)]


def _options_from(data: dict) -> SelectionOptions:
    return SelectionOptions.model_validate(data.get("options") or {})


def _field_as_text(options: SelectionOptions, field: str) -> str:
    """快捷指令逐项读值：rating → "0"–"5"（None 记 0），布尔 → "1"/"0"。"""
    if field == "rating":
        return str(options.rating or 0)
    return "1" if getattr(options, field) else "0"


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
    scopes = _granted_scopes(auth, request.scopes if request else None)
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

    repo = get_tags_repository()
    tags = await repo.get_all_tags(data["user_id"], enabled_only=enabled_only)

    return TagListResponse(tags=tags, total=len(tags))


@router.post("/{token}/selection", response_model=AuthAck)
async def save_selection(token: str, request: SelectionRequest):
    """
    Save tag selection to the token. Called by the web page on confirm.

    A token that expires between the read and the write is refused like any
    expired token (401): the selection was NOT saved, and the page must say so.
    """
    data = await _get_token_data(token)
    data["selection"] = request.tags
    data["options"] = request.model_dump(exclude={"tags"})

    redis = await get_async_redis()
    ttl = await redis.ttl(f"{REDIS_PREFIX}{token}")
    if ttl <= 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    await redis.setex(
        f"{REDIS_PREFIX}{token}",
        ttl,
        json.dumps(data),
    )

    return {"success": True}


class CreateTagRequest(BaseModel):
    # Bounds are the ``tags`` column widths: past them the INSERT failed as a 500.
    name: str = Field(..., min_length=1, max_length=50)
    name_zh: str | None = Field(None, max_length=50)
    group_id: GroupIdIn | None = None
    color: str | None = Field("#6366f1", max_length=20)


async def _group_exists(group_id: str) -> bool:
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TagGroups

    async with read_scope() as session:
        row = (
            await session.execute(
                select(TagGroups.id).where(TagGroups.id == int(group_id)).limit(1)
            )
        ).first()
    return row is not None


@router.post("/{token}/tags", response_model=TempTokenCreatedTagResponse)
async def create_tag_by_token(token: str, request: CreateTagRequest):
    """Create a new tag using a temporary token. No auth header needed."""
    data = await _get_token_data(token)

    scopes = data.get("scopes", [])
    if "tags:write" not in scopes and "tags:*" not in scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not have tags:write scope",
        )

    # An unknown group used to fail the group UPDATE with a foreign-key 500
    # AFTER the tag had been created. Check it before writing anything.
    if request.group_id and not await _group_exists(request.group_id):
        require_row(None)

    repo = get_tags_repository()

    # Check duplicate. Return WHICH tag conflicts so the picker can explain it:
    # the English `name` (often an auto-translation) may collide with a built-in
    # system tag whose Chinese alias differs (e.g. "康复" -> "Healing", which
    # already exists as Healing/治愈). The Chinese name isn't the duplicate.
    existing = await repo.get_tag_by_name(request.name, data["user_id"])
    if existing:
        ezh = existing.get("name_zh")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "tag_name_conflict",
                "message": (
                    f"English name '{request.name}' already belongs to tag "
                    f"'{existing.get('name')}'"
                    + (f" ({ezh})" if ezh else "")
                    + f" [{existing.get('type')}]"
                ),
                "attempted_name": request.name,
                "conflict": {
                    "name": existing.get("name"),
                    "name_zh": ezh,
                    "type": existing.get("type"),
                },
            },
        )

    created = await repo.create_tag(
        name=request.name,
        user_id=data["user_id"],
        color=request.color,
        name_zh=request.name_zh,
    )

    # Set group_id: use provided value, or default to "Uncategorized" group
    if created:
        from sqlalchemy import select
        from sqlalchemy import update as sa_update

        from app.db.session import read_scope, write_scope
        from app.models import TagGroups, Tags

        group_id = request.group_id
        if not group_id:
            # Find the Uncategorized group
            async with read_scope() as session:
                row = (
                    await session.execute(
                        select(TagGroups.id)
                        .where(TagGroups.name == "Uncategorized")
                        .limit(1)
                    )
                ).first()
            if row is not None:
                group_id = str(row[0])
        if group_id:
            async with write_scope() as session:
                await session.execute(
                    sa_update(Tags)
                    .where(Tags.id == int(str(created["id"])))
                    .values(group_id=int(str(group_id)))
                )
            created["group_id"] = group_id

    return {"success": True, "data": created}


@router.get(
    "/{token}/selection",
    response_model=SelectionResponse,
    responses={
        200: {
            "description": "JSON by default; with ``format=text`` a plain-text "
            "tag CSV, or one option's value when ``field`` is given.",
            "content": {"text/plain": {"schema": {"type": "string"}}},
        }
    },
)
async def get_selection(
    token: str,
    format: str = Query("json", description="Response format: json or text"),
    field: str | None = Query(
        None,
        description="With format=text: return ONE option as plain text — "
        "rating (0-5) | transcribe | summarize | analyze (1/0).",
    ),
):
    """
    Retrieve saved tag selection. Called by Shortcuts after web view closes.

    Use ?format=text to get comma-separated plain text (e.g. "tag1,tag2,tag3").
    Use ?format=text&field=rating (or transcribe / summarize / analyze) to get
    that single option as plain text, so Shortcuts needs no JSON parsing.
    """
    from fastapi.responses import PlainTextResponse

    data = await _get_token_data(token)
    tags = data.get("selection") or []

    # 选项只在用得到的分支里解析：format=text 的 CSV 老路径保持零改动。
    if format == "text":
        if field is None:
            return PlainTextResponse(",".join(tags))
        if field not in SELECTION_FIELDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown field '{field}'; expected one of {', '.join(SELECTION_FIELDS)}",
            )
        return PlainTextResponse(_field_as_text(_options_from(data), field))

    return SelectionResponse(tags=tags, **_options_from(data).model_dump())
