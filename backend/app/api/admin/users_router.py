"""Admin API routes for User management."""

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.repositories.admin.users_repository import AdminUsersRepository
from app.schemas.admin import (
    AdminUserResponse,
    AdminUserListResponse,
    AdminUserUpdate,
    AdminUserBanRequest,
)
from app.utils.admin_helpers import (
    create_audit_log,
    batch_get_user_auth_info,
    batch_get_user_counts,
    get_user_auth_info,
    get_user_video_count,
    get_user_team_count,
)


router = APIRouter()


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
    repo = AdminUsersRepository()
    rows, total = await repo.list_with_filters(
        page=page,
        page_size=page_size,
        search=search,
        role=role,
    )

    if not rows:
        return AdminUserListResponse(items=[], total=0, page=page, page_size=page_size)

    user_ids = [u["id"] for u in rows]

    # Batch fetch: counts and auth info in parallel
    user_counts, auth_info = await asyncio.gather(
        batch_get_user_counts(user_ids),
        batch_get_user_auth_info(user_ids),
    )

    # Build response
    items = []
    for u in rows:
        uid = u["id"]
        video_count, team_count = user_counts.get(uid, (0, 0))
        email, last_sign_in_at = auth_info.get(uid, (None, None))
        items.append(AdminUserResponse(
            id=str(uid),
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
        ))

    return AdminUserListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{user_id}", response_model=AdminUserResponse)
async def get_user(
    user_id: str,
    auth: AdminAuthDep,
):
    """Get detailed information about a specific user."""
    repo = AdminUsersRepository()
    u = await repo.get_by_id(user_id)

    if not u:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Get counts and auth info concurrently
    (video_count, team_count), (email, last_sign_in_at) = await asyncio.gather(
        asyncio.gather(get_user_video_count(user_id), get_user_team_count(user_id)),
        get_user_auth_info(user_id),
    )

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
    repo = AdminUsersRepository()

    if not await repo.exists(user_id):
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

    updated = await repo.update(user_id, update_data)
    if not updated:
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
    repo = AdminUsersRepository()

    if not await repo.exists(user_id):
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

    updated = await repo.set_banned(user_id, ban_request.is_banned)
    if not updated:
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
    repo = AdminUsersRepository()

    if not await repo.exists(user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if user_id == auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself",
        )

    updated = await repo.set_banned(user_id, True)
    if not updated:
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
