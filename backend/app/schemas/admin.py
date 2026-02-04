"""Admin API schemas for requests and responses."""

from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field


# ============================================
# User Management Schemas
# ============================================


class AdminUserResponse(BaseModel):
    """Admin user response with full details."""
    id: str
    email: Optional[str] = None
    username: Optional[str] = None
    avatar_url: Optional[str] = None
    role: str = "user"
    is_banned: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None
    last_sign_in_at: Optional[datetime] = None
    video_count: int = 0
    team_count: int = 0


class AdminUserListResponse(BaseModel):
    """Paginated list of admin users."""
    items: List[AdminUserResponse]
    total: int
    page: int
    page_size: int


class AdminUserUpdate(BaseModel):
    """Request to update user by admin."""
    role: Optional[str] = None
    is_banned: Optional[bool] = None


class AdminUserBanRequest(BaseModel):
    """Request to ban/unban a user."""
    is_banned: bool
    reason: Optional[str] = None


# ============================================
# Team Management Schemas
# ============================================


class AdminTeamMemberResponse(BaseModel):
    """Team member details for admin view."""
    user_id: str
    email: Optional[str] = None
    username: Optional[str] = None
    role: str
    joined_at: datetime


class AdminTeamResponse(BaseModel):
    """Admin team response with full details."""
    id: str
    name: str
    owner_id: str
    owner_email: Optional[str] = None
    owner_username: Optional[str] = None
    invite_code: str
    description: Optional[str] = None
    member_count: int = 0
    created_at: datetime


class AdminTeamListResponse(BaseModel):
    """Paginated list of admin teams."""
    items: List[AdminTeamResponse]
    total: int
    page: int
    page_size: int


# ============================================
# Audit Log Schemas
# ============================================


class AuditLogCreate(BaseModel):
    """Request to create an audit log entry."""
    action: str
    target_type: str
    target_id: str
    details: Optional[dict] = None
    ip_address: Optional[str] = None


class AuditLogResponse(BaseModel):
    """Audit log entry response."""
    id: str
    admin_id: str
    admin_email: Optional[str] = None
    admin_username: Optional[str] = None
    action: str
    target_type: str
    target_id: str
    details: Optional[dict] = None
    ip_address: Optional[str] = None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    """Paginated list of audit logs."""
    items: List[AuditLogResponse]
    total: int
    page: int
    page_size: int


# ============================================
# Admin Stats Schemas
# ============================================


class AdminStatsResponse(BaseModel):
    """Admin dashboard statistics."""
    total_users: int = 0
    total_videos: int = 0
    total_teams: int = 0
    total_downloads: int = 0
    active_users_today: int = 0
    new_users_today: int = 0
    new_videos_today: int = 0


# ============================================
# Credits Management Schemas
# ============================================


class AdminUserCreditsResponse(BaseModel):
    """User credits information for admin."""
    user_id: str
    balance: int = 0
    total_earned: int = 0
    total_spent: int = 0
    created_at: datetime
    updated_at: datetime


class AdminCreditsAdjustRequest(BaseModel):
    """Request to adjust user credits."""
    amount: int = Field(..., description="Amount to adjust (positive or negative)")
    reason: Optional[str] = None


# ============================================
# System Settings Schemas
# ============================================


class SystemSettingResponse(BaseModel):
    """System setting response."""
    key: str
    value: Any
    description: Optional[str] = None
    updated_at: datetime
    updated_by: Optional[str] = None


class SystemSettingUpdate(BaseModel):
    """Request to update a system setting."""
    value: Any
