"""Admin API routes for Team management."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.db import get_async_supabase_admin
from app.schemas.admin import (
    AdminTeamResponse,
    AdminTeamListResponse,
    AdminTeamMemberResponse,
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


async def get_user_info_by_id(user_id: str) -> tuple[Optional[str], Optional[str]]:
    """Get user email and username from Supabase Auth and profiles."""
    try:
        supabase = await get_async_supabase_admin()

        # Get email from auth
        email = None
        try:
            auth_response = await supabase.auth.admin.get_user_by_id(user_id)
            if auth_response and auth_response.user:
                email = auth_response.user.email
        except Exception as e:
            logger.warning(f"Failed to get auth info for user {user_id}: {e}")

        # Get username from profile
        username = None
        try:
            profile_result = await supabase.table("user_profiles").select("username").eq("id", user_id).single().execute()
            if profile_result.data:
                username = profile_result.data.get("username")
        except Exception as e:
            logger.warning(f"Failed to get profile for user {user_id}: {e}")

        return email, username
    except Exception as e:
        logger.warning(f"Failed to get user info for {user_id}: {e}")
        return None, None


# ============================================
# Team Endpoints
# ============================================


@router.get("", response_model=AdminTeamListResponse)
async def list_teams(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search by team name"),
):
    """
    List all teams with pagination and filters.

    - **page**: Page number (starts at 1)
    - **page_size**: Number of items per page (max 100)
    - **search**: Search by team name
    """
    supabase = await get_async_supabase_admin()

    # Build query
    query = supabase.table("teams").select("*", count="exact")

    # Apply filters
    if search:
        query = query.ilike("name", f"%{search}%")

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.order("created_at", desc=True).range(offset, offset + page_size - 1)

    # Execute query
    result = await query.execute()

    if not result.data:
        return AdminTeamListResponse(items=[], total=0, page=page, page_size=page_size)

    # Get member counts and owner info for each team
    items = []
    for team in result.data:
        # Get member count
        member_result = await supabase.table("team_members").select("user_id", count="exact").eq("team_id", team["id"]).execute()
        member_count = member_result.count or 0

        # Get owner info
        owner_email, owner_username = await get_user_info_by_id(team["owner_id"])

        items.append(AdminTeamResponse(
            id=str(team["id"]),
            name=team["name"],
            owner_id=str(team["owner_id"]),
            owner_email=owner_email,
            owner_username=owner_username,
            invite_code=team["invite_code"],
            description=team.get("description"),
            member_count=member_count,
            created_at=team["created_at"],
        ))

    return AdminTeamListResponse(
        items=items,
        total=result.count or len(items),
        page=page,
        page_size=page_size,
    )


@router.get("/{team_id}", response_model=AdminTeamResponse)
async def get_team(
    team_id: str,
    auth: AdminAuthDep,
):
    """Get detailed information about a specific team."""
    supabase = await get_async_supabase_admin()

    # Get team
    result = await supabase.table("teams").select("*").eq("id", team_id).single().execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found",
        )

    team = result.data

    # Get member count
    member_result = await supabase.table("team_members").select("user_id", count="exact").eq("team_id", team_id).execute()
    member_count = member_result.count or 0

    # Get owner info
    owner_email, owner_username = await get_user_info_by_id(team["owner_id"])

    return AdminTeamResponse(
        id=str(team["id"]),
        name=team["name"],
        owner_id=str(team["owner_id"]),
        owner_email=owner_email,
        owner_username=owner_username,
        invite_code=team["invite_code"],
        description=team.get("description"),
        member_count=member_count,
        created_at=team["created_at"],
    )


@router.get("/{team_id}/members", response_model=list[AdminTeamMemberResponse])
async def get_team_members(
    team_id: str,
    auth: AdminAuthDep,
):
    """Get all members of a specific team."""
    supabase = await get_async_supabase_admin()

    # Check if team exists
    team_result = await supabase.table("teams").select("id").eq("id", team_id).single().execute()
    if not team_result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found",
        )

    # Get team members
    result = await supabase.table("team_members").select("*").eq("team_id", team_id).order("joined_at", desc=False).execute()

    if not result.data:
        return []

    # Build response with user info
    members = []
    for member in result.data:
        email, username = await get_user_info_by_id(member["user_id"])
        members.append(AdminTeamMemberResponse(
            user_id=str(member["user_id"]),
            email=email,
            username=username,
            role=member["role"],
            joined_at=member["joined_at"],
        ))

    return members


@router.post("/{team_id}/transfer-ownership", response_model=AdminTeamResponse)
async def transfer_team_ownership(
    team_id: str,
    auth: AdminAuthDep,
    request: Request,
    new_owner_id: str = Query(..., description="ID of the new owner"),
):
    """
    Transfer team ownership to another member.

    - **new_owner_id**: User ID of the new owner (must be an existing team member)
    """
    supabase = await get_async_supabase_admin()

    # Check if team exists
    team_result = await supabase.table("teams").select("*").eq("id", team_id).single().execute()
    if not team_result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found",
        )

    team = team_result.data
    old_owner_id = team["owner_id"]

    # Check if new owner is same as current owner
    if new_owner_id == old_owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New owner is the same as current owner",
        )

    # Check if new owner is a team member
    member_result = await supabase.table("team_members").select("*").eq("team_id", team_id).eq("user_id", new_owner_id).single().execute()
    if not member_result.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New owner must be an existing team member",
        )

    # Update team owner
    await supabase.table("teams").update({
        "owner_id": new_owner_id,
    }).eq("id", team_id).execute()

    # Update team_members roles: new owner becomes 'owner', old owner becomes 'member'
    await supabase.table("team_members").update({
        "role": "owner",
    }).eq("team_id", team_id).eq("user_id", new_owner_id).execute()

    await supabase.table("team_members").update({
        "role": "member",
    }).eq("team_id", team_id).eq("user_id", old_owner_id).execute()

    # Get client IP for audit log
    client_ip = request.client.host if request.client else None

    # Create audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="transfer_team_ownership",
        target_type="team",
        target_id=team_id,
        details={
            "old_owner_id": str(old_owner_id),
            "new_owner_id": new_owner_id,
        },
        ip_address=client_ip,
    )

    logger.info(f"Team {team_id} ownership transferred from {old_owner_id} to {new_owner_id} by admin {auth.user_id}")

    # Return updated team
    return await get_team(team_id, auth)


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: str,
    auth: AdminAuthDep,
    request: Request,
):
    """
    Delete a team and unlink associated resources.

    This will:
    - Delete all team members
    - Unlink collections (set team_id to null)
    - Delete the team
    """
    supabase = await get_async_supabase_admin()

    # Check if team exists
    team_result = await supabase.table("teams").select("*").eq("id", team_id).single().execute()
    if not team_result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found",
        )

    team = team_result.data

    # Unlink collections (set team_id to null)
    try:
        await supabase.table("collections").update({
            "team_id": None,
        }).eq("team_id", team_id).execute()
    except Exception as e:
        logger.warning(f"Failed to unlink collections from team {team_id}: {e}")

    # Delete team (this will cascade delete team_members and notifications)
    await supabase.table("teams").delete().eq("id", team_id).execute()

    # Get client IP for audit log
    client_ip = request.client.host if request.client else None

    # Create audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="delete_team",
        target_type="team",
        target_id=team_id,
        details={
            "team_name": team["name"],
            "owner_id": str(team["owner_id"]),
        },
        ip_address=client_ip,
    )

    logger.info(f"Team {team_id} ({team['name']}) deleted by admin {auth.user_id}")
