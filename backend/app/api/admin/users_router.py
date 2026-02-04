"""Admin API routes for User management."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.db import get_async_supabase, get_async_supabase_admin
from app.schemas.admin import (
    AdminUserResponse,
    AdminUserListResponse,
    AdminUserUpdate,
    AdminUserBanRequest,
)


router = APIRouter()


# ============================================
# Helper Functions
# ============================================


async def create_audit_log(
    admin_id: str,
    action: str,
    target_type: str,
    target_id: str,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Create an audit log entry for admin actions."""
    try:
        supabase = await get_async_supabase_admin()
        await supabase.table("audit_logs").insert({
            "admin_id": admin_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "details": details,
            "ip_address": ip_address,
        }).execute()
    except Exception as e:
        logger.error(f"Failed to create audit log: {e}")
        # Don't raise - audit log failures shouldn't block the main operation


async def get_user_email_by_id(user_id: str) -> Optional[str]:
    """Get user email from Supabase Auth."""
    try:
        supabase = await get_async_supabase_admin()
        response = await supabase.auth.admin.get_user_by_id(user_id)
        if response and response.user:
            return response.user.email
        return None
    except Exception as e:
        logger.warning(f"Failed to get user email for {user_id}: {e}")
        return None


# ============================================
# User Endpoints
# ============================================


@router.get("", response_model=AdminUserListResponse)
async def list_users(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search by username or email"),
    role: Optional[str] = Query(None, description="Filter by role"),
):
    """
    List all users with pagination and filters.

    - **page**: Page number (starts at 1)
    - **page_size**: Number of items per page (max 100)
    - **search**: Search by username
    - **role**: Filter by role (admin, user, test)
    """
    supabase = await get_async_supabase_admin()

    # Build query
    query = supabase.table("user_profiles").select("*", count="exact")

    # Apply filters
    if search:
        query = query.ilike("username", f"%{search}%")

    if role:
        query = query.eq("role", role)

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.order("created_at", desc=True).range(offset, offset + page_size - 1)

    # Execute query
    result = await query.execute()

    if not result.data:
        return AdminUserListResponse(items=[], total=0, page=page, page_size=page_size)

    # Get video counts for each user
    user_ids = [u["id"] for u in result.data]
    video_counts = {}
    team_counts = {}
    user_emails = {}
    last_sign_ins = {}

    # Get video counts
    for user_id in user_ids:
        video_result = await supabase.table("douyin_videos").select("id", count="exact").eq("user_id", user_id).execute()
        video_counts[user_id] = video_result.count or 0

        # Get team counts
        team_result = await supabase.table("team_members").select("team_id", count="exact").eq("user_id", user_id).execute()
        team_counts[user_id] = team_result.count or 0

        # Get email and last sign in from auth
        try:
            auth_response = await supabase.auth.admin.get_user_by_id(user_id)
            if auth_response and auth_response.user:
                user_emails[user_id] = auth_response.user.email
                last_sign_ins[user_id] = auth_response.user.last_sign_in_at
        except Exception as e:
            logger.warning(f"Failed to get auth info for user {user_id}: {e}")

    # Build response
    items = [
        AdminUserResponse(
            id=str(u["id"]),
            email=user_emails.get(u["id"]),
            username=u.get("username"),
            avatar_url=u.get("avatar_url"),
            role=str(u.get("role", "user")),
            is_banned=u.get("is_banned", False),
            created_at=u["created_at"],
            updated_at=u.get("updated_at"),
            last_sign_in_at=last_sign_ins.get(u["id"]),
            video_count=video_counts.get(u["id"], 0),
            team_count=team_counts.get(u["id"], 0),
        )
        for u in result.data
    ]

    return AdminUserListResponse(
        items=items,
        total=result.count or len(items),
        page=page,
        page_size=page_size,
    )


