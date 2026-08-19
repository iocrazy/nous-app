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

from pydantic import BaseModel, Field, field_validator


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
    # The platform signs in by text message and we do not have a number to send
    # it to yet. Split from `SMS_REQUIRED` for the same reason
    # `IDENTITY_CHALLENGE` is: the two license opposite sentences. `sms_required`
    # means "type the code you were sent"; saying that before anybody has given
    # us a phone number leaves the user waiting for a message that could not
    # possibly have been sent — nothing was requested, because nothing knew
    # where to send it (2026-08-13, Xiaohongshu: its creator platform has no QR
    # sign-in, only 手机号 + 验证码).
    PHONE_REQUIRED = "phone_required"
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
    # Null on platforms that do not sign in with a QR code at all (see
    # `capabilities.PLATFORM_LOGIN_METHODS`). It was non-optional while every
    # login was assumed to be a scan, which is the assumption that made the
    # SMS-only platform fail at `start` with "login page rendered no QR code".
    qrcode_data_url: str | None = None
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


class PhoneNumberRequest(BaseModel):
    """The account's phone number, on platforms that sign in by text message.

    Digits only and bounded, like `SmsCodeRequest`: it is typed straight into a
    page. Deliberately **not** pinned to 11 digits — that is the mainland mobile
    format, and hard-coding one country's shape into the channel contract is the
    same category of mistake as assuming every platform renders a QR code.

    It is never logged and never persisted: it goes into the live page and
    nothing else. `redaction.scrub_page_text` masks digit runs of five or more,
    so it cannot ride back out inside captured page text either.
    """

    phone: str = Field(min_length=6, max_length=20, pattern=r"^\d+$")


class PhoneNumberResponse(BaseModel):
    """Same envelope as `SmsCodeResponse`, and for the same reason.

    Submitting a number is a *user action*, so its failures have to be typed
    and visible: `detail["reason"]` says whether the page had no number field
    at all or whether the platform's own "send me the code" button could not be
    pressed. Both are unverified-selector failures on a platform nobody has
    bound yet, and both must land in front of the user rather than leaving the
    modal sitting on a code field no code is coming to.
    """

    status: SessionStatus
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
    # **Minted by the caller, not by us**, and that inversion is the whole
    # mechanism (see `publish_sms`). A publish is one blocking call, so there is
    # no response in which to hand an id back — the only way the caller can
    # address a challenge belonging to a publish still in flight is to have
    # named it first.
    #
    # Optional so a caller that never supplies codes is unaffected: without it
    # a mid-publish SMS challenge fails exactly as it did before, which is the
    # honest answer for a caller that has no channel to the user.
    correlation_id: str | None = Field(default=None, max_length=128)


class PublishSmsStatusResponse(BaseModel):
    """Is this publish parked on a verification code right now?

    Polled by the backend *while its own publish call is still outstanding* —
    the one thing it can do from outside a blocking request. Cheap and pure: it
    reads registry state and never touches the page.
    """

    # False for both "no such publish" and "that publish is not asking", which
    # are the same instruction to the caller: nothing to show the user.
    waiting: bool
    correlation_id: str | None = None
    platform: str | None = None
    # Set once the challenge is over, so a poll that arrives late reports how
    # it ended rather than looking like it never happened.
    outcome: str | None = None
    message: str = ""
    attempts_left: int = 0
    max_attempts: int = 0
    seconds_remaining: float = 0.0


class PublishSmsSubmitResponse(BaseModel):
    """What the page did with the code. The reverse channel.

    `outcome` is the field that matters and it is a closed vocabulary
    (`publish_sms`): `accepted`, `rejected`, `exhausted`, `expired`,
    `abandoned`, `not_pending`. A submitted code that merely returns 200 tells
    the user nothing — and "the user did a thing and nothing visibly happened"
    is the failure this whole change exists to remove, so it must not be
    reintroduced at the last hop.
    """

    outcome: str
    message: str
    attempts_left: int = 0
    # Whether the user can usefully type another code. Derived here rather than
    # left to each client to infer from `outcome` + `attempts_left`, because two
    # clients inferring it separately is two chances to disagree with the
    # browser about whether the publish is still listening.
    retryable: bool = False


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


# --- typed-input reconnaissance ---------------------------------------------
#
# The second recon endpoint. `/session/inspect` reads a page that is sitting
# still; this one puts a probe string into one editable node and reports the
# network the page produced **as a result** — the only way to learn where an
# as-you-type suggestion list comes from. See `app/probe.py` for the boundary
# (same allow-list, same source-text guard, plus `focus`/`press_sequentially`).

