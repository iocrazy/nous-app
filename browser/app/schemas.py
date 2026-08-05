"""Wire contract for nous-browser.

`SessionStatus` is the typed-failure enum from the design doc (7.8). It is
deliberately platform-neutral: no member may name a platform, because callers
branch on it and the UI renders per-status guidance. Adding a platform must not
add a status.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    """The full enum from spec 7.8, in the order the table lists it.

    Every member is defined on both sides of the wire even when the local
    service cannot yet produce it. `PUBLISHED` is emitted by nothing here - S3
    adds the publish path - but it exists because the S1 mismatch (the two
    sides disagreeing about which members are legal) is precisely what makes a
    peer's answer get downgraded to `failed`, throwing away the actionable part.
    """

    SESSION_VALID = "session_valid"
    SESSION_INVALID = "session_invalid"
    PROXY_FAILED = "proxy_failed"
    TIMEOUT = "timeout"
    FAILED = "failed"
    WAITING_SCAN = "waiting_scan"
    SCANNED = "scanned"
    QRCODE_EXPIRED = "qrcode_expired"
    SMS_REQUIRED = "sms_required"
    SUCCESS = "success"
    PUBLISHED = "published"


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


# --- QR login (S2) ----------------------------------------------------------
#
# The login endpoints are the one place where a browser context outlives the
# request that created it: a QR code belongs to a live page, and closing the
# context invalidates the code. So `login_session_id` is a handle onto a
# resource held inside this process, and every response carries enough for the
# caller to decide whether to poll again or stop.


class LoginStartRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    environment: EnvironmentConfig | None = None


class LoginStartResponse(BaseModel):
    login_session_id: str
    status: SessionStatus
    qrcode_data_url: str
    # Hard deadline for the whole login attempt. The context is destroyed at
    # this point whether or not anybody scanned, so the caller must not keep
    # polling past it.
    expires_at: datetime


class LoginStatusResponse(BaseModel):
    status: SessionStatus
    # Non-null on `waiting_scan` and on `qrcode_expired` (where it is the
    # already-refreshed code, per the endpoint contract). Null once a code is
    # no longer meaningful, or when a refresh failed - null is how the caller
    # tells "here is a new code" from "there is no usable code".
    qrcode_data_url: str | None = None
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class SmsCodeRequest(BaseModel):
    # Digits only, bounded: this is typed straight into a page, and a wildly
    # out-of-range value is a caller bug worth rejecting before it costs a
    # browser round trip.
    code: str = Field(min_length=4, max_length=8, pattern=r"^\d+$")


class SmsCodeResponse(BaseModel):
    status: SessionStatus
    message: str


class LoginStateResponse(BaseModel):
    """The sensitive one: plaintext `storage_state`.

    It is returned exactly once per login, to `nous-backend`, which encrypts it
    before it touches a disk. Nothing here may be logged (spec 7.6).
    """

    storage_state: dict[str, Any]
    platform_user_id: str
    username: str
    avatar_url: str | None = None


class LoginCloseResponse(BaseModel):
    closed: bool
