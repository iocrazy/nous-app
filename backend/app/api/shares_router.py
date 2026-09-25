# backend/app/api/shares_router.py

"""
Shares Router

Sharing system API endpoints: create, list, update, cancel, and
public access by share code. Supports link, review, presentation,
and delivery share types.

Access rules (P6, 2026-09-24):

- Owner routes (``DELETE /shares/{id}``, ``DELETE /shares/{id}/permanent``):
  a share that does not exist and a share someone else created are the same
  typed 404 ``not_found_or_out_of_scope`` (it used to be 404 vs 403, which
  confirmed that a guessed id existed). ``GET /shares`` lists only the
  caller's own. ``GET`` / ``PUT /shares/{id}`` were removed in P6: nothing
  called them.
- ``POST /shares`` only shares what the caller may read: their own resource
  (or one filed into their team), a file of a project they can read, a folder
  of a team they belong to. It used to share any id, and a share is a public
  read grant — that was a way to read anybody's file.
- Visitor routes (``/shares/code/{code}...``) go through
  ``app/api/share_access.py``: the access route hands out a share grant once
  the password has been checked, and the comment routes demand that grant on
  a protected share.
"""

import secrets
import string
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from app.api.row_guard import NOT_FOUND_OR_OUT_OF_SCOPE
from app.api.share_access import (
    is_live,
    load_share,
    sign_share_grant,
    token_opens_share,
)
from app.core.deps import AuthDep, OptionalAuthDep
from app.schemas.envelope import Envelope
from app.schemas.share_responses import (
    ShareComment,
    ShareCommentRow,
    ShareListResponse,
    ShareMessageResponse,
    ShareRow,
    ShareStatusToggleResponse,
    ShareVisitorView,
)
from app.schemas.shares import ShareAccessRequest, ShareCreate
from app.services.library.share_passwords import (
    has_password,
    password_matches_async,
    share_password_columns,
)
from app.services.modules.gate import require_module


class ShareCommentCreate(BaseModel):
    """Request body for creating a comment on a shared resource."""

    content: str = Field(..., min_length=1, max_length=5000)
    timecode: Optional[float] = Field(None, ge=0, description="Timestamp in seconds")


router = APIRouter(
    prefix="/shares",
    dependencies=[Depends(require_module("shares"))],
)

SHARE_CODE_LENGTH = 8
SHARE_CODE_ALPHABET = string.ascii_letters + string.digits

ShareTokenQuery = Annotated[
    Optional[str],
    Query(
        description=(
            "The access_token from POST /shares/code/{code}. Required when "
            "the share has a password."
        )
    ),
]


# ============================================
# Helper functions
# ============================================


def _generate_share_code(length: int = SHARE_CODE_LENGTH) -> str:
    """Generate a cryptographically random alphanumeric share code."""
    return "".join(secrets.choice(SHARE_CODE_ALPHABET) for _ in range(length))


def _build_share_url(share_code: str) -> str:
    """Build the public URL for a share code."""
    return f"/s/{share_code}"


def _enrich_share(share: dict) -> dict:
    """Add computed fields (share_url) and strip the password columns."""
    share["share_url"] = _build_share_url(share.get("share_code", ""))
    # Never expose the hash (or the legacy lock); only whether one is set.
    share["has_password"] = has_password(share)
    share.pop("password", None)
    share.pop("password_hash", None)
    return share


def _row_to_dict(row) -> dict:
    """Row mapping → PostgREST-shaped dict: datetime → ISO str, UUID → str;
    other columns native (BIGINT ids stay int, matching the old client)."""
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def _not_found(message: str = "Share not found") -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": NOT_FOUND_OR_OUT_OF_SCOPE, "message": message},
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resources_scope(reason: str):
    """``resources`` is user-scoped; a visitor has no user scope at all."""
    from app.db.scope import is_enforced, system_request_scope

    if is_enforced("resources"):
        return system_request_scope(reason=f"shares: {reason}")
    return nullcontext()


