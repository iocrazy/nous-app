"""Invite schemas for API requests and responses."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel

ExpiryOption = Literal["30m", "1h", "6h", "12h", "1d", "7d", "never"]


class InviteCreate(BaseModel):
    """Request to create a team invite."""

    team_id: str
    expires_in: Optional[ExpiryOption] = None
    max_uses: Optional[int] = None


class InviteResponse(BaseModel):
    """Team invite response."""

    id: str
    team_id: str
    code: str
    created_by: str
    expires_at: Optional[datetime] = None
    max_uses: Optional[int] = None
    use_count: int
    created_at: datetime


class InviteListResponse(BaseModel):
    """List of invites response."""

    invites: List[InviteResponse]
    total: int


class AcceptInviteRequest(BaseModel):
    """Request to accept an invite."""

    code: str


class AcceptInviteResponse(BaseModel):
    """Response after accepting an invite."""

    team_id: str
    team_name: str
