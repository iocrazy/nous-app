"""Team schemas for API requests and responses."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class TeamCreate(BaseModel):
    """Request to create a team."""
    name: str


class TeamUpdate(BaseModel):
    """Request to update a team."""
    name: Optional[str] = None
    description: Optional[str] = None


class TeamMemberAdd(BaseModel):
    """Request to add a member to a team."""
    user_id: str
    role: str = "member"


class TeamMemberUpdate(BaseModel):
    """Request to update a team member's role."""
    role: str  # "admin" or "member"


class TeamMemberResponse(BaseModel):
    """Team member response."""
    team_id: str
    user_id: str
    role: str
    joined_at: datetime
    email: Optional[str] = None
    name: Optional[str] = None


class TeamResponse(BaseModel):
    """Team response."""
    id: str
    name: str
    owner_id: str
    invite_code: str
    description: Optional[str] = None
    created_at: datetime


class TeamListResponse(BaseModel):
    """List of teams response."""
    teams: List[TeamResponse]
    total: int


class TeamMemberListResponse(BaseModel):
    """List of team members response."""
    members: List[TeamMemberResponse]
    total: int


class JoinTeamRequest(BaseModel):
    """Request to join a team by invite code."""
    invite_code: str
