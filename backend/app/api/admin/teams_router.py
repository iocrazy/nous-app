"""Admin API routes for Team management."""

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.db import get_async_supabase_admin
from app.core.team_permissions import ASSIGNABLE_ROLES, get_role_info
from app.schemas.admin import (
    AdminTeamResponse,
    AdminTeamListResponse,
    AdminTeamMemberResponse,
    AdminUpdateMemberRoleRequest,
    AdminTeamModulesResponse,
    AdminModuleDefinition,
    AdminUpdateModulesRequest,
    TeamRoleResponse,
)
from app.core.modules import MODULE_DEFINITIONS, ALL_MODULE_KEYS, validate_module_keys
from app.utils.admin_helpers import (
    create_audit_log,
    get_user_info,
    get_team_member_count,
    batch_get_user_info,
    batch_get_team_member_counts,
)


router = APIRouter()


# ============================================
# Team Endpoints
# ============================================


@router.get("/roles", response_model=list[TeamRoleResponse])
async def list_team_roles(auth: AdminAuthDep):
    """Return all team role definitions with their permissions."""
    return [TeamRoleResponse(**r) for r in get_role_info()]


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

    # Batch fetch: member counts, owner info, and points balances in parallel
    team_ids = [t["id"] for t in result.data]
    owner_ids = list(set(t["owner_id"] for t in result.data))

    async def batch_get_points_balances(tids: list) -> dict:
        """Get points_balance for multiple teams from team_quotas."""
        if not tids:
            return {}
        resp = await supabase.table("team_quotas").select("team_id, points_balance").in_("team_id", tids).execute()
        return {str(r["team_id"]): r.get("points_balance", 0) for r in (resp.data or [])}

    member_counts, owner_info, points_balances = await asyncio.gather(
        batch_get_team_member_counts(team_ids),
        batch_get_user_info(owner_ids),
        batch_get_points_balances(team_ids),
    )

    # Build response
    items = []
    for team in result.data:
        tid = team["id"]
        oid = team["owner_id"]
        owner_email, owner_username = owner_info.get(oid, (None, None))
        items.append(AdminTeamResponse(
            id=str(tid),
            name=team["name"],
            owner_id=str(oid),
            owner_email=owner_email,
            owner_username=owner_username,
            invite_code=team["invite_code"],
            description=team.get("description"),
            is_personal=team.get("is_personal", False),
            member_count=member_counts.get(tid, 0),
            points_balance=points_balances.get(str(tid), 0),
            enabled_modules=team.get("enabled_modules", ALL_MODULE_KEYS),
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

    # Get member count, owner info, and points balance concurrently
    async def get_points_balance(tid: str) -> int:
        resp = await supabase.table("team_quotas").select("points_balance").eq("team_id", tid).maybe_single().execute()
        return resp.data.get("points_balance", 0) if resp.data else 0

    member_count, (owner_email, owner_username), points_balance = await asyncio.gather(
        get_team_member_count(team_id),
        get_user_info(team["owner_id"]),
        get_points_balance(team_id),
    )

    return AdminTeamResponse(
        id=str(team["id"]),
        name=team["name"],
        owner_id=str(team["owner_id"]),
        owner_email=owner_email,
        owner_username=owner_username,
        invite_code=team["invite_code"],
        description=team.get("description"),
        is_personal=team.get("is_personal", False),
        member_count=member_count,
        points_balance=points_balance,
        enabled_modules=team.get("enabled_modules", ALL_MODULE_KEYS),
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

    # Batch fetch user info for all members (avoiding N+1 queries)
    user_ids = [m["user_id"] for m in result.data]
    user_info = await batch_get_user_info(user_ids)

    # Build response
    members = []
    for member in result.data:
        uid = member["user_id"]
        email, username = user_info.get(uid, (None, None))
        members.append(AdminTeamMemberResponse(
            user_id=str(uid),
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
        "role": "admin",
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


@router.patch("/{team_id}/members/{user_id}/role", response_model=AdminTeamMemberResponse)
async def update_member_role(
    team_id: str,
    user_id: str,
    body: AdminUpdateMemberRoleRequest,
    auth: AdminAuthDep,
    request: Request,
):
    """
    Change a team member's role.

    - Cannot set role to 'owner' (use transfer-ownership instead)
    - Cannot change the team owner's role
    """
    if body.role not in ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Role must be one of: {', '.join(ASSIGNABLE_ROLES)}",
        )

    supabase = await get_async_supabase_admin()

    # Check team exists and get owner
    team_result = await supabase.table("teams").select("owner_id").eq("id", team_id).single().execute()
    if not team_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    if user_id == team_result.data["owner_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change the team owner's role. Use transfer-ownership instead.",
        )

    # Check member exists
    member_result = await supabase.table("team_members").select("*").eq("team_id", team_id).eq("user_id", user_id).single().execute()
    if not member_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found in this team")

    old_role = member_result.data["role"]

    # Update role
    await supabase.table("team_members").update({"role": body.role}).eq("team_id", team_id).eq("user_id", user_id).execute()

    # Audit log
    client_ip = request.client.host if request.client else None
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_member_role",
        target_type="team_member",
        target_id=f"{team_id}/{user_id}",
        details={"old_role": old_role, "new_role": body.role},
        ip_address=client_ip,
    )

    logger.info(f"Team {team_id} member {user_id} role changed from {old_role} to {body.role} by admin {auth.user_id}")

    # Return updated member
    email, username = await get_user_info(user_id)
    return AdminTeamMemberResponse(
        user_id=str(user_id),
        email=email,
        username=username,
        role=body.role,
        joined_at=member_result.data["joined_at"],
    )


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    team_id: str,
    user_id: str,
    auth: AdminAuthDep,
    request: Request,
):
    """
    Remove a member from a team.

    - Cannot remove the team owner
    """
    supabase = await get_async_supabase_admin()

    # Check team exists and get owner
    team_result = await supabase.table("teams").select("owner_id").eq("id", team_id).single().execute()
    if not team_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    if user_id == team_result.data["owner_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove the team owner. Transfer ownership first.",
        )

    # Check member exists
    member_result = await supabase.table("team_members").select("role").eq("team_id", team_id).eq("user_id", user_id).single().execute()
    if not member_result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found in this team")

    old_role = member_result.data["role"]

    # Delete member
    await supabase.table("team_members").delete().eq("team_id", team_id).eq("user_id", user_id).execute()

    # Audit log
    client_ip = request.client.host if request.client else None
    await create_audit_log(
        admin_id=auth.user_id,
        action="remove_team_member",
        target_type="team_member",
        target_id=f"{team_id}/{user_id}",
        details={"role": old_role},
        ip_address=client_ip,
    )

    logger.info(f"Member {user_id} (role={old_role}) removed from team {team_id} by admin {auth.user_id}")


# ============================================
# Module Permission Endpoints
# ============================================


@router.get("/{team_id}/modules", response_model=AdminTeamModulesResponse)
async def get_team_modules(
    team_id: str,
    auth: AdminAuthDep,
):
    """Get current module settings for a team."""
    supabase = await get_async_supabase_admin()

    result = await supabase.table("teams").select("id, enabled_modules").eq("id", team_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    enabled = result.data.get("enabled_modules") or ALL_MODULE_KEYS

    modules = [
        AdminModuleDefinition(
            key=m["key"],
            name=m["name"],
            description=m["description"],
            enabled=m["key"] in enabled,
        )
        for m in MODULE_DEFINITIONS
    ]

    return AdminTeamModulesResponse(team_id=str(result.data["id"]), modules=modules)


@router.patch("/{team_id}/modules", response_model=AdminTeamModulesResponse)
async def update_team_modules(
    team_id: str,
    body: AdminUpdateModulesRequest,
    auth: AdminAuthDep,
    request: Request,
):
    """Update module permissions for a team."""
    # Validate module keys
    invalid_keys = validate_module_keys(body.enabled_modules)
    if invalid_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid module keys: {', '.join(invalid_keys)}. Valid keys: {', '.join(ALL_MODULE_KEYS)}",
        )

    supabase = await get_async_supabase_admin()

    # Check team exists and get current state
    result = await supabase.table("teams").select("id, enabled_modules").eq("id", team_id).single().execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    old_modules = result.data.get("enabled_modules") or ALL_MODULE_KEYS

    # Update
    await supabase.table("teams").update({
        "enabled_modules": body.enabled_modules,
    }).eq("id", team_id).execute()

    # Audit log
    client_ip = request.client.host if request.client else None
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_team_modules",
        target_type="team",
        target_id=team_id,
        details={
            "old_modules": old_modules,
            "new_modules": body.enabled_modules,
        },
        ip_address=client_ip,
    )

    logger.info(f"Team {team_id} modules updated by admin {auth.user_id}: {body.enabled_modules}")

    # Return updated state
    return await get_team_modules(team_id, auth)
