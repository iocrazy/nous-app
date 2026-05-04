"""API routes for Team Invites management."""

from fastapi import APIRouter, HTTPException, status
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.invite_repository import InviteRepository
from app.schemas.invite import (
    AcceptInviteRequest,
    AcceptInviteResponse,
    InviteCreate,
    InviteListResponse,
    InviteResponse,
)

router = APIRouter(prefix="/invites", tags=["Invites"])


@router.get("", response_model=InviteListResponse)
async def list_invites(team_id: str, auth: AuthDep):
    """List all invites for a team."""
    repo = InviteRepository()

    # Check permission
    can_manage = await repo.check_user_can_manage_invites(team_id, auth.user_id)
    if not can_manage:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )

    invites = await repo.get_invites_by_team(team_id)

    return InviteListResponse(
        invites=[
            InviteResponse(
                id=str(i["id"]),
                team_id=str(i["team_id"]),
                code=i["code"],
                created_by=i["created_by"],
                expires_at=i.get("expires_at"),
                max_uses=i.get("max_uses"),
                use_count=i.get("use_count", 0),
                created_at=i["created_at"],
            )
            for i in invites
        ],
        total=len(invites),
    )


@router.post("", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def create_invite(invite: InviteCreate, auth: AuthDep):
    """Create a new team invite."""
    repo = InviteRepository()

    # Check permission
    can_manage = await repo.check_user_can_manage_invites(invite.team_id, auth.user_id)
    if not can_manage:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )

    try:
        created = await repo.create_invite(
            team_id=invite.team_id,
            created_by=auth.user_id,
            expires_in=invite.expires_in,
            max_uses=invite.max_uses,
        )
    except Exception as e:
        logger.exception(f"Failed to create invite: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create invite",
        )

    return InviteResponse(
        id=str(created["id"]),
        team_id=str(created["team_id"]),
        code=created["code"],
        created_by=created["created_by"],
        expires_at=created.get("expires_at"),
        max_uses=created.get("max_uses"),
        use_count=created.get("use_count", 0),
        created_at=created["created_at"],
    )


@router.delete("/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invite(invite_id: str, auth: AuthDep):
    """Delete an invite."""
    repo = InviteRepository()
    deleted = await repo.delete_invite(invite_id, auth.user_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invite not found or permission denied",
        )


@router.post("/accept", response_model=AcceptInviteResponse)
async def accept_invite(request: AcceptInviteRequest, auth: AuthDep):
    """Accept an invite and join the team."""
    repo = InviteRepository()

    try:
        result = await repo.accept_invite(request.code, auth.user_id)
    except Exception as e:
        error_msg = str(e)
        if "Invalid invite code" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invalid invite code"
            )
        if "expired" in error_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_410_GONE, detail="Invite has expired"
            )
        if "max uses" in error_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_410_GONE, detail="Invite has reached max uses"
            )
        if "Already a member" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Already a member of this team",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to accept invite",
        )

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invalid invite code"
        )

    return AcceptInviteResponse(
        team_id=result["team_id"], team_name=result["team_name"]
    )
