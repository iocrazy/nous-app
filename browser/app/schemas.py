"""Wire contract for nous-browser.

`SessionStatus` is the typed-failure enum from the design doc (7.8). It is
deliberately platform-neutral: no member may name a platform, because callers
branch on it and the UI renders per-status guidance. Adding a platform must not
add a status.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    SESSION_VALID = "session_valid"
    SESSION_INVALID = "session_invalid"
    QRCODE_EXPIRED = "qrcode_expired"
    SMS_REQUIRED = "sms_required"
    TIMEOUT = "timeout"
    PROXY_FAILED = "proxy_failed"
    FAILED = "failed"


class EnvironmentConfig(BaseModel):
    """Per-account browser environment (mirrors `account_environments`).

    Every field is optional in S1, but the plumbing down to
    `browser.new_context()` is wired now so S4 only has to start sending values.
    """

    proxy_url: str | None = None
    user_agent: str | None = None
    locale: str | None = "zh-CN"
    timezone_id: str | None = "Asia/Shanghai"
    geo_lat: float | None = None
    geo_lng: float | None = None


class SessionValidateRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    # Playwright storage_state as a plain object. Kept as a dict and handed
    # straight to new_context(storage_state=...) - never written to a file.
    storage_state: dict[str, Any]
    environment: EnvironmentConfig | None = None


class SessionResult(BaseModel):
    success: bool
    status: SessionStatus
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    browser_ready: bool
    xvfb: bool
    version: str
