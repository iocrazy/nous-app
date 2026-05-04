"""API routes for Teams management."""

from fastapi import APIRouter, HTTPException, status
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.team_repository import TeamRepository
from app.schemas.team import (
    JoinTeamRequest,
    TeamCreate,
    TeamListResponse,
    TeamMemberListResponse,
    TeamMemberResponse,
    TeamMemberUpdate,
    TeamResponse,
    TeamUpdate,
)

router = APIRouter(prefix="/teams", tags=["Teams"])


@router.get("", response_model=TeamListResponse)
async def list_teams(auth: AuthDep):
    """List all teams the current user is a member of."""
    repo = TeamRepository()
    teams = await repo.get_user_teams(auth.user_id)

    return TeamListResponse(
        teams=[
            TeamResponse(
                id=str(t["id"]),
                name=t["name"],
                owner_id=t["owner_id"],
                invite_code=t.get("invite_code", ""),
                description=t.get("description"),
                created_at=t["created_at"],
            )
            for t in teams
        ],
        total=len(teams),
    )


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(team: TeamCreate, auth: AuthDep):
    """Create a new team."""
    repo = TeamRepository()

    try:
        created = await repo.create_team(team.name, auth.user_id)
    except Exception as e:
        logger.exception(f"Failed to create team: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create team",
        )

    return TeamResponse(
        id=str(created["id"]),
        name=created["name"],
        owner_id=created["owner_id"],
        invite_code=created.get("invite_code", ""),
        description=created.get("description"),
        created_at=created["created_at"],
    )


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(team_id: str, auth: AuthDep):
    """Get a specific team by ID."""
    repo = TeamRepository()
    team = await repo.get_team_by_id(team_id, auth.user_id)

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found or access denied",
        )

    return TeamResponse(
        id=str(team["id"]),
        name=team["name"],
        owner_id=team["owner_id"],
        invite_code=team.get("invite_code", ""),
        description=team.get("description"),
        created_at=team["created_at"],
    )


@router.put("/{team_id}", response_model=TeamResponse)
async def update_team(team_id: str, update: TeamUpdate, auth: AuthDep):
    """Update a team (owner only)."""
    repo = TeamRepository()

    update_data = {}
    if update.name is not None:
        update_data["name"] = update.name
    if update.description is not None:
        update_data["description"] = update.description

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No update data provided"
        )

    updated = await repo.update_team(team_id, auth.user_id, **update_data)

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found or not owner"
        )

    return TeamResponse(
        id=str(updated["id"]),
        name=updated["name"],
        owner_id=updated["owner_id"],
        invite_code=updated.get("invite_code", ""),
        description=updated.get("description"),
        created_at=updated["created_at"],
    )


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(team_id: str, auth: AuthDep):
    """Delete a team (owner only)."""
    repo = TeamRepository()
    deleted = await repo.delete_team(team_id, auth.user_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found or not owner"
        )


@router.get("/{team_id}/members", response_model=TeamMemberListResponse)
async def list_team_members(team_id: str, auth: AuthDep):
    """List all members of a team."""
    repo = TeamRepository()
    members = await repo.get_team_members(team_id, auth.user_id)

    if not members and not await repo.get_team_by_id(team_id, auth.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found or access denied",
        )

    return TeamMemberListResponse(
        members=[
            TeamMemberResponse(
                team_id=str(m["team_id"]),
                user_id=m["user_id"],
                role=m["role"],
                joined_at=m["joined_at"],
                email=m.get("email"),
                name=m.get("name"),
            )
            for m in members
        ],
        total=len(members),
    )


@router.put("/{team_id}/members/{user_id}", response_model=TeamMemberResponse)
async def update_member_role(
    team_id: str, user_id: str, update: TeamMemberUpdate, auth: AuthDep
):
    """Update a team member's role (owner/admin only)."""
    repo = TeamRepository()

    if update.role not in ["admin", "editor", "reviewer", "viewer"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be 'admin', 'editor', 'reviewer', or 'viewer'",
        )

    updated = await repo.update_member_role(team_id, user_id, update.role, auth.user_id)

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied or member not found",
        )

    # Get updated member info
    members = await repo.get_team_members(team_id, auth.user_id)
    member = next((m for m in members if m["user_id"] == user_id), None)

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    return TeamMemberResponse(
        team_id=str(member["team_id"]),
        user_id=member["user_id"],
        role=member["role"],
        joined_at=member["joined_at"],
        email=member.get("email"),
        name=member.get("name"),
    )


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(team_id: str, user_id: str, auth: AuthDep):
    """Remove a member from team (owner/admin only, or self)."""
    repo = TeamRepository()
    removed = await repo.remove_member(team_id, user_id, auth.user_id)

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied, member not found, or cannot remove owner",
        )


@router.post("/join", response_model=TeamResponse)
async def join_team(request: JoinTeamRequest, auth: AuthDep):
    """Join a team using invite code."""
    repo = TeamRepository()

    try:
        team = await repo.join_team_by_code(request.invite_code, auth.user_id)
    except Exception as e:
        if "Already a member" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Already a member of this team",
            )
        raise

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invalid invite code"
        )

    return TeamResponse(
        id=str(team["id"]),
        name=team["name"],
        owner_id=team["owner_id"],
        invite_code=team.get("invite_code", ""),
        description=team.get("description"),
        created_at=team["created_at"],
    )
