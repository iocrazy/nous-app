# backend/app/api/shares_router.py

"""
Shares Router

Sharing system API endpoints: create, list, update, cancel, and
public access by share code. Supports link, review, presentation,
and delivery share types.
"""

import secrets
import string
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from app.core.deps import AuthDep, OptionalAuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.schemas.shares import ShareAccessRequest, ShareCreate, ShareUpdate


class ShareCommentCreate(BaseModel):
    """Request body for creating a comment on a shared resource."""

    content: str = Field(..., min_length=1, max_length=5000)
    timecode: Optional[float] = Field(None, ge=0, description="Timestamp in seconds")
    visibility: str = Field("all", pattern="^(all|team|private)$")


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
        client = await get_async_supabase_admin()

        # Generate a unique share code with retry
        share_code = _generate_share_code()
        for _ in range(5):
            existing = (
                await client.table("shares")
                .select("id")
                .eq("share_code", share_code)
                .execute()
            )
            if not existing.data:
                break
            share_code = _generate_share_code()
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to generate a unique share code. Please try again.",
            )

        insert_data = {
            "share_code": share_code,
            "shared_by": auth.user_id,
            "share_type": data.share_type,
            "share_name": data.share_name,
            "allow_download": data.allow_download,
            "watermark": data.watermark,
        }

        if data.resource_id:
            insert_data["resource_id"] = data.resource_id
        if data.project_file_id:
            insert_data["project_file_id"] = data.project_file_id
        if data.folder_id:
            insert_data["folder_id"] = data.folder_id
        if data.version_id:
            insert_data["version_id"] = data.version_id
        if data.password:
            insert_data["password"] = data.password
        if data.expires_at:
            insert_data["expires_at"] = data.expires_at.isoformat()
        if data.max_views is not None:
            insert_data["max_views"] = data.max_views
        if data.team_id:
            insert_data["team_id"] = data.team_id

        result = await client.table("shares").insert(insert_data).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create share")

        share = _enrich_share(result.data[0])
        logger.info(f"Share created: {share_code} by user {auth.user_id}")
        return {"success": True, "data": share}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to create share: {e}")
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
        client = await get_async_supabase_admin()

        query = (
            client.table("shares")
            .select("*")
            .eq("shared_by", auth.user_id)
            .order("created_at", desc=True)
        )

        if team_id == "personal":
            query = query.is_("team_id", "null")
        elif team_id:
            query = query.eq("team_id", team_id)

        if share_type:
            query = query.eq("share_type", share_type)
        if status:
            query = query.eq("status", status)

        query = query.range(offset, offset + limit - 1)
        result = await query.execute()

        shares = [_enrich_share(s) for s in (result.data or [])]

        return {
            "success": True,
            "data": shares,
            "count": len(shares),
        }

    except Exception as e:
        logger.exception(f"Failed to list shares: {e}")
        raise HTTPException(status_code=500, detail="Failed to list shares")