@router.get("/{user_id}", response_model=AdminUserResponse)
async def get_user(
    user_id: str,
    auth: AdminAuthDep,
):
    """Get detailed information about a specific user."""
    supabase = await get_async_supabase_admin()

    # Get user profile
    result = await supabase.table("user_profiles").select("*").eq("id", user_id).single().execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    u = result.data

    # Get video count
    video_result = await supabase.table("douyin_videos").select("id", count="exact").eq("user_id", user_id).execute()
    video_count = video_result.count or 0

    # Get team count
    team_result = await supabase.table("team_members").select("team_id", count="exact").eq("user_id", user_id).execute()
    team_count = team_result.count or 0

    # Get email and last sign in from auth
    email = None
    last_sign_in_at = None
    try:
        auth_response = await supabase.auth.admin.get_user_by_id(user_id)
        if auth_response and auth_response.user:
            email = auth_response.user.email
            last_sign_in_at = auth_response.user.last_sign_in_at
    except Exception as e:
        logger.warning(f"Failed to get auth info for user {user_id}: {e}")

    return AdminUserResponse(
        id=str(u["id"]),
        email=email,
        username=u.get("username"),
        avatar_url=u.get("avatar_url"),
        role=str(u.get("role", "user")),
        is_banned=u.get("is_banned", False),
        created_at=u["created_at"],
        updated_at=u.get("updated_at"),
        last_sign_in_at=last_sign_in_at,
        video_count=video_count,
        team_count=team_count,
    )


@router.patch("/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: str,
    update: AdminUserUpdate,
    auth: AdminAuthDep,
    request: Request,
):
    """
    Update a user's role or banned status.

    - **role**: New role (admin, user, test)
    - **is_banned**: Whether user is banned
    """
    supabase = await get_async_supabase_admin()

    # Check if user exists
    existing = await supabase.table("user_profiles").select("id").eq("id", user_id).single().execute()
    if not existing.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent self-modification of role
    if update.role is not None and user_id == auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot modify your own role",
        )

    # Build update data
    update_data = {}
    if update.role is not None:
        if update.role not in ["admin", "user", "test"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid role. Must be one of: admin, user, test",
            )
        update_data["role"] = update.role

    if update.is_banned is not None:
        update_data["is_banned"] = update.is_banned

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No update data provided",
        )

    # Update user
    result = await supabase.table("user_profiles").update(update_data).eq("id", user_id).execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user",
        )

    # Get client IP for audit log
    client_ip = request.client.host if request.client else None

    # Create audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_user",
        target_type="user",
        target_id=user_id,
        details={"changes": update_data},
        ip_address=client_ip,
    )

    # Return updated user
    return await get_user(user_id, auth)


@router.post("/{user_id}/ban", response_model=AdminUserResponse)
async def ban_user(
    user_id: str,
    ban_request: AdminUserBanRequest,
    auth: AdminAuthDep,
    request: Request,
):
    """
    Ban or unban a user.

    - **is_banned**: True to ban, False to unban
    - **reason**: Optional reason for the ban
    """
    supabase = await get_async_supabase_admin()

    # Check if user exists
    existing = await supabase.table("user_profiles").select("id").eq("id", user_id).single().execute()
    if not existing.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent self-ban
    if user_id == auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot ban yourself",
        )

    # Update ban status
    result = await supabase.table("user_profiles").update({
        "is_banned": ban_request.is_banned,
    }).eq("id", user_id).execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update ban status",
        )

    # Get client IP for audit log
    client_ip = request.client.host if request.client else None

    # Create audit log
    action = "ban_user" if ban_request.is_banned else "unban_user"
    await create_audit_log(
        admin_id=auth.user_id,
        action=action,
        target_type="user",
        target_id=user_id,
        details={"reason": ban_request.reason} if ban_request.reason else None,
        ip_address=client_ip,
    )

    logger.info(f"User {user_id} {'banned' if ban_request.is_banned else 'unbanned'} by admin {auth.user_id}")

    # Return updated user
    return await get_user(user_id, auth)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    auth: AdminAuthDep,
    request: Request,
):
    """
    Soft delete a user by banning them.

    Note: This does not actually delete the user, but bans them instead.
    For full deletion, use Supabase Admin Console.
    """
    supabase = await get_async_supabase_admin()

    # Check if user exists
    existing = await supabase.table("user_profiles").select("id").eq("id", user_id).single().execute()
    if not existing.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent self-deletion
    if user_id == auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself",
        )

    # Soft delete by banning
    result = await supabase.table("user_profiles").update({
        "is_banned": True,
    }).eq("id", user_id).execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete user",
        )

    # Get client IP for audit log
    client_ip = request.client.host if request.client else None

    # Create audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="delete_user",
        target_type="user",
        target_id=user_id,
        ip_address=client_ip,
    )

    logger.info(f"User {user_id} soft-deleted by admin {auth.user_id}")
