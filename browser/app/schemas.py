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
    # The platform interrupted the scan to ask *how* it should verify the
    # person, and is waiting for that choice — nothing has been sent to anybody
    # yet. It is a separate member from `SMS_REQUIRED` precisely because the two
    # licence opposite user-facing sentences: `sms_required` invites "enter the
    # code you received", and saying that while the platform is still showing a
    # menu leaves the user waiting for a text message that was never requested.
    # That is not hypothetical — it is the bug this member was added for
    # (2026-08-11: a Douyin account whose login inserted 身份验证 read as
    # `sms_required`, and the user waited out the full TTL for a code nobody
    # had asked for).
    IDENTITY_CHALLENGE = "identity_challenge"
    SMS_REQUIRED = "sms_required"
    SUCCESS = "success"
    PUBLISHED = "published"
    # S3/P1-3 read-back. **A conclusion, not a failure**: we reached the
    # creator centre, read the works list, and the post is NOT live. That is
    # different in kind from `failed` ("we could not find out"), and the
    # difference is the whole point of the read-back — a caller that collapses
    # the two either blocks every batch during a container outage, or calls a
    # rejected post "done". Neutral wording on purpose: "not published" is true
    # whether the platform is reviewing it, refused it, or the user deleted it;
    # WHICH of those rides in `detail["reason"]`.
    NOT_PUBLISHED = "not_published"


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
    # mig 424 — per-account window size, the one fingerprint axis that can
    # differ between accounts without contradicting anything else (a UA
    # override does NOT move Client Hints; a timezone must match the exit IP).
    # Both None = Playwright's default 1280x720, i.e. every account bound
    # before mig 424. Bounds mirror the DB CHECK: the floor is the size the
    # Douyin DOM flow is known to work at, the ceiling is the Xvfb screen.
    viewport_width: int | None = Field(default=None, ge=1280, le=1920)
    viewport_height: int | None = Field(default=None, ge=720, le=1080)


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
    # Same envelope as `LoginStatusResponse.detail`, and for the same reason:
    # `sms_required` is the answer to both "we need a code" and "the code you
    # sent did not work", so the status alone cannot carry the verdict. The key
    # that separates them is `code_rejected` (see `LoginSession.submit_sms`);
    # without it the caller has no way to tell a rejection from a fresh prompt
    # and ends up calling a refused code a success.
    detail: dict[str, Any] = Field(default_factory=dict)


class LoginStateResponse(BaseModel):
    """The sensitive one: plaintext `storage_state`.

    It is returned exactly once per login, to `nous-backend`, which encrypts it
    before it touches a disk. Nothing here may be logged (spec 7.6).
    """

    storage_state: dict[str, Any]
    # The identity key the account is upserted on. Always non-empty here: the
    # login fails with `identity_unresolved` before it can get this far
    # (see `login.IdentityUnresolved`).
    platform_user_id: str
    username: str
    avatar_url: str | None = None
    # 抖音号 / 小红书号 — display only, and free to change without forking the
    # account. Split out of `platform_user_id` on 2026-08-09.
    platform_handle: str | None = None


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
    # Offset-aware ISO 8601, or null for "publish now". A naive value is
    # **refused**, not assumed to be UTC: reading a wall-clock time in the wrong
    # zone is an eight-hour error in a field nobody re-checks, and the post is
    # already out by the time anyone notices.
    #
    # Whether a value is legal at all is the platform's to say - Douyin accepts
    # 2 hours to 14 days out - so the window is enforced by that platform's
    # `PlatformIntentRules`, before a browser starts (spec 7.7). A platform that
    # registers no rules cannot schedule: absence means refusal, never "publish
    # it now", because a post that goes out twelve hours early is not a smaller
    # failure than one that does not go out at all.
    scheduled_at: datetime | None = None
    # Passed through untouched, and read only by the platform that understands
    # it. Douyin reads `self_declaration` (one of the platform's six declaration
    # strings verbatim), `collection` (a collection name) and `music` (a track
    # name, searched for and selected in the editor's 选择音乐 dialog). An
    # absent key means "leave that control alone" - which for `self_declaration`
    # is distinct from the user choosing 无需添加自主声明, a declaration the
    # platform actually records, and for `music` means the post keeps the
    # platform default (原声).
    #
    # ⚠️ `music` is here rather than as a field of its own **on purpose**. The
    # channel contract above is stated in channel terms; a top-level `music`
    # would assert that picking a track by name is something every platform
    # does, while only this one implements it - and it would route around the
    # `supports_music` gate the backend applies to `platform_options` keys it
    # recognises (`session_adapter._option_shape_problems`), which is what turns
    # "that platform has no music picker" into a refusal instead of a field
    # quietly dropped on the floor.
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


# --- publish read-back (P1-3) ----------------------------------------------
#
# `PublishResponse.published_url` has always been null on this channel, and the
# comment in `douyin_publish._drive` says why: the post-publish redirect carries
# no identifier, and picking the newest card off the manage page would attribute
# the wrong post to the batch on any account that has a scheduled or
# concurrently-published item. The read-back solves that from the other end -
# it goes looking for a SPECIFIC post, identified by what we typed into the
# editor, and reports what the platform now says about it.


class VerifyProbe(BaseModel):
    """What to look for. Deliberately not "the post id" - we never had one.

    `title` is the handle, because it is the one thing we know we put on the
    platform ourselves. It is matched normalised (see `douyin_verify`), never
    by equality on the raw string: the editor trims, collapses whitespace and
    drops characters it does not accept, so the card's caption is routinely a
    near-miss of what we sent.

    There is deliberately no `published_after` narrowing field. It would be the
    obvious second handle, but the works list renders dates in relative prose
    ("3天前") as often as absolutely, and a filter built on parsing that would
    silently exclude the very post it was asked to find. Declaring a knob we
    cannot honour is the mistake the `content_types = {"video","images"}` note
    in `session_adapter` was written about.
    """

    title: str = Field(min_length=1, max_length=500)


class VerifyPublishRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    storage_state: dict[str, Any]
    environment: EnvironmentConfig | None = None
    probe: VerifyProbe


class VerifyPublishResponse(BaseModel):
    """Same envelope as `PublishResponse`, same three extra fields.

    Reusing the shape is intentional: the caller's settle path for "a post is
    live, here is its URL and id" should not become a second implementation
    just because the news arrived from a read-back instead of from the publish
    that created it.
    """

    success: bool
    status: SessionStatus
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    platform_item_id: str | None = None
    published_url: str | None = None
    # Same reason as on `PublishResponse`: a read-back is a real page visit and
    # the platform rotates cookies on it, so throwing the refreshed state away
    # would make the extra job a net cost to session lifetime rather than free.
    updated_storage_state: dict[str, Any] | None = None


# --- read-only page reconnaissance (T0) -------------------------------------
#
# The one endpoint that exists to ANSWER questions about a platform's DOM
# rather than to act on it. Its contract is shaped by that: the request says
# where to look and what to count, the response carries numbers and a bounded
# text excerpt — never the document. See `app/inspect.py` for the constraints
# (allow-listed host, no interaction vocabulary anywhere in the module).

# Request-side ceilings live here, on the wire contract, so a caller's limits
# are declared exactly once and enforced by pydantic rather than by remembering.
MAX_TEXT_PROBES = 40
MAX_SELECTOR_PROBES = 40
MAX_SEED_FILES = 12
MAX_EXCERPT_CHARS = 8_000
DEFAULT_EXCERPT_CHARS = 2_000
DEFAULT_SEED_SELECTOR = 'input[type="file"]'


class InspectRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    # Plaintext, decrypted by the backend. Memory only (spec 7.6).
    storage_state: dict[str, Any]
    environment: EnvironmentConfig | None = None
    # Refused unless it lands inside the platform's own creator hosts. The
    # check is `inspect.url_refusal`, and it runs before a browser exists.
    url: str = Field(min_length=1, max_length=2048)
    # Files to hand to a file input so a form that only renders after a
    # transfer will render. Same `MediaItem` shape (and the same signed-URL
    # staging path) the posting flow uses — there is no second downloader.
    seed_files: list[MediaItem] = Field(default_factory=list, max_length=MAX_SEED_FILES)
    seed_selector: str = Field(
        default=DEFAULT_SEED_SELECTOR, min_length=1, max_length=200
    )
    # Which matching input, when a page has several. Explicit rather than
    # "the first": on this platform the first has already been the wrong one.
    seed_input_index: int = Field(default=0, ge=0, le=20)
    seed_wait_ms: int = Field(default=8_000, ge=0, le=120_000)
    # Captions to count. Counted exactly AND as substrings — see `TextCount`.
    text_probes: list[str] = Field(default_factory=list, max_length=MAX_TEXT_PROBES)
    selector_probes: list[str] = Field(
        default_factory=list, max_length=MAX_SELECTOR_PROBES
    )
    settle_ms: int = Field(default=4_000, ge=0, le=60_000)
    excerpt_chars: int = Field(
        default=DEFAULT_EXCERPT_CHARS, ge=0, le=MAX_EXCERPT_CHARS
    )
    # Wall-clock ceiling for the whole read, enforced by the endpoint.
    budget_s: int = Field(default=180, ge=30, le=600)


class TextCount(BaseModel):
    """Three numbers for one caption, because one number cannot answer this.

    `exact` is what the calibration standard asks for — "不等于 1 就是错的".
    `substring` is what catches the 「允许」/「不允许」 family, where a
    generous match silently votes on the wrong control. `exact_visible` splits
    "present in the DOM" from "on screen", which is the difference between a
    hidden app-shell node and a real control.
    """

    exact: int = 0
    exact_visible: int = 0
    substring: int = 0
    # Set when the probe itself could not be evaluated. Distinct from a count
    # of zero, which is a real finding.
    error: str | None = None


class SelectorCount(BaseModel):
    total: int = 0
    visible: int = 0
    error: str | None = None


class InputSummary(BaseModel):
    """One input / textarea / contenteditable, as attributes only.

    Deliberately no `value`: whatever is in there is what the account owner
    last typed, and it has no business leaving the process.
    """

    tag: str = ""
    type: str | None = None
    accept: str | None = None
    multiple: bool = False
    name: str | None = None
    elem_id: str | None = None
    placeholder: str | None = None
    maxlength: str | None = None
    contenteditable: str | None = None
    class_name: str | None = None
    visible: bool = False


class InspectResponse(BaseModel):
    """Same four-field envelope as every other route, plus the observations."""

    success: bool
    status: SessionStatus
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    url_after: str = ""
    page_title: str = ""
    texts: dict[str, TextCount] = Field(default_factory=dict)
    selectors: dict[str, SelectorCount] = Field(default_factory=dict)
    # Visible text, whitespace-collapsed, scrubbed and truncated. Never HTML.
    body_text_excerpt: str = ""
    body_text_truncated: bool = False
    input_summary: list[InputSummary] = Field(default_factory=list)
    input_total: int = 0
    seeded_files: list[str] = Field(default_factory=list)
    # Same reason as on `PublishResponse` / `VerifyPublishResponse`: an
    # authenticated page load renews the session, and dropping the renewal
    # would make recon a net drain on how long the account stays bound.
    updated_storage_state: dict[str, Any] | None = None