@router.get("/{share_id}")
async def get_share(share_id: str, auth: AuthDep):
    """
    Get detailed information about a share (owner only).

    Returns full share details including view statistics.

    Authentication: Bearer Token or API Key
    """
    try:
        client = await get_async_supabase_admin()

        result = await client.table("shares").select("*").eq("id", share_id).execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Share not found")

        share = result.data[0]

        if share["shared_by"] != auth.user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to view this share"
            )

        share = _enrich_share(share)

        # Fetch recent view records
        views_result = (
            await client.table("share_views")
            .select("*")
            .eq("share_id", share_id)
            .order("last_viewed_at", desc=True)
            .limit(50)
            .execute()
        )
        share["recent_views"] = views_result.data or []

        return {"success": True, "data": share}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get share {share_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get share")


@router.put("/{share_id}")
async def update_share(share_id: str, data: ShareUpdate, auth: AuthDep):
    """
    Update share settings (owner only).

    Allows modifying password, expiration, download permission, and watermark.

    Authentication: Bearer Token or API Key
    """
    try:
        client = await get_async_supabase_admin()

        # Verify ownership
        existing = (
            await client.table("shares")
            .select("id, shared_by, status")
            .eq("id", share_id)
            .execute()
        )

        if not existing.data:
            raise HTTPException(status_code=404, detail="Share not found")

        share = existing.data[0]

        if share["shared_by"] != auth.user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to update this share"
            )

        if share["status"] == "cancelled":
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
            update_data["expires_at"] = data.expires_at.isoformat()
        if data.max_views is not None:
            update_data["max_views"] = data.max_views
        if data.watermark is not None:
            update_data["watermark"] = data.watermark

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        result = (
            await client.table("shares")
            .update(update_data)
            .eq("id", share_id)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to update share")

        updated = _enrich_share(result.data[0])
        logger.info(f"Share {share_id} updated by user {auth.user_id}")
        return {"success": True, "data": updated}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to update share {share_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update share")


@router.delete("/{share_id}")
async def toggle_share_status(share_id: str, auth: AuthDep):
    """
    Toggle share status: active → inactive, inactive → active.

    Authentication: Bearer Token or API Key
    """
    try:
        client = await get_async_supabase_admin()

        existing = (
            await client.table("shares")
            .select("id, shared_by, status")
            .eq("id", share_id)
            .execute()
        )

        if not existing.data:
            raise HTTPException(status_code=404, detail="Share not found")

        share = existing.data[0]

        if share["shared_by"] != auth.user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        # Toggle: active → inactive, inactive/cancelled → active
        new_status = "inactive" if share["status"] == "active" else "active"

        result = (
            await client.table("shares")
            .update({"status": new_status})
            .eq("id", share_id)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to update share status")

        logger.info(f"Share {share_id} toggled to {new_status} by user {auth.user_id}")
        return {"success": True, "message": f"Share {new_status}", "status": new_status}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to toggle share {share_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update share")


@router.delete("/{share_id}/permanent")
async def delete_share_permanent(share_id: str, auth: AuthDep):
    """
    Permanently delete a share record (hard delete).

    Authentication: Bearer Token or API Key
    """
    try:
        client = await get_async_supabase_admin()

        existing = (
            await client.table("shares")
            .select("id, shared_by")
            .eq("id", share_id)
            .execute()
        )

        if not existing.data:
            raise HTTPException(status_code=404, detail="Share not found")

        share = existing.data[0]

        if share["shared_by"] != auth.user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        await client.table("shares").delete().eq("id", share_id).execute()

        logger.info(f"Share {share_id} permanently deleted by user {auth.user_id}")
        return {"success": True, "message": "Share deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to delete share {share_id}: {e}")
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
        client = await get_async_supabase_admin()

        # Look up the share by code
        result = (
            await client.table("shares")
            .select("*")
            .eq("share_code", share_code)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=404, detail="Share not found")

        share = result.data[0]

        # Check status
        if share["status"] in ("cancelled", "inactive"):
            raise HTTPException(
                status_code=410, detail="This share is no longer available"
            )

        # Check expiration
        if _is_expired(share):
            # Auto-update status to expired if it was still active
            if share["status"] == "active":
                await (
                    client.table("shares")
                    .update({"status": "expired"})
                    .eq("id", share["id"])
                    .execute()
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
        await (
            client.table("shares")
            .update({"view_count": new_view_count})
            .eq("id", share["id"])
            .execute()
        )

        # Record/update share_views
        viewer_id = auth.user_id if auth else None

        if viewer_id:
            # Check if this viewer already has a record
            existing_view = (
                await client.table("share_views")
                .select("id, view_count")
                .eq("share_id", share["id"])
                .eq("viewer_id", viewer_id)
                .execute()
            )

            if existing_view.data:
                # Update existing view record
                view_record = existing_view.data[0]
                await (
                    client.table("share_views")
                    .update(
                        {
                            "view_count": view_record["view_count"] + 1,
                            "last_viewed_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    .eq("id", view_record["id"])
                    .execute()
                )
            else:
                # Create new view record
                await (
                    client.table("share_views")
                    .insert(
                        {
                            "share_id": share["id"],
                            "viewer_id": viewer_id,
                            "last_viewed_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    .execute()
                )
        else:
            # Anonymous viewer: create a record without viewer_id
            await (
                client.table("share_views")
                .insert(
                    {
                        "share_id": share["id"],
                        "last_viewed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                .execute()
            )

        # Fetch resource metadata for preview (mime_type, filename, cover)
        resource_meta = {}
        if share.get("resource_id"):
            try:
                res_data = (
                    await client.table("resources")
                    .select(
                        "mime_type, file_type, filename, cover_image_path, thumbnail_path, media_id"
                    )
                    .eq("id", share["resource_id"])
                    .maybe_single()
                    .execute()
                )
                if res_data.data:
                    resource_meta = {
                        "mime_type": res_data.data.get("mime_type"),
                        "file_type": res_data.data.get("file_type"),
                        "filename": res_data.data.get("filename"),
                        "cover_image_path": res_data.data.get("cover_image_path"),
                        "thumbnail_path": res_data.data.get("thumbnail_path"),
                        "media_id": (
                            str(res_data.data["media_id"])
                            if res_data.data.get("media_id")
                            else None
                        ),
                    }
            except Exception as e:
                logger.opt(exception=True).warning(f"Failed to fetch resource metadata for share: {e}")

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
        logger.exception(f"Failed to access share {share_code}: {e}")
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

    Returns comments ordered by timestamp_seconds (if present) then created_at.
    """
    try:
        client = await get_async_supabase_admin()

        # Look up share
        share_result = (
            await client.table("shares")
            .select("id, status, share_type")
            .eq("share_code", share_code)
            .execute()
        )

        if not share_result.data:
            raise HTTPException(status_code=404, detail="Share not found")

        share = share_result.data[0]

        if share["status"] == "cancelled":
            raise HTTPException(status_code=410, detail="Share cancelled")
        if share["share_type"] != "review":
            raise HTTPException(
                status_code=400, detail="Comments only available for review shares"
            )

        # Fetch comments for this share
        comments_result = (
            await client.table("review_comments")
            .select("id, content, timestamp_seconds, visibility, author_id, created_at")
            .eq("share_id", share["id"])
            .order("created_at", desc=False)
            .execute()
        )

        return {"success": True, "data": comments_result.data or []}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get share comments: {e}")
        raise HTTPException(status_code=500, detail="Failed to get comments")


@router.post("/code/{share_code}/comments")
async def create_share_comment(
    share_code: str,
    body: ShareCommentCreate,
    auth: OptionalAuthDep = None,
):
    """
    Create a comment on a shared resource.

    Supports both authenticated and anonymous comments.
    Only available for review-type shares.
    """
    try:
        client = await get_async_supabase_admin()

        # Look up share
        share_result = (
            await client.table("shares")
            .select("id, status, share_type, resource_id, project_file_id")
            .eq("share_code", share_code)
            .execute()
        )

        if not share_result.data:
            raise HTTPException(status_code=404, detail="Share not found")

        share = share_result.data[0]

        if share["status"] != "active":
            raise HTTPException(status_code=410, detail="Share is not active")
        if share["share_type"] != "review":
            raise HTTPException(
                status_code=400, detail="Comments only available for review shares"
            )

        # Build comment record
        # file_id is required in DB; use project_file_id from share if available
        file_id = share.get("project_file_id")
        if not file_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot add comments: share has no associated project file",
            )

        comment_data = {
            "file_id": file_id,
            "share_id": share["id"],
            "content": body.content,
            "timestamp_seconds": body.timecode,
            "visibility": body.visibility,
            "author_id": auth.user_id if auth else None,
        }

        # Remove None author_id for anonymous
        if comment_data["author_id"] is None:
            # author_id is NOT NULL in DB, so anonymous comments need
            # a sentinel value or we skip. For now, require auth.
            raise HTTPException(
                status_code=401,
                detail="Authentication required to post comments",
            )

        result = await client.table("review_comments").insert(comment_data).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create comment")

        comment = result.data[0]
        logger.info(
            f"Comment created on share {share_code} by "
            f"{auth.user_id if auth else 'anonymous'}"
        )
        return {"success": True, "data": comment}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to create share comment: {e}")
        raise HTTPException(status_code=500, detail="Failed to create comment")