MAX_TARGET_SELECTORS = 8
MAX_OBSERVE_SELECTORS = 12
MAX_CAPTURE_FILTERS = 16
MAX_CAPTURES = 24
MAX_CAPTURE_BODY_CHARS = 8_000
DEFAULT_CAPTURE_BODY_CHARS = 3_000
MAX_PROBE_TEXT_CHARS = 64
MAX_PRE_STEPS = 6
MAX_POST_STEPS = 8
MAX_REPLAY_FILTERS = 8


class ProbeActivationStep(BaseModel):
    """One control the recon run may activate, named by its rendered caption.

    ⚠️ `label` is checked against `probe_actions.PROBE_LABELS` **here**, at the
    schema layer, which means a request naming anything else is a 422 before a
    browser process exists. `probe_actions.activate_label` checks the same rule
    again at call time; two checks of one rule, because the schema one protects
    the endpoint and the runtime one protects any caller that builds the model
    in code.

    There is deliberately no way to name a **selector** to activate. The list
    of activatable things is a closed vocabulary in our own source, not
    something a request can widen.
    """

    label: str = Field(min_length=1, max_length=32)
    #: Read-only predicate: which selector becoming visible means this
    #: activation did what it was supposed to. Without it, "the caption was
    #: pressed" and "the panel opened" are the same observation, and the first
    #: one is not the finding.
    until_selectors: list[str] = Field(default_factory=list, max_length=4)
    #: How many identically-captioned nodes to try. 「选择音乐」 is exact=2 on
    #: the live page (heading + button) and the heading is inert.
    candidates: int = Field(default=4, ge=1, le=6)
    settle_ms: int = Field(default=4_000, ge=0, le=30_000)
    #: Snapshot taken right after this step — how the panel/tab renders, which
    #: is the fallback answer when the data never crossed the network.
    observe_selectors: list[str] = Field(
        default_factory=list, max_length=MAX_OBSERVE_SELECTORS
    )

    @field_validator("label")
    @classmethod
    def _label_is_allow_listed(cls, value: str) -> str:
        from .probe_actions import label_refusal

        refusal = label_refusal(value)
        if refusal is not None:
            raise ValueError(refusal)
        return value


class ProbeRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    # Plaintext, decrypted by the backend. Memory only (spec 7.6).
    storage_state: dict[str, Any]
    environment: EnvironmentConfig | None = None
    # Same allow-list as `/session/inspect`, enforced by the same function.
    url: str = Field(min_length=1, max_length=2048)

    # Reaching the box. Douyin only renders a description editor after an
    # upload, so the probe needs the same seeding path the read-only recon has.
    seed_files: list[MediaItem] = Field(default_factory=list, max_length=MAX_SEED_FILES)
    seed_selector: str = Field(
        default=DEFAULT_SEED_SELECTOR, min_length=1, max_length=200
    )
    seed_input_index: int = Field(default=0, ge=0, le=20)
    seed_wait_ms: int = Field(default=10_000, ge=0, le=120_000)

    # Where to type. A list because the post editor ships in two parallel gray
    # releases whose description boxes carry different attributes; the first
    # candidate that becomes visible wins.
    target_selectors: list[str] = Field(
        min_length=1, max_length=MAX_TARGET_SELECTORS
    )
    target_index: int = Field(default=0, ge=0, le=20)
    target_wait_ms: int = Field(default=60_000, ge=1_000, le=300_000)

    # Activations, in two positions relative to the typing. `pre_steps` reach
    # a box that does not exist yet (the 「选择音乐」 panel and its search
    # field); `post_steps` change what is being listed once we are inside it
    # (its tab row). Both are recorded with the traffic they caused, which is
    # what makes "this tab has its own endpoint" answerable at all.
    pre_steps: list[ProbeActivationStep] = Field(
        default_factory=list, max_length=MAX_PRE_STEPS
    )
    post_steps: list[ProbeActivationStep] = Field(
        default_factory=list, max_length=MAX_POST_STEPS
    )
    # Some panels only search on Enter. Off by default; when on, the key goes
    # through `probe_actions.press_confirmed_key`, which refuses any key but
    # Enter and any target that is not verifiably an `input` / `textarea`.
    press_enter_after_typing: bool = False

    # What to type. Bounded hard: this string goes into a real account's
    # composer, and there is no reason a reconnaissance probe needs a sentence.
    probe_text: str = Field(min_length=1, max_length=MAX_PROBE_TEXT_CHARS)
    # Per-keystroke delay. Real platforms debounce; typing instantly is the
    # classic way to observe zero requests and conclude the wrong thing.
    keystroke_delay_ms: int = Field(default=120, ge=0, le=2_000)

    settle_ms: int = Field(default=5_000, ge=0, le=60_000)
    # How long to keep listening after the last keystroke.
    capture_settle_ms: int = Field(default=6_000, ge=0, le=60_000)

    # Which recorded responses get the detailed treatment. Empty = all of them,
    # which is the right default the first time you look at an unknown page.
    capture_url_contains: list[str] = Field(
        default_factory=list, max_length=MAX_CAPTURE_FILTERS
    )
    max_captures: int = Field(default=12, ge=1, le=MAX_CAPTURES)
    capture_body_chars: int = Field(
        default=DEFAULT_CAPTURE_BODY_CHARS, ge=0, le=MAX_CAPTURE_BODY_CHARS
    )
    max_replay_targets: int = Field(default=2, ge=0, le=8)
    # A recorded call becomes replayable when it carried our typed word **or**
    # when its URL matches one of these. The second door exists because a list
    # that loads when a panel opens — a recommendation feed, a chart tab —
    # never carries a keyword, and refusing to replay it would leave the
    # decisive question ("can we call this without a browser?") unanswerable
    # for exactly the endpoints a sidebar would need.
    replay_url_contains: list[str] = Field(
        default_factory=list, max_length=MAX_REPLAY_FILTERS
    )

    # Nodes to read after typing — the suggestion dropdown, typically. Its
    # rendered rows answer "what does a suggestion look like" even when the
    # data never crossed the network.
    observe_selectors: list[str] = Field(
        default_factory=list, max_length=MAX_OBSERVE_SELECTORS
    )

    budget_s: int = Field(default=240, ge=30, le=600)