async def _load_owned_share(share_id: str, user_id: str, *columns) -> dict:
    """``columns`` of the caller's own share row, or the typed 404 (missing
    and foreign are indistinguishable)."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Shares

    sid = _as_int(share_id)
    if sid is None:
        raise _not_found()
    async with read_scope() as session:
        row = (
            (await session.execute(select(*columns).where(Shares.id == sid).limit(1)))
            .mappings()
            .first()
        )
    if not row or str(row["shared_by"]) != str(user_id):
        raise _not_found()
    return _row_to_dict(row)


# ============================================
# Share target checks (POST /shares)
# ============================================


async def _in_team(session, team_id: int, user_id: str) -> bool:
    from sqlalchemy import select

    from app.models import TeamMembers

    hit = (
        await session.execute(
            select(TeamMembers.team_id)
            .where(TeamMembers.team_id == team_id)
            .where(TeamMembers.user_id == user_id)
            .limit(1)
        )
    ).first()
    return hit is not None


async def _can_share_project_file(file_id: int, user_id: str) -> bool:
    from sqlalchemy import select

    from app.core.scope_guards import _resolve_project_access
    from app.db.session import read_scope
    from app.models import ProjectFiles

    async with read_scope() as session:
        project_id = (
            await session.execute(
                select(ProjectFiles.project_id).where(ProjectFiles.id == file_id)
            )
        ).scalar()
    if project_id is None:
        return False
    access = await _resolve_project_access(str(project_id), user_id)
    return bool(access and access.can_read)


async def _can_share_folder(folder_id: int, user_id: str) -> bool:
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Folders

    async with read_scope() as session:
        row = (
            await session.execute(
                select(Folders.scope_id, Folders.created_by).where(
                    Folders.id == folder_id
                )
            )
        ).first()
        if row is None:
            return False
        if str(row[1]) == str(user_id):
            return True
        return await _in_team(session, row[0], user_id)


async def _version_belongs_to_file(version_id: int, file_id: int | None) -> bool:
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import FileVersions

    if file_id is None:
        return False
    async with read_scope() as session:
        owner = (
            await session.execute(
                select(FileVersions.file_id).where(FileVersions.id == version_id)
            )
        ).scalar()
    return owner is not None and owner == file_id


async def _require_share_targets(data: ShareCreate, user_id: str) -> dict:
    """Every id in ``data`` exists and the caller may share it; returns the
    ids as ints. A target the caller cannot read is the typed 404."""
    from app.api.media_access_guard import caller_can_read_resource
    from app.db.session import read_scope

    raw = {
        "resource_id": data.resource_id,
        "project_file_id": data.project_file_id,
        "folder_id": data.folder_id,
        "version_id": data.version_id,
        "team_id": data.team_id,
    }
    ids = {k: _as_int(v) for k, v in raw.items() if v}
    if any(v is None for v in ids.values()):
        raise _not_found("Share target not found")

    checks = []
    if "resource_id" in ids:
        checks.append(caller_can_read_resource(ids["resource_id"], user_id))
    if "project_file_id" in ids:
        checks.append(_can_share_project_file(ids["project_file_id"], user_id))
    if "folder_id" in ids:
        checks.append(_can_share_folder(ids["folder_id"], user_id))
    if "version_id" in ids:
        checks.append(
            _version_belongs_to_file(ids["version_id"], ids.get("project_file_id"))
        )
    for check in checks:
        if not await check:
            raise _not_found("Share target not found")
    if "team_id" in ids:
        async with read_scope() as session:
            if not await _in_team(session, ids["team_id"], user_id):
                raise _not_found("Team not found")
    return ids


# ============================================
# Authenticated endpoints
# ============================================


async def _unique_share_code() -> str:
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Shares

    for _ in range(5):
        share_code = _generate_share_code()
        async with read_scope() as session:
            exists = (
                await session.execute(
                    select(Shares.id).where(Shares.share_code == share_code).limit(1)
                )
            ).scalar()
        if exists is None:
            return share_code
    raise HTTPException(
        status_code=500,
        detail="Failed to generate a unique share code. Please try again.",
    )


@router.post("", response_model=Envelope[ShareRow])
async def create_share(data: ShareCreate, auth: AuthDep):
    """
    Create a new share.

    At least one of resource_id, project_file_id, or folder_id must be
    provided, and the caller must be able to read it.  A unique 8-character
    share code is generated automatically.

    Authentication: Bearer Token or API Key
    """
    # Validate that at least one target is specified
    if not any([data.resource_id, data.project_file_id, data.folder_id]):
        raise HTTPException(
            status_code=400,
            detail="At least one of resource_id, project_file_id, or folder_id is required.",
        )

    try:
        from sqlalchemy import insert

        from app.db.session import write_scope
        from app.models import Shares

        ids = await _require_share_targets(data, auth.user_id)
        share_code = await _unique_share_code()

        # expires_at is timestamptz → bind the native datetime (not isoformat).
        insert_data = {
            "share_code": share_code,
            "shared_by": auth.user_id,
            "share_type": data.share_type,
            "share_name": data.share_name,
            "allow_download": data.allow_download,
            "watermark": data.watermark,
            **ids,
        }
        if data.password:
            insert_data.update(share_password_columns(data.password))
        if data.expires_at:
            insert_data["expires_at"] = data.expires_at
        if data.max_views is not None:
            insert_data["max_views"] = data.max_views

        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        insert(Shares)
                        .values(**insert_data)
                        .returning(*Shares.__table__.columns)
                    )
                )
                .mappings()
                .first()
            )

        if not row:
            raise HTTPException(status_code=500, detail="Failed to create share")

        share = _enrich_share(_row_to_dict(row))
        logger.info(f"Share created: {share_code} by user {auth.user_id}")
        return {"success": True, "data": share}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create share: {e}")
        raise HTTPException(status_code=500, detail="Failed to create share")


@router.get("", response_model=ShareListResponse)
async def list_shares(
    auth: AuthDep,
    share_type: Optional[str] = Query(
        None,
        pattern="^(link|review|presentation|delivery)$",
        description="Filter by share type",
    ),
    status: Optional[str] = Query(
        None,
        pattern="^(active|inactive|expired|cancelled)$",
        description="Filter by status",
    ),
    team_id: Optional[str] = Query(
        None,
        description="Filter by team ID. 'personal' = team_id IS NULL.",
    ),
    limit: int = Query(50, ge=1, le=200, description="Number of records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
):
    """
    List shares created by the current user.

    Supports filtering by share_type, status, and team_id, with pagination.

    Authentication: Bearer Token or API Key
    """
    team_filter = None
    if team_id and team_id != "personal":
        team_filter = _as_int(team_id)
        if team_filter is None:
            raise HTTPException(status_code=400, detail="Invalid team_id")

    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import Shares

        stmt = (
            select(*Shares.__table__.columns)
            .where(Shares.shared_by == auth.user_id)
            .order_by(Shares.created_at.desc())
        )

        if team_id == "personal":
            stmt = stmt.where(Shares.team_id.is_(None))
        elif team_filter is not None:
            stmt = stmt.where(Shares.team_id == team_filter)

        if share_type:
            stmt = stmt.where(Shares.share_type == share_type)
        if status:
            stmt = stmt.where(Shares.status == status)

        stmt = stmt.offset(offset).limit(limit)

        async with read_scope() as session:
            rows = (await session.execute(stmt)).mappings().all()

        shares = [_enrich_share(_row_to_dict(r)) for r in rows]

        return {
            "success": True,
            "data": shares,
            "count": len(shares),
        }

    except Exception as e:
        logger.error(f"Failed to list shares: {e}")
        raise HTTPException(status_code=500, detail="Failed to list shares")


@router.delete("/{share_id}", response_model=ShareStatusToggleResponse)
async def toggle_share_status(share_id: str, auth: AuthDep):
    """
    Toggle share status: active → inactive, inactive → active.

    Authentication: Bearer Token or API Key
    """
    try:
        from sqlalchemy import update

        from app.db.session import write_scope
        from app.models import Shares

        existing = await _load_owned_share(
            share_id, auth.user_id, Shares.id, Shares.shared_by, Shares.status
        )

        # Toggle: active → inactive, inactive/cancelled → active
        new_status = "inactive" if existing["status"] == "active" else "active"

        async with write_scope() as session:
            updated = (
                await session.execute(
                    update(Shares)
                    .where(Shares.id == existing["id"])
                    .where(Shares.shared_by == auth.user_id)
                    .values(status=new_status)
                    .returning(Shares.id)
                )
            ).scalar()

        if updated is None:
            raise _not_found()

        logger.info(f"Share {share_id} toggled to {new_status} by user {auth.user_id}")
        return {"success": True, "message": f"Share {new_status}", "status": new_status}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to toggle share {share_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update share")


@router.delete("/{share_id}/permanent", response_model=ShareMessageResponse)
async def delete_share_permanent(share_id: str, auth: AuthDep):
    """
    Permanently delete a share record (hard delete).

    Authentication: Bearer Token or API Key
    """
    try:
        from sqlalchemy import delete

        from app.db.session import write_scope
        from app.models import Shares

        existing = await _load_owned_share(
            share_id, auth.user_id, Shares.id, Shares.shared_by
        )

        async with write_scope() as session:
            await session.execute(
                delete(Shares)
                .where(Shares.id == existing["id"])
                .where(Shares.shared_by == auth.user_id)
            )

        logger.info(f"Share {share_id} permanently deleted by user {auth.user_id}")
        return {"success": True, "message": "Share deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete share {share_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete share")


# ============================================
# Public access endpoint
# ============================================


async def _record_view(share_id: int, viewer_id: Optional[str]) -> None:
    """Upsert the viewer's ``share_views`` row (anonymous: always a new row).
    last_viewed_at is timestamptz → bind a native datetime (asyncpg rejects
    ISO strings)."""
    from sqlalchemy import insert, select, update

    from app.db.session import read_scope, write_scope
    from app.models import ShareViews

    now_dt = datetime.now(timezone.utc)
    if not viewer_id:
        async with write_scope() as session:
            await session.execute(
                insert(ShareViews).values(share_id=share_id, last_viewed_at=now_dt)
            )
        return

    async with read_scope() as session:
        view_record = (
            (
                await session.execute(
                    select(ShareViews.id, ShareViews.view_count)
                    .where(ShareViews.share_id == share_id)
                    .where(ShareViews.viewer_id == viewer_id)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
    async with write_scope() as session:
        if view_record:
            await session.execute(
                update(ShareViews)
                .where(ShareViews.id == view_record["id"])
                .values(
                    view_count=view_record["view_count"] + 1,
                    last_viewed_at=now_dt,
                )
            )
        else:
            await session.execute(
                insert(ShareViews).values(
                    share_id=share_id, viewer_id=viewer_id, last_viewed_at=now_dt
                )
            )


async def _resource_preview_meta(resource_id: Any) -> dict:
    """mime_type / filename / cover / media_id of the shared resource, for the
    visitor's preview. Empty when the resource is gone.

    ``resources`` is user-scoped and a visitor has no scope: without the
    system scope this lookup raised under ``SCOPE_ENFORCE_RESOURCES`` (on in
    production), the except below swallowed it, and every share page lost
    its preview metadata."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Resources

    if not resource_id:
        return {}
    try:
        async with _resources_scope("visitor preview of the shared resource"):
            async with read_scope() as session:
                res = (
                    (
                        await session.execute(
                            select(
                                Resources.mime_type,
                                Resources.file_type,
                                Resources.filename,
                                Resources.cover_image_path,
                                Resources.thumbnail_path,
                                Resources.media_id,
                            )
                            .where(Resources.id == resource_id)
                            .limit(1)
                        )
                    )
                    .mappings()
                    .first()
                )
    except Exception as e:
        logger.warning(f"Failed to fetch resource metadata for share: {e}")
        return {}
    if not res:
        return {}
    return {
        "mime_type": res.get("mime_type"),
        "file_type": res.get("file_type"),
        "filename": res.get("filename"),
        "cover_image_path": res.get("cover_image_path"),
        "thumbnail_path": res.get("thumbnail_path"),
        "media_id": (str(res["media_id"]) if res.get("media_id") else None),
    }


