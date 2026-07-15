# backend/app/api/shares_router.py

"""
Shares Router

Sharing system API endpoints: create, list, update, cancel, and
public access by share code. Supports link, review, presentation,
and delivery share types.
"""

import secrets
import string
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from app.core.deps import AuthDep, OptionalAuthDep
from app.schemas.shares import ShareAccessRequest, ShareCreate, ShareUpdate


class ShareCommentCreate(BaseModel):
    """Request body for creating a comment on a shared resource."""

    content: str = Field(..., min_length=1, max_length=5000)
    timecode: Optional[float] = Field(None, ge=0, description="Timestamp in seconds")


router = APIRouter(prefix="/shares")

SHARE_CODE_LENGTH = 8
SHARE_CODE_ALPHABET = string.ascii_letters + string.digits


# ============================================
# Helper functions
# ============================================


def _generate_share_code(length: int = SHARE_CODE_LENGTH) -> str:
    """Generate a cryptographically random alphanumeric share code."""
    return "".join(secrets.choice(SHARE_CODE_ALPHABET) for _ in range(length))


def _build_share_url(share_code: str) -> str:
    """Build the public URL for a share code."""
    return f"/s/{share_code}"


def _is_expired(share: dict) -> bool:
    """Check whether a share has expired by time or max views."""
    if share.get("status") != "active":
        return True

    expires_at = share.get("expires_at")
    if expires_at:
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                pass
        if isinstance(expires_at, datetime):
            now = datetime.now(timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if now > expires_at:
                return True

    max_views = share.get("max_views")
    view_count = share.get("view_count", 0)
    if max_views is not None and view_count >= max_views:
        return True

    return False


def _enrich_share(share: dict) -> dict:
    """Add computed fields (share_url) and strip password hash from response."""
    share["share_url"] = _build_share_url(share.get("share_code", ""))
    # Never expose the actual password value; only indicate whether one is set
    share["has_password"] = share.get("password") is not None
    share.pop("password", None)
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


# ============================================
# Authenticated endpoints
# ============================================


@router.post("")
async def create_share(data: ShareCreate, auth: AuthDep):
    """
    Create a new share.

    At least one of resource_id, project_file_id, or folder_id must be
    provided.  A unique 8-character share code is generated automatically.

    Authentication: Bearer Token or API Key
    """
    # Validate that at least one target is specified
    if not any([data.resource_id, data.project_file_id, data.folder_id]):
        raise HTTPException(
            status_code=400,
            detail="At least one of resource_id, project_file_id, or folder_id is required.",
        )

    try:
        from sqlalchemy import insert, select

        from app.db.session import read_scope, write_scope
        from app.models import Shares

        # Generate a unique share code with retry
        share_code = _generate_share_code()
        for _ in range(5):
            async with read_scope() as session:
                exists = (
                    await session.execute(
                        select(Shares.id)
                        .where(Shares.share_code == share_code)
                        .limit(1)
                    )
                ).scalar()
            if exists is None:
                break
            share_code = _generate_share_code()
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to generate a unique share code. Please try again.",
            )

        # FK ids are BIGINT columns → coerce the schema's str fields to int;
        # expires_at is timestamptz → bind the native datetime (not isoformat).
        insert_data = {
            "share_code": share_code,
            "shared_by": auth.user_id,
            "share_type": data.share_type,
            "share_name": data.share_name,
            "allow_download": data.allow_download,
            "watermark": data.watermark,
        }

        if data.resource_id:
            insert_data["resource_id"] = int(data.resource_id)
        if data.project_file_id:
            insert_data["project_file_id"] = int(data.project_file_id)
        if data.folder_id:
            insert_data["folder_id"] = int(data.folder_id)
        if data.version_id:
            insert_data["version_id"] = int(data.version_id)
        if data.password:
            insert_data["password"] = data.password
        if data.expires_at:
            insert_data["expires_at"] = data.expires_at
        if data.max_views is not None:
            insert_data["max_views"] = data.max_views
        if data.team_id:
            insert_data["team_id"] = int(data.team_id)

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


@router.get("")
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
        elif team_id:
            stmt = stmt.where(Shares.team_id == int(team_id))

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


