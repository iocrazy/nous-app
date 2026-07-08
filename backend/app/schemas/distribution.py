"""Distribution — schemas for platform account connect/list (PR-D1)."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ConnectAccountRequest(BaseModel):
    platform: Literal["douyin"]
    scope_type: Literal["user", "team"]
    scope_id: str = Field(min_length=1, max_length=64)


class ConnectAccountResponse(BaseModel):
    auth_url: str


class SocialAccountOut(BaseModel):
    id: str  # Snowflake BIGINT → str（同 team.py 约定；repo 的 _public_row 已转）
    scope_type: str
    scope_id: str
    platform: str
    platform_user_id: str
    username: str
    avatar_url: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    status: str
    created_at: datetime


class AccountListResponse(BaseModel):
    accounts: list[SocialAccountOut]


class ModuleStatusResponse(BaseModel):
    """Distribution module switches (admin-controlled via
    ``system_settings['distribution.module']``). ``visible`` controls whether
    the frontend shows the nav entry + routes; ``enabled`` gates the backend
    account/OAuth API. Both default OFF (opt-in)."""

    enabled: bool = False
    visible: bool = False