async def _require_open_share(share: dict) -> None:
    """410 for a share that is switched off or used up. An expired share that
    still says ``active`` is flipped to ``expired`` on the way out."""
    from sqlalchemy import update

    from app.db.session import write_scope
    from app.models import Shares

    if share["status"] in ("cancelled", "inactive"):
        raise HTTPException(status_code=410, detail="This share is no longer available")
    if share["status"] != "active" or not is_live(share, count_views=True):
        if share["status"] == "active":
            async with write_scope() as session:
                await session.execute(
                    update(Shares)
                    .where(Shares.id == share["id"])
                    .values(status="expired")
                )
        raise HTTPException(status_code=410, detail="This share has expired")


@router.post(
    "/code/{share_code}",
    response_model=Envelope[ShareVisitorView],
    response_model_exclude_unset=True,
)
async def access_share_by_code(
    share_code: str,
    body: ShareAccessRequest,
    auth: OptionalAuthDep = None,
):
    """
    Public: access shared content by share code.

    Validates password (if set), checks expiration and max view limits,
    increments view_count, and records a view in share_views. Returns an
    ``access_token`` (share grant) for the media and comment routes.

    Authentication: Optional (viewer identity is recorded if authenticated)
    """
    try:
        from sqlalchemy import select, update

        from app.db.session import read_scope, write_scope
        from app.models import Shares

        # Look up the share by code
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(*Shares.__table__.columns)
                        .where(Shares.share_code == share_code)
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )

        if not row:
            raise HTTPException(status_code=404, detail="Share not found")

        share = _row_to_dict(row)
        await _require_open_share(share)

        # Check password
        if has_password(share):
            if not body.password:
                raise HTTPException(
                    status_code=401,
                    detail="Password required",
                    headers={"X-Share-Password-Required": "true"},
                )
            if not await password_matches_async(share["password_hash"], body.password):
                raise HTTPException(status_code=401, detail="Incorrect password")

        # Increment view_count on the share
        new_view_count = (share.get("view_count") or 0) + 1
        async with write_scope() as session:
            await session.execute(
                update(Shares)
                .where(Shares.id == share["id"])
                .values(view_count=new_view_count)
            )

        viewer_id = auth.user_id if auth else None
        await _record_view(share["id"], viewer_id)

        # Build the response (strip sensitive fields)
        response_data = {
            "id": share["id"],
            "share_type": share["share_type"],
            "share_name": share["share_name"],
            "share_code": share["share_code"],
            "allow_download": share["allow_download"],
            "watermark": share.get("watermark", False),
            "view_count": new_view_count,
            "resource_id": share.get("resource_id"),
            "project_file_id": share.get("project_file_id"),
            "folder_id": share.get("folder_id"),
            "version_id": share.get("version_id"),
            "created_at": share["created_at"],
            "access_token": sign_share_grant(
                {"id": share["id"], "password_hash": share.get("password_hash")}
            ),
            **(await _resource_preview_meta(share.get("resource_id"))),
        }

        logger.info(
            f"Share {share_code} accessed (view #{new_view_count})"
            f"{' by user ' + viewer_id if viewer_id else ' anonymously'}"
        )
        return {"success": True, "data": response_data}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to access share {share_code}: {e}")
        raise HTTPException(status_code=500, detail="Failed to access share")


