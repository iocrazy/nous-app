"""Admin API routes for Team management."""

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.core.modules import ALL_MODULE_KEYS, MODULE_DEFINITIONS, validate_module_keys
from app.core.team_permissions import ASSIGNABLE_ROLES, get_role_info
from app.repositories.admin.teams_repository import AdminTeamsRepository
from app.schemas.admin import (
    AdminModuleDefinition,
    AdminTeamListResponse,
    AdminTeamMemberResponse,
    AdminTeamModulesResponse,
    AdminTeamResponse,
    AdminUpdateMemberRoleRequest,
    AdminUpdateModulesRequest,
    TeamRoleResponse,
)
from app.utils.admin_helpers import (
    batch_get_team_member_counts,
    batch_get_user_info,
    create_audit_log,
    get_team_member_count,
    get_user_info,
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
    repo = AdminTeamsRepository()
    offset = (page - 1) * page_size
    rows, total = await repo.list_teams(search=search, offset=offset, limit=page_size)

    if not rows:
        return AdminTeamListResponse(items=[], total=0, page=page, page_size=page_size)

    team_ids = [t["id"] for t in rows]
    owner_ids = list(set(t["owner_id"] for t in rows))

    member_counts, owner_info, points_balances = await asyncio.gather(
        batch_get_team_member_counts(team_ids),
        batch_get_user_info(owner_ids),
        repo.batch_points_balances(team_ids),
    )

    items = []
    for team in rows:
        tid = team["id"]
        oid = team["owner_id"]
        owner_email, owner_username = owner_info.get(oid, (None, None))
        items.append(
            AdminTeamResponse(
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
            )
        )

    return AdminTeamListResponse(
        items=items,
        total=total or len(items),
        page=page,
        page_size=page_size,
    )


@router.get("/{team_id}", response_model=AdminTeamResponse)
async def get_team(
    team_id: str,
    auth: AdminAuthDep,
):
    """Get detailed information about a specific team."""
    repo = AdminTeamsRepository()
    team = await repo.get(team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found",
        )

    member_count, (owner_email, owner_username), points_balance = await asyncio.gather(
        get_team_member_count(team_id),
        get_user_info(team["owner_id"]),
        repo.get_points_balance(team_id),
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
    repo = AdminTeamsRepository()

    if not await repo.get(team_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found",
        )

    members_rows = await repo.list_members(team_id)
    if not members_rows:
        return []

    user_ids = [m["user_id"] for m in members_rows]
    user_info = await batch_get_user_info(user_ids)

    members = []
    for member in members_rows:
        uid = member["user_id"]
        email, username = user_info.get(uid, (None, None))
        members.append(
            AdminTeamMemberResponse(
                user_id=str(uid),
                email=email,
                username=username,
                role=member["role"],
                joined_at=member["joined_at"],
            )
        )

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
    repo = AdminTeamsRepository()

    team = await repo.get(team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found",
        )

    old_owner_id = team["owner_id"]

    if new_owner_id == old_owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New owner is the same as current owner",
        )

    if not await repo.get_member(team_id, new_owner_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New owner must be an existing team member",
        )

    await repo.update(team_id, {"owner_id": new_owner_id})
    await repo.update_member_role(team_id, new_owner_id, "owner")
    await repo.update_member_role(team_id, old_owner_id, "admin")

    client_ip = request.client.host if request.client else None
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

    logger.info(
        f"Team {team_id} ownership transferred from {old_owner_id} to {new_owner_id} by admin {auth.user_id}"
    )

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
    repo = AdminTeamsRepository()

    team = await repo.get(team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found",
        )

    try:
        await repo.unlink_collections(team_id)
    except Exception as e:
        logger.warning(f"Failed to unlink collections from team {team_id}: {e}")

    await repo.delete(team_id)

    client_ip = request.client.host if request.client else None
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


@router.patch(
    "/{team_id}/members/{user_id}/role", response_model=AdminTeamMemberResponse
)
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

    repo = AdminTeamsRepository()

    team = await repo.get(team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

    if user_id == team["owner_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change the team owner's role. Use transfer-ownership instead.",
        )

    member = await repo.get_member(team_id, user_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this team",
        )

    old_role = member["role"]
    await repo.update_member_role(team_id, user_id, body.role)

    client_ip = request.client.host if request.client else None
    await create_audit_log(
        admin_id=auth.user_id,
        action="update_member_role",
        target_type="team_member",
        target_id=f"{team_id}/{user_id}",
        details={"old_role": old_role, "new_role": body.role},
        ip_address=client_ip,
    )

    logger.info(
        f"Team {team_id} member {user_id} role changed from {old_role} to {body.role} by admin {auth.user_id}"
    )

    email, username = await get_user_info(user_id)
    return AdminTeamMemberResponse(
        user_id=str(user_id),
        email=email,
        username=username,
        role=body.role,
        joined_at=member["joined_at"],
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
    repo = AdminTeamsRepository()

    team = await repo.get(team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

    if user_id == team["owner_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove the team owner. Transfer ownership first.",
        )

    member = await repo.get_member(team_id, user_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this team",
        )

    old_role = member["role"]
    await repo.delete_member(team_id, user_id)

    client_ip = request.client.host if request.client else None
    await create_audit_log(
        admin_id=auth.user_id,
        action="remove_team_member",
        target_type="team_member",
        target_id=f"{team_id}/{user_id}",
        details={"role": old_role},
        ip_address=client_ip,
    )

    logger.info(
        f"Member {user_id} (role={old_role}) removed from team {team_id} by admin {auth.user_id}"
    )


# ============================================
# Module Permission Endpoints
# ============================================


@router.get("/{team_id}/modules", response_model=AdminTeamModulesResponse)
async def get_team_modules(
    team_id: str,
    auth: AdminAuthDep,
):
    """Get current module settings for a team."""
    repo = AdminTeamsRepository()
    team = await repo.get(team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

    enabled = team.get("enabled_modules") or ALL_MODULE_KEYS

    modules = [
        AdminModuleDefinition(
            key=m["key"],
            name=m["name"],
            description=m["description"],
            enabled=m["key"] in enabled,
        )
        for m in MODULE_DEFINITIONS
    ]

    return AdminTeamModulesResponse(team_id=str(team["id"]), modules=modules)


@router.patch("/{team_id}/modules", response_model=AdminTeamModulesResponse)
async def update_team_modules(
    team_id: str,
    body: AdminUpdateModulesRequest,
    auth: AdminAuthDep,
    request: Request,
):
    """Update module permissions for a team."""
    invalid_keys = validate_module_keys(body.enabled_modules)
    if invalid_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid module keys: {', '.join(invalid_keys)}. Valid keys: {', '.join(ALL_MODULE_KEYS)}",
        )

    repo = AdminTeamsRepository()

    team = await repo.get(team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

    old_modules = team.get("enabled_modules") or ALL_MODULE_KEYS

    await repo.update(team_id, {"enabled_modules": body.enabled_modules})

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

    logger.info(
        f"Team {team_id} modules updated by admin {auth.user_id}: {body.enabled_modules}"
    )

    return await get_team_modules(team_id, auth)
