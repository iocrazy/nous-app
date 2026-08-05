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


# --- publish (S3) -----------------------------------------------------------
#
# The intent is described in *channel* terms, never in a platform's own
# vocabulary: "visibility: friends", not Douyin's `private_status` integer.
# Translating that into whatever the platform's editor calls it is the job of
# each publisher (design doc 6.1a). A field that only one platform understands
# belongs in `platform_options`, which is passed through untouched.


class MediaItem(BaseModel):
    """One downloadable asset.

    `url` is a short-lived signed URL issued by nous-backend and resolvable on
    the docker network. This service never sees a filesystem path or a bucket
    key: it has no storage volume, by design (design doc 4.2 step 4).
    """

    kind: str = Field(min_length=1, max_length=16)
    url: str = Field(min_length=1, max_length=4096)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str | None = None
    size_bytes: int | None = None


class PublishIntent(BaseModel):
    content_type: str = Field(min_length=1, max_length=32)
    media: list[MediaItem] = Field(default_factory=list)
    title: str = ""
    description: str = ""
    topics: list[str] = Field(default_factory=list)
    visibility: str = "public"
    allow_download: bool = True
    cover: MediaItem | None = None
    # S3 publishes immediately. A non-null value is answered with `failed`
    # rather than silently published now - a post that goes out twelve hours
    # early is not a smaller failure than one that does not go out at all.
    scheduled_at: datetime | None = None
    platform_options: dict[str, Any] = Field(default_factory=dict)


class PublishRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    # Plaintext, decrypted by the backend. Memory only (spec 7.6).
    storage_state: dict[str, Any]
    environment: EnvironmentConfig | None = None
    intent: PublishIntent


class PublishResponse(BaseModel):
    """A superset of `SessionResult`, so a caller that only knows the smaller
    shape still parses the four fields it cares about."""

    success: bool
    status: SessionStatus
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    platform_item_id: str | None = None
    published_url: str | None = None
    # **The field that decides how often a user has to re-scan a QR code.**
    # Platform sessions slide forward on use: the server hands back refreshed
    # cookies every time. Dropping them means every publish spends down the
    # original grant instead of renewing it, turning a three-month session into
    # a two-week one (design doc 4.2 step 6). Returned on failure too, whenever
    # a context got far enough to have one - the renewal may already have
    # happened before whatever went wrong.
    updated_storage_state: dict[str, Any] | None = None
