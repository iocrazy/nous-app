"""Team schemas for API requests and responses."""

from datetime import datetime
from typing import List, Optional

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


class TeamAiBudgetUpdate(BaseModel):
    """Request to set a team's monthly AI budget (W3c).

    ``monthly_budget_cents`` None = unlimited (removes the ceiling)."""

    monthly_budget_cents: Optional[float] = None


class TeamAiBudgetResponse(BaseModel):
    """A team's monthly AI budget + current calendar-month spend (W3c)."""

    team_id: str
    monthly_budget_cents: Optional[float] = None
    month_spend_cents: float = 0.0
    over_budget: bool = False
    updated_by_user_id: Optional[str] = None
    updated_at: Optional[datetime] = None


class TeamMemberUpdate(BaseModel):
    """Request to update a team member's role."""

    role: str  # "admin", "editor", "reviewer", or "viewer"


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
    # personal = auto-created single-member team; collaborative = user-created.
    # Lets clients (e.g. the browser extension scope picker) label scopes.
    kind: str = "collaborative"


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
