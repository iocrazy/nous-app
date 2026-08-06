"""Platform-neutral publish orchestration.

The order of the four steps below is the design, and each ordering decision has
a cost attached:

1. **Check the intent** - pure, no I/O. Rejecting a malformed request after
   spending three minutes uploading a video is the most expensive way to
   discover a typo (spec 7.7).
2. **Check the session** - by calling the platform's *one* registered validator
   (spec 7.1). Uploading a few hundred megabytes and only then finding out
   nobody is logged in is the second most expensive.
3. **Stage the assets** - after the session check, so a dead account costs no
   bandwidth at all.
4. **Hand off to the platform publisher**, which owns everything site-specific.

The registry stores *callables*, exactly like the validator registry, because
"publishing" is not necessarily a DOM walk. Xiaohongshu's route is the browser
acting as a signing machine while the upload goes over plain HTTP (design doc
1.3 tier 2, and 6.1c makes room for it an explicit requirement). A registry of
DOM step-lists would have to be torn up to accommodate that; a registry of
coroutines does not care.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from .assets import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    AssetError,
    StagedAsset,
    stage_assets,
    validate_extension,
    validate_media_url,
)
from .config import get_settings
from .redaction import scrub
from .schemas import (
    EnvironmentConfig,
    MediaItem,
    PublishIntent,
    PublishResponse,
    SessionStatus,
)

logger = logging.getLogger("nous_browser.publish")

# S3 scope. Image posts (图文) remain a separate increment. Scheduling is no
# longer refused outright - it is delegated to the platform's own rules, because
# "how far ahead may a post be scheduled" has no channel-neutral answer.
SUPPORTED_CONTENT_TYPES = ("video",)
VIDEO_ROLE = "video"
COVER_ROLE = "cover"


@dataclass(frozen=True)
class IntentProblem:
    """Why an intent cannot be published. Pure data.

    `reason` is a stable machine code and lands in `detail["reason"]`, which
    spec 7.8 reserves for *business* failures - the ones where the account
    genuinely needs a human. Infrastructure failures use `error_kind` instead so
    a caller never marks an account broken because a container was unreachable.
    """

    reason: str
    message: str


@dataclass(frozen=True)
class PlatformIntentRules:
    """The part of the spec 7.7 gate that only one platform can answer.

    Douyin's scheduling window (2h..14d) and its six legal self-declaration
    strings are not channel semantics - a second platform will have different
    numbers and different strings, and hard-coding Douyin's into
    `validate_intent` is how the neutral layer starts accumulating platform
    knowledge (§6.1a forbids exactly that for `SessionStatus`; the same argument
    applies here).

    `supports_scheduling` is a separate flag rather than something `check` can
    express, because the safe default has to be *refusal*: a platform that
    registers no rules at all must not silently publish a scheduled post right
    now. Absent rules ⇒ scheduling refused.

    `check` takes `now` so the window arithmetic is testable without freezing
    the clock.
    """

    supports_scheduling: bool
    check: Callable[[PublishIntent, datetime], "IntentProblem | None"]


@dataclass(frozen=True)
class PublishJob:
    """Everything a platform publisher gets. No wire types, no I/O left to do."""

    platform: str
    storage_state: dict[str, Any]
    environment: EnvironmentConfig | None
    intent: PublishIntent
    assets: Mapping[str, StagedAsset]


@dataclass(frozen=True)
class PublishOutcome:
    status: SessionStatus
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    platform_item_id: str | None = None
    published_url: str | None = None
    updated_storage_state: dict[str, Any] | None = None

    @property
    def success(self) -> bool:
        return self.status is SessionStatus.PUBLISHED


class Deadline:
    """A shared wall-clock budget the whole publish draws from.

    Every stage asks how much is left rather than owning an independent
    timeout, so a slow upload eats into the time available for the rest instead
    of extending the total. That is what keeps a bounded-per-stage design from
    silently becoming an unbounded whole (spec 7.2).

    Cooperative on purpose: stages consult it and return, rather than being
    cancelled mid-flight. A cancelled publish is a publish whose refreshed
    cookies never make it back, and those cookies are the point of step 6.
    """

    def __init__(self, budget_s: float):
        self._expires_at = time.monotonic() + budget_s

    def remaining(self) -> float:
        return max(0.0, self._expires_at - time.monotonic())

    def expired(self) -> bool:
        return self.remaining() <= 0

    def slice_ms(self, ceiling_ms: int) -> int:
        """`ceiling_ms`, or whatever is left if that is less. Never below zero.

        Returned in milliseconds because it feeds Playwright's `timeout=`.
        """
        return max(0, min(ceiling_ms, int(self.remaining() * 1000)))


# The deadline is a parameter rather than something each publisher creates, so
# every platform draws from the same budget the endpoint promised the caller.
Publisher = Callable[[PublishJob, Deadline], Awaitable[PublishOutcome]]


def validate_intent(
    intent: PublishIntent,
    rules: PlatformIntentRules | None = None,
    now: datetime | None = None,
) -> IntentProblem | None:
    """First reason this intent cannot be published, or None. Pure.

    Total by construction: it runs before any browser or network exists, so
    everything it can catch is caught for free.

    `rules` are the platform's own additions to the gate. **Omitting them is
    not neutral** - a platform that registered none cannot schedule, because the
    alternative reading ("no rules, so anything goes") publishes a post booked
    for tomorrow morning right now, which the audience has already seen by the
    time anyone notices.
    """
    if intent.content_type not in SUPPORTED_CONTENT_TYPES:
        return IntentProblem(
            "unsupported_content_type",
            f"content_type '{intent.content_type}' is not supported; "
            f"expected one of {', '.join(SUPPORTED_CONTENT_TYPES)}",
        )

    if intent.scheduled_at is not None and (rules is None or not rules.supports_scheduling):
        return IntentProblem(
            "scheduling_not_supported",
            "scheduled publishing is not supported for this platform; "
            "send scheduled_at=null",
        )

    videos = [item for item in intent.media if item.kind == VIDEO_ROLE]
    if not videos:
        return IntentProblem(
            "missing_video", "a video publish needs exactly one media item of kind 'video'"
        )
    if len(videos) > 1:
        return IntentProblem(
            "too_many_videos",
            f"a video publish takes one video, got {len(videos)}",
        )

    if not intent.title.strip():
        return IntentProblem("empty_title", "title is required for a video publish")

    problem = _check_asset(videos[0], VIDEO_EXTENSIONS, "video")
    if problem is not None:
        return problem

    if intent.cover is not None:
        problem = _check_asset(intent.cover, IMAGE_EXTENSIONS, "cover")
        if problem is not None:
            return problem

    if rules is not None:
        # Last, so a platform rule never pre-empts a neutral one: "there is no
        # video in this request" is a more useful answer than "your scheduled
        # time is 40 minutes too soon" when both are true.
        return rules.check(intent, now or datetime.now(timezone.utc))

    return None


def _check_asset(
    item: MediaItem, allowed: frozenset[str], kind: str
) -> IntentProblem | None:
    url_problem = validate_media_url(item.url)
    if url_problem is not None:
        return IntentProblem(f"bad_{kind}_url", url_problem)

    extension_problem = validate_extension(item.filename, allowed, kind)
    if extension_problem is not None:
        return IntentProblem(f"unsupported_{kind}_type", extension_problem)

    return None


def assets_to_stage(intent: PublishIntent) -> list[tuple[str, MediaItem]]:
    """`(role, item)` pairs to download, in the order they are needed. Pure."""
    videos = [item for item in intent.media if item.kind == VIDEO_ROLE]
    staged: list[tuple[str, MediaItem]] = [(VIDEO_ROLE, videos[0])]
    if intent.cover is not None:
        staged.append((COVER_ROLE, intent.cover))
    return staged


def _response(outcome: PublishOutcome, platform: str) -> PublishResponse:
    detail = dict(outcome.detail)
    detail.setdefault("platform", platform)
    return PublishResponse(
        success=outcome.success,
        status=outcome.status,
        message=outcome.message,
        detail=detail,
        platform_item_id=outcome.platform_item_id,
        published_url=outcome.published_url,
        updated_storage_state=outcome.updated_storage_state,
    )


async def run_publish(
    platform: str,
    publisher: Publisher,
    storage_state: dict[str, Any],
    environment: EnvironmentConfig | None,
    intent: PublishIntent,
) -> PublishResponse:
    # Imported here, not at module scope: `platforms/__init__` imports every
    # platform module, and `douyin_publish` imports this one for `PublishJob` /
    # `Deadline`. At module scope that cycle resolves only when `app.platforms`
    # happens to be imported first, so `import app.publish` on a cold
    # interpreter (a test, a script, a future module) fails on an import order
    # nobody chose.
    from .platforms import get_intent_rules, get_validator

    # Both halves of the gate before anything is launched or downloaded
    # (spec 7.7). The platform half is where a scheduling window and a
    # self-declaration vocabulary live, and both are cheap to check and
    # expensive to discover late: a scheduled time the platform will reject is
    # otherwise found *after* the video has finished transferring.
    problem = validate_intent(intent, get_intent_rules(platform))
    if problem is not None:
        return _response(
            PublishOutcome(
                status=SessionStatus.FAILED,
                message=problem.message,
                detail={"reason": problem.reason, "stage": "intent"},
            ),
            platform,
        )

    validator = get_validator(platform)
    if validator is None:
        # A platform with a publisher but no validator would publish without
        # ever checking the session - the failure mode step 2 exists to prevent.
        return _response(
            PublishOutcome(
                status=SessionStatus.FAILED,
                message=f"no session validator registered for '{platform}'",
                detail={"error_kind": "not_configured", "stage": "precheck"},
            ),
            platform,
        )

    # The single Douyin session check (spec 7.1), reused rather than reimplemented
    # in publish-flavoured form. The reference project's second copy is what kept
    # condemning healthy accounts, and a *third* copy here would be the same bug
    # with the added twist of appearing only after a full upload.
    check = await validator(storage_state, environment)
    if check.status is not SessionStatus.SESSION_VALID:
        # The status passes through unchanged - `proxy_failed` must not arrive at
        # the caller as `session_invalid`, or a proxy outage sends every account
        # off to re-scan a QR code (spec 7.8).
        detail = dict(check.detail)
        detail["stage"] = "precheck"
        return _response(
            PublishOutcome(
                status=check.status,
                message=f"session check failed before upload: {check.message}",
                detail=detail,
            ),
            platform,
        )

    deadline = Deadline(get_settings().publish_total_timeout_s)

    try:
        async with stage_assets(assets_to_stage(intent)) as assets:
            job = PublishJob(
                platform=platform,
                storage_state=storage_state,
                environment=environment,
                intent=intent,
                assets=assets,
            )
            outcome = await publisher(job, deadline)
    except AssetError as exc:
        return _response(
            PublishOutcome(
                status=exc.status,
                message=exc.message,
                detail={**exc.detail, "stage": "assets"},
            ),
            platform,
        )
    except Exception as exc:  # noqa: BLE001 - typed status, never a 500
        logger.exception("publish raised for platform=%s", platform)
        return _response(
            PublishOutcome(
                status=SessionStatus.FAILED,
                message=scrub(f"{type(exc).__name__}: {exc}"),
                detail={"stage": "publisher"},
            ),
            platform,
        )

    return _response(replace(outcome, detail=dict(outcome.detail)), platform)