@router.get("/{share_id}")
async def get_share(share_id: str, auth: AuthDep):
    """
    Get detailed information about a share (owner only).

    Returns full share details including view statistics.

    Authentication: Bearer Token or API Key
    """
    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import Shares, ShareViews

        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(*Shares.__table__.columns)
                        .where(Shares.id == int(share_id))
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )

        if not row:
            raise HTTPException(status_code=404, detail="Share not found")

        share = _row_to_dict(row)

        if share["shared_by"] != auth.user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to view this share"
            )

        share = _enrich_share(share)

        # Fetch recent view records
        async with read_scope() as session:
            views = (
                (
                    await session.execute(
                        select(*ShareViews.__table__.columns)
                        .where(ShareViews.share_id == int(share_id))
                        .order_by(ShareViews.last_viewed_at.desc())
                        .limit(50)
                    )
                )
                .mappings()
                .all()
            )
        share["recent_views"] = [_row_to_dict(v) for v in views]

        return {"success": True, "data": share}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get share {share_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get share")


@router.put("/{share_id}")
async def update_share(share_id: str, data: ShareUpdate, auth: AuthDep):
    """
    Update share settings (owner only).

    Allows modifying password, expiration, download permission, and watermark.

    Authentication: Bearer Token or API Key
    """
    try:
        from sqlalchemy import select, update

        from app.db.session import read_scope, write_scope
        from app.models import Shares

        # Verify ownership
        async with read_scope() as session:
            existing = (
                (
                    await session.execute(
                        select(Shares.id, Shares.shared_by, Shares.status)
                        .where(Shares.id == int(share_id))
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )

        if not existing:
            raise HTTPException(status_code=404, detail="Share not found")

        if existing["shared_by"] != auth.user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to update this share"
            )

        if existing["status"] == "cancelled":
            raise HTTPException(
                status_code=400, detail="Cannot update a cancelled share"
            )

        update_data = {}

        if data.share_name is not None:
            update_data["share_name"] = data.share_name
        if data.password is not None:
            # Empty string means remove password
            update_data["password"] = data.password if data.password else None
        if data.allow_download is not None:
            update_data["allow_download"] = data.allow_download
        if data.expires_at is not None:
            update_data["expires_at"] = data.expires_at
        if data.max_views is not None:
            update_data["max_views"] = data.max_views
        if data.watermark is not None:
            update_data["watermark"] = data.watermark

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        update(Shares)
                        .where(Shares.id == int(share_id))
                        .values(**update_data)
                        .returning(*Shares.__table__.columns)
                    )
                )
                .mappings()
                .first()
            )

        if not row:
            raise HTTPException(status_code=500, detail="Failed to update share")

        updated = _enrich_share(_row_to_dict(row))
        logger.info(f"Share {share_id} updated by user {auth.user_id}")
        return {"success": True, "data": updated}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update share {share_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update share")


@router.delete("/{share_id}")
async def toggle_share_status(share_id: str, auth: AuthDep):
    """
    Toggle share status: active → inactive, inactive → active.

    Authentication: Bearer Token or API Key
    """
    try:
        from sqlalchemy import select, update

        from app.db.session import read_scope, write_scope
        from app.models import Shares

        async with read_scope() as session:
            existing = (
                (
                    await session.execute(
                        select(Shares.id, Shares.shared_by, Shares.status)
                        .where(Shares.id == int(share_id))
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )

        if not existing:
            raise HTTPException(status_code=404, detail="Share not found")

        if existing["shared_by"] != auth.user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        # Toggle: active → inactive, inactive/cancelled → active
        new_status = "inactive" if existing["status"] == "active" else "active"

        async with write_scope() as session:
            updated = (
                await session.execute(
                    update(Shares)
                    .where(Shares.id == int(share_id))
                    .values(status=new_status)
                    .returning(Shares.id)
                )
            ).scalar()

        if updated is None:
            raise HTTPException(status_code=500, detail="Failed to update share status")

        logger.info(f"Share {share_id} toggled to {new_status} by user {auth.user_id}")
        return {"success": True, "message": f"Share {new_status}", "status": new_status}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to toggle share {share_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update share")


@router.delete("/{share_id}/permanent")
async def delete_share_permanent(share_id: str, auth: AuthDep):
    """
    Permanently delete a share record (hard delete).

    Authentication: Bearer Token or API Key
    """
    try:
        from sqlalchemy import delete, select

        from app.db.session import read_scope, write_scope
        from app.models import Shares

        async with read_scope() as session:
            existing = (
                (
                    await session.execute(
                        select(Shares.id, Shares.shared_by)
                        .where(Shares.id == int(share_id))
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )

        if not existing:
            raise HTTPException(status_code=404, detail="Share not found")

        if existing["shared_by"] != auth.user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        async with write_scope() as session:
            await session.execute(delete(Shares).where(Shares.id == int(share_id)))

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


@router.post("/code/{share_code}")
async def access_share_by_code(
    share_code: str,
    body: ShareAccessRequest,
    auth: OptionalAuthDep = None,
):
    """
    Public: access shared content by share code.

    Validates password (if set), checks expiration and max view limits,
    increments view_count, and records a view in share_views.

    Authentication: Optional (viewer identity is recorded if authenticated)
    """
    try:
        from sqlalchemy import insert, select, update

        from app.db.session import read_scope, write_scope
        from app.models import Resources, Shares, ShareViews

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

        # Check status
        if share["status"] in ("cancelled", "inactive"):
            raise HTTPException(
                status_code=410, detail="This share is no longer available"
            )

        # Check expiration
        if _is_expired(share):
            # Auto-update status to expired if it was still active
            if share["status"] == "active":
                async with write_scope() as session:
                    await session.execute(
                        update(Shares)
                        .where(Shares.id == share["id"])
                        .values(status="expired")
                    )
            raise HTTPException(status_code=410, detail="This share has expired")

        # Check password
        if share.get("password"):
            if not body.password:
                raise HTTPException(
                    status_code=401,
                    detail="Password required",
                    headers={"X-Share-Password-Required": "true"},
                )
            if body.password != share["password"]:
                raise HTTPException(status_code=401, detail="Incorrect password")

        # Increment view_count on the share
        new_view_count = (share.get("view_count") or 0) + 1
        async with write_scope() as session:
            await session.execute(
                update(Shares)
                .where(Shares.id == share["id"])
                .values(view_count=new_view_count)
            )

        # Record/update share_views. last_viewed_at is timestamptz → bind a
        # native datetime (asyncpg rejects ISO strings).
        viewer_id = auth.user_id if auth else None
        now_dt = datetime.now(timezone.utc)

        if viewer_id:
            # Check if this viewer already has a record
            async with read_scope() as session:
                view_record = (
                    (
                        await session.execute(
                            select(ShareViews.id, ShareViews.view_count)
                            .where(ShareViews.share_id == share["id"])
                            .where(ShareViews.viewer_id == viewer_id)
                            .limit(1)
                        )
                    )
                    .mappings()
                    .first()
                )

            if view_record:
                # Update existing view record
                async with write_scope() as session:
                    await session.execute(
                        update(ShareViews)
                        .where(ShareViews.id == view_record["id"])
                        .values(
                            view_count=view_record["view_count"] + 1,
                            last_viewed_at=now_dt,
                        )
                    )
            else:
                # Create new view record
                async with write_scope() as session:
                    await session.execute(
                        insert(ShareViews).values(
                            share_id=share["id"],
                            viewer_id=viewer_id,
                            last_viewed_at=now_dt,
                        )
                    )
        else:
            # Anonymous viewer: create a record without viewer_id
            async with write_scope() as session:
                await session.execute(
                    insert(ShareViews).values(
                        share_id=share["id"],
                        last_viewed_at=now_dt,
                    )
                )

        # Fetch resource metadata for preview (mime_type, filename, cover)
        resource_meta = {}
        if share.get("resource_id"):
            try:
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
                                .where(Resources.id == share["resource_id"])
                                .limit(1)
                            )
                        )
                        .mappings()
                        .first()
                    )
                if res:
                    resource_meta = {
                        "mime_type": res.get("mime_type"),
                        "file_type": res.get("file_type"),
                        "filename": res.get("filename"),
                        "cover_image_path": res.get("cover_image_path"),
                        "thumbnail_path": res.get("thumbnail_path"),
                        "media_id": (
                            str(res["media_id"]) if res.get("media_id") else None
                        ),
                    }
            except Exception as e:
                logger.warning(f"Failed to fetch resource metadata for share: {e}")

        # Build the response (strip sensitive fields)
        share["view_count"] = new_view_count
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
            **resource_meta,
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


@router.get("/code/{share_code}/comments")
async def get_share_comments(
    share_code: str,
    auth: OptionalAuthDep = None,
):
    """
    Get comments for a shared resource (public endpoint).

    Returns review_comments for the share's resource, ordered by created_at.
    """
    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import ReviewComments, Shares

        # Look up share
        async with read_scope() as session:
            share = (
                (
                    await session.execute(
                        select(
                            Shares.id,
                            Shares.status,
                            Shares.share_type,
                            Shares.resource_id,
                        )
                        .where(Shares.share_code == share_code)
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )

        if not share:
            raise HTTPException(status_code=404, detail="Share not found")

        if share["status"] == "cancelled":
            raise HTTPException(status_code=410, detail="Share cancelled")
        if share["share_type"] != "review":
            raise HTTPException(
                status_code=400, detail="Comments only available for review shares"
            )
        if share.get("resource_id") is None:
            raise HTTPException(
                status_code=400,
                detail="Cannot load comments: share has no associated resource",
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


@router.post("/code/{share_code}/comments")
async def create_share_comment(
    share_code: str,
    body: ShareCommentCreate,
    auth: OptionalAuthDep = None,
):
    """
    Create a comment on a shared resource.

    Authenticated review-share members only (review_comments.author_id is
    NOT NULL — anonymous posts are rejected with 401).
    Only available for review-type shares.
    """
    try:
        from sqlalchemy import insert, select

        from app.db.session import read_scope, write_scope
        from app.models import ReviewComments, Shares

        # Look up share
        async with read_scope() as session:
            share = (
                (
                    await session.execute(
                        select(
                            Shares.id,
                            Shares.status,
                            Shares.share_type,
                            Shares.resource_id,
                        )
                        .where(Shares.share_code == share_code)
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )

        if not share:
            raise HTTPException(status_code=404, detail="Share not found")

        if share["status"] != "active":
            raise HTTPException(status_code=410, detail="Share is not active")
        if share["share_type"] != "review":
            raise HTTPException(
                status_code=400, detail="Comments only available for review shares"
            )
        if share.get("resource_id") is None:
            raise HTTPException(
                status_code=400,
                detail="Cannot add comments: share has no associated resource",
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
        logger.info(
            f"Comment created on share {share_code} by "
            f"{auth.user_id if auth else 'anonymous'}"
        )
        return {"success": True, "data": comment}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create share comment: {e}")
        raise HTTPException(status_code=500, detail="Failed to create comment")
