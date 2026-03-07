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


class AdminUpdateMemberRoleRequest(BaseModel):
    """Request to update a team member's role."""
    role: str


class TeamRoleResponse(BaseModel):
    """Team role definition for API response."""
    role: str
    permissions: List[str]
    is_assignable: bool


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


# ============================================
# Video Management Schemas
# ============================================


class AdminVideoResponse(BaseModel):
    """Admin video response with core details."""
    id: int
    aweme_id: str
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    video_title: Optional[str] = None
    video_desc: Optional[str] = None
    author: Optional[str] = None
    aweme_type: Optional[str] = None
    video_download_status: str = "pending"
    cover_url: Optional[str] = None
    cover_download_path: Optional[str] = None
    source_platform: Optional[str] = None
    video_duration: Optional[str] = None
    video_datasize: Optional[str] = None
    video_datasize_bytes: int = 0
    video_digg_count: int = 0
    video_comment_count: int = 0
    video_share_count: int = 0
    error_message: Optional[str] = None
    download_time: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class AdminVideoListResponse(BaseModel):
    """Paginated list of admin videos."""
    items: List[AdminVideoResponse]
    total: int
    page: int
    page_size: int


class AdminVideoDetailResponse(AdminVideoResponse):
    """Extended video response with download paths and URLs."""
    video_original_url: Optional[str] = None
    video_download_path: Optional[str] = None
    cover_download_path: Optional[str] = None
    music_name: Optional[str] = None
    music_download_status: str = "pending"
    cover_download_status: str = "pending"
    video_download_urls: Optional[list] = None
    video_hashtag_name: Optional[str] = None
    video_collect_count: int = 0
    view_count: int = 0
    storage_size: Optional[int] = None
    keep_forever: bool = False


class AdminVideoStatsResponse(BaseModel):
    """Video status distribution stats."""
    total: int = 0
    completed: int = 0
    pending: int = 0
    failed: int = 0
    downloading: int = 0
    skipped: int = 0
    total_storage_bytes: int = 0


# ============================================
# Transcode Management Schemas
# ============================================


class AdminTranscodeVersionResponse(BaseModel):
    """A resource version with transcode status info."""
    id: str
    resource_id: str
    version_number: int
    filename: Optional[str] = None
    file_size_bytes: int = 0
    mime_type: Optional[str] = None
    transcode_status: Optional[str] = None
    hls_path: Optional[str] = None
    hls_tiers: Optional[dict[str, bool]] = None
    transcode_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    video_title: Optional[str] = None
    cover_url: Optional[str] = None
    cover_download_path: Optional[str] = None
    source_platform: Optional[str] = None
    author: Optional[str] = None


class AdminTranscodeListResponse(BaseModel):
    """Paginated list of transcode versions."""
    items: List[AdminTranscodeVersionResponse]
    total: int
    page: int
    page_size: int


class AdminTranscodeStatsResponse(BaseModel):
    """Transcode status distribution stats."""
    total_video_versions: int = 0
    completed: int = 0
    processing: int = 0
    failed: int = 0
    pending: int = 0
    not_transcoded: int = 0


class AdminTranscodeSettingsResponse(BaseModel):
    """Current HLS transcode settings."""
    transcode_enabled: bool = True
    transcode_tiers: str = "480p,720p,1080p"
    ffmpeg_encoder: str = "auto"
    ffmpeg_preset: str = "medium"
    transcode_parallel_tiers: bool = True
    transcode_min_size_mb: int = 100


class AdminTranscodeSettingsUpdate(BaseModel):
    """Request to update HLS transcode settings."""
    transcode_enabled: Optional[bool] = None
    transcode_tiers: Optional[str] = None
    ffmpeg_encoder: Optional[str] = None
    ffmpeg_preset: Optional[str] = None
    transcode_parallel_tiers: Optional[bool] = None
    transcode_min_size_mb: Optional[int] = None


# ============================================
# Task Management Schemas
# ============================================


class AdminTaskResponse(BaseModel):
    """Admin task response with user info."""
    id: str
    user_id: str
    user_email: Optional[str] = None
    task_type: str
    status: str
    phase: Optional[str] = None
    title: str
    subtitle: Optional[str] = None
    progress: int = 0
    speed: Optional[int] = None
    total_bytes: Optional[int] = None
    error_msg: Optional[str] = None
    error_code: Optional[str] = None
    resource_id: Optional[str] = None
    media_id: Optional[str] = None
    celery_task_id: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class AdminTaskListResponse(BaseModel):
    """Paginated list of admin tasks."""
    items: List[AdminTaskResponse]
    total: int
    page: int = 1
    page_size: int = 20


class AdminTaskStatsResponse(BaseModel):
    """Task status distribution stats."""
    total: int
    pending: int
    processing: int
    completed: int
    failed: int
    cancelled: int


# ============================================
# Table Preferences Schemas
# ============================================


class TableFilter(BaseModel):
    """Single filter condition."""
    field: str
    operator: str
    value: Any = None


class TableSort(BaseModel):
    """Single sort directive."""
    field: str
    direction: str = "asc"


class AdminTablePreferenceResponse(BaseModel):
    """Stored table preferences for a user + table_key."""
    table_key: str
    filters: List[TableFilter] = []
    sorts: List[TableSort] = []
    visible_columns: Optional[List[str]] = None
    column_order: Optional[List[str]] = None


class AdminTablePreferenceUpdate(BaseModel):
    """Upsert payload for table preferences."""
    filters: List[TableFilter] = []
    sorts: List[TableSort] = []
    visible_columns: Optional[List[str]] = None
    column_order: Optional[List[str]] = None