class CapturedParam(BaseModel):
    """One query parameter. The value is masked when the name looks credential-
    shaped; the length survives either way, because "there is a 172-character
    signature parameter here" is a stronger finding than "there is one"."""

    name: str = ""
    value: str = ""
    redacted: bool = False
    length: int = 0


class CapturedCall(BaseModel):
    """One response the page produced while we typed.

    Headers are **names only**. There is no redaction rule for header values
    that would be safe here — `cookie` is a header — and the names alone
    answer the question this endpoint exists for ("what does this call need").
    """

    method: str = ""
    host: str = ""
    path: str = ""
    query_param_names: list[str] = Field(default_factory=list)
    query_params: list[CapturedParam] = Field(default_factory=list)
    # The subset that means "this request is signed". Empty is the finding
    # that decides whether we can call it from our own server.
    signature_params: list[str] = Field(default_factory=list)
    # Which parameter carried the string we typed, detected by value.
    keyword_param: str | None = None
    request_header_names: list[str] = Field(default_factory=list)
    post_data_param_names: list[str] = Field(default_factory=list)
    status: int = 0
    response_content_type: str = ""
    resource_type: str = ""
    body_chars: int = 0
    # JSON bodies keep their numbers (a play count is public data about a
    # topic); anything unparseable falls back to the digit-masking scrubber.
    body_excerpt: str = ""
    body_json_top_keys: list[str] = Field(default_factory=list)
    #: Which run step this response arrived during — `"load"`, `"type"`, or
    #: `"activate:<label>"`. Attribution is the whole point once a run has more
    #: than one step: "the platform fetched something" is weak, "taking the
    #: 热门榜 tab fetched this" is the finding.
    phase: str = ""


class ObservedNode(BaseModel):
    """One element inside the box we typed into. Attribute **names**, and a
    short text excerpt — enough to see whether the platform turned `#word`
    into a styled entity node or left it as literal text."""

    tag: str = ""
    class_name: str = ""
    attr_names: list[str] = Field(default_factory=list)
    text: str = ""


class ObservedSelector(BaseModel):
    selector: str = ""
    total: int = 0
    visible: int = 0
    texts: list[str] = Field(default_factory=list)
    class_names: list[str] = Field(default_factory=list)
    error: str | None = None


class ReplayTarget(BaseModel):
    """A call worth trying **without** a browser. Carries a raw URL.

    The one un-redacted thing this endpoint emits, and it is internal
    transport: the backend consumes it to run the decisive experiment (does
    the same URL still answer when the keyword changes and no browser is
    involved?) and never forwards it. Same class as `updated_storage_state`.
    """

    method: str = "GET"
    url: str = ""
    referer: str = ""
    keyword_param: str | None = None
    keyword_value: str = ""
    header_names: list[str] = Field(default_factory=list)
    #: The step this call belonged to, carried through so a replay result can
    #: be attributed to the tab that produced it.
    phase: str = ""


class ProbeStepResult(BaseModel):
    """One step of a recon run, and what the page did during it.

    `activated=False` with a non-empty `error` is a *result*, not an exception:
    a caption that moved and a control that vanished look identical from the
    outside, and `text_mismatches` is what tells them apart.
    """

    kind: str = ""  # "activate" | "type"
    label: str = ""
    phase: str = ""
    matches: int = 0
    index: int = -1
    activated: bool = False
    error: str = ""
    text_mismatches: list[str] = Field(default_factory=list)
    #: How many responses the page produced while this step ran.
    responses: int = 0
    observed: list[ObservedSelector] = Field(default_factory=list)