# ============================================
# Share comments endpoints
# ============================================


async def _review_share_for_comments(
    share_code: str, share_token: Optional[str], action: str
) -> dict:
    """The live review share behind ``share_code``, after the same checks a
    visitor passes on ``POST /shares/code/{code}``.

    The comment routes used to check only the code: a password-protected
    review's comments were readable (and writable) without the password, and
    GET also served inactive and expired shares.
    """
    share = await load_share(code=share_code)
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")

    if share["status"] == "cancelled":
        raise HTTPException(status_code=410, detail="Share cancelled")
    # Views are not counted again here: the visitor spent one on the page.
    if not is_live(share, count_views=False):
        raise HTTPException(status_code=410, detail="Share is not active")
    if share["share_type"] != "review":
        raise HTTPException(
            status_code=400, detail="Comments only available for review shares"
        )
    if share.get("resource_id") is None:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot {action}: share has no associated resource",
        )
    if has_password(share) and not await token_opens_share(share_token, share["id"]):
        raise HTTPException(
            status_code=401,
            detail="Password required",
            headers={"X-Share-Password-Required": "true"},
        )
    return share


@router.get("/code/{share_code}/comments", response_model=Envelope[list[ShareComment]])
async def get_share_comments(
    share_code: str,
    auth: OptionalAuthDep = None,
    share_token: ShareTokenQuery = None,
):
    """
    Get comments for a shared resource (public endpoint).

    Returns review_comments for the share's resource, ordered by created_at.
    A password-protected share needs ``share_token`` (the access_token).
    """
    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import ReviewComments

        share = await _review_share_for_comments(
            share_code, share_token, "load comments"
        )

        # Fetch comments for this share's resource
        async with read_scope() as session:
            comments = (
                (
                    await session.execute(
                        select(
                            ReviewComments.id,
                            ReviewComments.content,
                            ReviewComments.timecode,
                            ReviewComments.frame_number,
                            ReviewComments.status,
                            ReviewComments.author_id,
                            ReviewComments.parent_id,
                            ReviewComments.created_at,
                        )
                        .where(ReviewComments.resource_id == share["resource_id"])
                        .order_by(ReviewComments.created_at.asc())
                    )
                )
                .mappings()
                .all()
            )

        return {"success": True, "data": [_row_to_dict(c) for c in comments]}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get share comments: {e}")
        raise HTTPException(status_code=500, detail="Failed to get comments")