class ProbeResponse(BaseModel):
    success: bool
    status: SessionStatus
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    url_after: str = ""
    page_title: str = ""

    target_selector_used: str = ""
    #: Non-empty when the typing target never became visible. The run then
    #: reports **everything it did capture** rather than throwing the evidence
    #: away — a panel that failed to open and a panel that opened and fetched
    #: nothing are different findings, and only this field separates them.
    target_error: str | None = None
    # Falsifiability. Without this, "the platform issued no request" and "our
    # keystrokes never landed" produce identical output, and we would report
    # the first while the second was true.
    typed_text_landed: bool = False
    target_text_before: str = ""
    target_text_after: str = ""
    target_child_total: int = 0
    target_nodes: list[ObservedNode] = Field(default_factory=list)
    observed_selectors: list[ObservedSelector] = Field(default_factory=list)
    #: One entry per activation and for the typing itself, in order.
    steps: list[ProbeStepResult] = Field(default_factory=list)

    # Every response, by host, for the whole run — a histogram, not a log.
    host_totals: dict[str, int] = Field(default_factory=dict)
    captures: list[CapturedCall] = Field(default_factory=list)
    captures_dropped: int = 0

    replay_targets: list[ReplayTarget] = Field(default_factory=list)
    replay_user_agent: str = ""

    seeded_files: list[str] = Field(default_factory=list)
    updated_storage_state: dict[str, Any] | None = None


# --- music chart harvest -----------------------------------------------------
#
# The 「选择音乐」 panel's chart tabs, read once and handed to the backend to
# cache. Why a harvest instead of an on-demand fetch: the panel's two endpoints
# refuse every mutated replay [实测 2026-08-19] — the signature is bound to the
# whole query — so a chart can only be read by the platform's own page, which
# only exists after an upload. That makes this a scheduled job with one side
# effect, not something a page view can trigger.


class MusicSongPayload(BaseModel):
    """One track, in the vocabulary a picker renders.

    `music_id` is a STRING and stays one all the way to the frontend. It is a
    19-digit Snowflake-scale id, and JSON numbers lose precision above 2^53 —
    the same trap `bigIntSafeFetch` exists for on the app's own ids.
    """

    music_id: str
    music_name: str
    music_author: str = ""
    duration_s: int = 0
    #: `null` = the platform did not say. **Not `0`** — zero is a real
    #: catalogue value, and a track with exactly that produced a production
    #: refusal on 2026-08-17.
    user_count: int | None = None
    cover_url: str = ""
    play_url: str = ""


class MusicChartPayload(BaseModel):
    """One tab's worth of tracks, plus whether we managed to read it.

    `ok=True, songs=[]` and `ok=False` are different and both happen: the first
    is 收藏 on an account with no favourites (measured), the second is a tab
    that answered nothing. A caller that merges them will either show a blank
    tab as if the platform were empty, or keep retrying an account that simply
    has no favourites.
    """

    category_id: str
    category_name: str
    #: `recommend` / `rank` / `fav` / `category`. Load-bearing: 推荐 and 收藏
    #: both answer `category_id="1"` and differ only here, so a store keyed on
    #: the id alone merges two charts into one.
    category_kind: str
    ok: bool = False
    error: str = ""
    cursor: str = ""
    has_more: bool = False
    songs: list[MusicSongPayload] = Field(default_factory=list)


class MusicHarvestRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    # Plaintext, decrypted by the backend. Memory only (spec 7.6).
    storage_state: dict[str, Any]
    environment: EnvironmentConfig | None = None
    url: str = Field(min_length=1, max_length=2048)

    # The editor — and therefore the panel — does not exist before an upload.
    # Required, not optional: a request without one cannot succeed, and taking
    # it as optional would turn "you forgot the seed" into "the platform has no
    # charts".
    seed_file: MediaItem
    seed_selector: str = Field(
        default='input[type="file"][multiple]', min_length=1, max_length=200
    )
    seed_wait_ms: int = Field(default=60_000, ge=0, le=180_000)

    settle_ms: int = Field(default=5_000, ge=0, le=60_000)
    panel_settle_ms: int = Field(default=6_000, ge=0, le=60_000)
    tab_settle_ms: int = Field(default=4_000, ge=0, le=30_000)
    click_timeout_ms: int = Field(default=8_000, ge=1_000, le=60_000)
    max_charts: int = Field(default=16, ge=1, le=32)
    budget_s: int = Field(default=420, ge=60, le=900)


class MusicHarvestResponse(BaseModel):
    """Same four-field envelope as every other route, plus the charts."""

    success: bool
    status: SessionStatus
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    charts: list[MusicChartPayload] = Field(default_factory=list)
    # Same reason as on the recon routes: an authenticated page load renews the
    # session, and dropping the renewal would make this job a net drain on how
    # long the account stays bound.
    updated_storage_state: dict[str, Any] | None = None