@router.post("/code/{share_code}/comments", response_model=Envelope[ShareCommentRow])
async def create_share_comment(
    share_code: str,
    body: ShareCommentCreate,
    auth: OptionalAuthDep = None,
    share_token: ShareTokenQuery = None,
):
    """
    Create a comment on a shared resource.

    Authenticated review-share members only (review_comments.author_id is
    NOT NULL — anonymous posts are rejected with 401).
    Only available for review-type shares; a password-protected share needs
    ``share_token`` (the access_token).
    """
    try:
        from sqlalchemy import insert

        from app.db.session import write_scope
        from app.models import ReviewComments

        share = await _review_share_for_comments(
            share_code, share_token, "add comments"
        )

        # author_id is NOT NULL in DB, so anonymous comments are rejected.
        if auth is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required to post comments",
            )

        comment_data = {
            "resource_id": share["resource_id"],
            "author_id": auth.user_id,
            "content": body.content,
            "timecode": body.timecode,
        }

        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        insert(ReviewComments)
                        .values(**comment_data)
                        .returning(*ReviewComments.__table__.columns)
                    )
                )
                .mappings()
                .first()
            )

        if not row:
            raise HTTPException(status_code=500, detail="Failed to create comment")

        comment = _row_to_dict(row)
        logger.info(f"Comment created on share {share_code} by {auth.user_id}")
        return {"success": True, "data": comment}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create share comment: {e}")
        raise HTTPException(status_code=500, detail="Failed to create comment")
