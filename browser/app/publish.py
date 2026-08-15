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
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .assets import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    AssetError,
    StagedAsset,
    stage_assets,
    validate_extension,
    validate_media_url,
)
from .capabilities import content_types_for
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

VIDEO_CONTENT_TYPE = "video"
IMAGES_CONTENT_TYPE = "images"

# The answer for a caller that did not name a platform. **No longer the
# capability declaration** - that moved to the per-platform table in the
# dependency-free `app/capabilities.py`, which `supported_content_types_for`
# reads and which the backend guard
# (`backend/tests/test_capability_matches_browser.py`) holds the backend profile
# against. Everything on the real publish path goes through that table; this
# tuple only answers `validate_intent(intent)` calls that pass no
# `supported_content_types`, and it stays at the most conservative real value so
# that path can never be more permissive than the table.
SUPPORTED_CONTENT_TYPES = (VIDEO_CONTENT_TYPE,)
VIDEO_ROLE = "video"
COVER_ROLE = "cover"

# Image posts carry N assets, so the role has to encode *which* one:
# `image:0`, `image:1`, ... (decimal, no zero padding). Reading order off
# `dict` insertion instead would make a user-visible product property - the
# order the gallery is published in - rest on a CPython implementation detail
# that any `dict(...)` rebuild, filter or merge silently breaks (spec D2).
IMAGE_ROLE_PREFIX = "image"
# The `kind` the backend stamps on each gallery item
# (`publish_distribution.py:337` builds `PublishMedia(kind="image", ...)`).
IMAGE_KIND = "image"

# Neutral, defensive bounds - **not** the platform's real limits. Douyin's true
# ceiling is unmeasured (`[TO-VERIFY]` V2); the backend's `MAX_IMAGES = 35`
# (`schemas/distribution_publish.py:46`) says so in its own comment, and T4
# lands the measured value on the platform profile. This pair exists so a
# malformed intent cannot ask the container to download an unbounded number of
# files, and every call site can override it once a better number exists.
NEUTRAL_MIN_IMAGES = 1
NEUTRAL_MAX_IMAGES = 35


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
class ImageBounds:
    """How many images an image post may carry, as data rather than a literal.

    A parameter and not an `if len(images) > 35` inline, because the numbers are
    going to change: the request schema keeps a neutral hard cap, the platform
    profile carries the measured one (spec D3), and the browser gate is the
    backstop for both. A literal here would have to be edited in a third place
    every time - which is the shape of the drift D1 exists to kill.
    """

    minimum: int = NEUTRAL_MIN_IMAGES
    maximum: int = NEUTRAL_MAX_IMAGES


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
    # The caller's handle on this publish, used to address a mid-flight SMS
    # challenge (`publish_sms`). `None` means the caller has no channel to the
    # user, and a challenge must then fail rather than park on a code nobody
    # can supply.
    correlation_id: str | None = None


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

    def extend(self, seconds: float) -> None:
        """Give back wall-clock that was spent waiting for a *person*.

        The one sanctioned exception to "a slow stage eats the rest", and it is
        not an exception to the principle behind it: that rule exists so slow
        *machine* work cannot make the whole unbounded. Time parked on a human
        supplying a verification code is not work — the browser is idle — and
        charging it to the budget would mean a correctly supplied code could
        still lose the post to a deadline the waiting itself consumed.

        Bounded by construction, not by trust: the only caller draws from a
        separate, fixed SMS window (`publish_sms.SmsWindow`), so the total
        extension a publish can win is that window and nothing more. The
        endpoint's hard ceiling is computed from exactly that sum
        (`publish_budget.browser_hard_ceiling_s`), so this can never push a
        publish past what the caller was promised.
        """
        if seconds > 0:
            self._expires_at += seconds

    def slice_ms(self, ceiling_ms: int) -> int:
        """`ceiling_ms`, or whatever is left if that is less. Never below zero.

        Returned in milliseconds because it feeds Playwright's `timeout=`.
        """
        return max(0, min(ceiling_ms, int(self.remaining() * 1000)))


# The deadline is a parameter rather than something each publisher creates, so
# every platform draws from the same budget the endpoint promised the caller.
Publisher = Callable[[PublishJob, Deadline], Awaitable[PublishOutcome]]


def supported_content_types_for(platform: str) -> tuple[str, ...]:
    """Content types this browser can actually publish for `platform`.

    The seam T2 left, now filled: the answer is a lookup in the
    dependency-free `app/capabilities.py` table, which is the single source of
    truth for this claim. Nothing else in this module moved - `validate_intent`
    reads the answer from a parameter, so it never has to know which shape is
    in force.

    **Per platform, not global**, and that is the whole point. A single module
    tuple means the day Douyin learns image posts, *every* platform with a
    registered publisher starts passing the content-type gate for galleries -
    including ones that have not written a line of gallery code (spec §1.1
    defect 2). The gate would be answering "does anybody support this?" while
    the caller asked about one account's platform.

    An unknown platform gets `()` - refuse everything - rather than falling
    back to `SUPPORTED_CONTENT_TYPES`. A platform nobody declared has, by
    definition, no publisher written for it, so "video is fine" would be a
    claim about a code path that does not exist. Absence collapses toward
    refusal here exactly as it does for `PlatformIntentRules`.
    """
    return content_types_for(platform)


def validate_intent(
    intent: PublishIntent,
    rules: PlatformIntentRules | None = None,
    now: datetime | None = None,
    *,
    supported_content_types: Sequence[str] | None = None,
    image_bounds: ImageBounds | None = None,
) -> IntentProblem | None:
    """First reason this intent cannot be published, or None. Pure.

    Total by construction: it runs before any browser or network exists, so
    everything it can catch is caught for free.

    `rules` are the platform's own additions to the gate. **Omitting them is
    not neutral** - a platform that registered none cannot schedule, because the
    alternative reading ("no rules, so anything goes") publishes a post booked
    for tomorrow morning right now, which the audience has already seen by the
    time anyone notices.

    `supported_content_types` is the caller's answer to "what can this platform
    publish". Omitting it falls back to the global tuple, which is the same
    safe direction: an unknown platform publishes nothing, never everything.
    """
    allowed = (
        tuple(supported_content_types)
        if supported_content_types is not None
        else SUPPORTED_CONTENT_TYPES
    )
    if intent.content_type not in allowed:
        return IntentProblem(
            "unsupported_content_type",
            f"content_type '{intent.content_type}' is not supported; "
            + (
                f"expected one of {', '.join(allowed)}"
                if allowed
                else "this platform publishes no content types"
            ),
        )

    if intent.scheduled_at is not None and (rules is None or not rules.supports_scheduling):
        return IntentProblem(
            "scheduling_not_supported",
            "scheduled publishing is not supported for this platform; "
            "send scheduled_at=null",
        )

    if intent.content_type == IMAGES_CONTENT_TYPE:
        problem = _check_images_intent(intent, image_bounds or ImageBounds())
    else:
        problem = _check_video_intent(intent)
    if problem is not None:
        return problem

    if rules is not None:
        # Last, so a platform rule never pre-empts a neutral one: "there is no
        # video in this request" is a more useful answer than "your scheduled
        # time is 40 minutes too soon" when both are true.
        return rules.check(intent, now or datetime.now(timezone.utc))

    return None


def _check_video_intent(intent: PublishIntent) -> IntentProblem | None:
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
        return _check_asset(intent.cover, IMAGE_EXTENSIONS, "cover")

    return None


def _check_images_intent(
    intent: PublishIntent, bounds: ImageBounds
) -> IntentProblem | None:
    """The image-post half of the gate. Pure.

    The cover check is first on purpose. "This channel has no separate cover"
    is a statement about the shape of the request the caller must stop sending
    (spec D4), whereas a count or an extension is a value they can adjust; and
    answering it first means the refusal cannot be masked by whichever image
    happens to also be wrong.
    """
    if intent.cover is not None:
        return IntentProblem(
            "cover_not_supported_for_images",
            "an image post has no separate cover; the first image is the cover. "
            "Send cover=null and order the images instead",
        )

    images = [item for item in intent.media if item.kind == IMAGE_KIND]
    foreign = sorted({item.kind for item in intent.media if item.kind != IMAGE_KIND})
    if foreign:
        # Dropping them silently would publish a gallery the user never
        # composed, which is the same failure `ordered_image_assets` refuses to
        # commit further down: an image post is exactly its media list.
        return IntentProblem(
            "unexpected_media_kind",
            "an image post takes media items of kind 'image' only; got "
            f"{', '.join(repr(kind) for kind in foreign)}",
        )

    if len(images) < bounds.minimum:
        return IntentProblem(
            "too_few_images",
            f"an image post needs at least {bounds.minimum} image(s), got {len(images)}",
        )
    if len(images) > bounds.maximum:
        return IntentProblem(
            "too_many_images",
            f"an image post takes at most {bounds.maximum} images, got {len(images)}",
        )

    if not intent.title.strip():
        return IntentProblem("empty_title", "title is required for an image publish")

    for position, item in enumerate(images):
        problem = _check_asset(item, IMAGE_EXTENSIONS, "image")
        if problem is not None:
            # Which one, not just "one of them". Fifteen images with one bad
            # extension is otherwise a request the caller has to bisect by hand.
            return replace(problem, message=f"image {position}: {problem.message}")

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


def image_role(index: int) -> str:
    """The role for the `index`-th image. Decimal, no zero padding.

    One function so the format has one definition: the writer here and the
    reader in `ordered_image_assets` cannot drift into disagreeing about
    padding, which is the one way `image:01` and `image:1` could both appear
    and quietly become two entries for the same position.
    """
    return f"{IMAGE_ROLE_PREFIX}:{index}"


def assets_to_stage(intent: PublishIntent) -> list[tuple[str, MediaItem]]:
    """`(role, item)` pairs to download, in the order they are needed. Pure."""
    if intent.content_type == IMAGES_CONTENT_TYPE:
        # No cover: an image post's cover is its first image (spec D4), so
        # producing a COVER_ROLE here would stage a file no publisher may use.
        return [
            (image_role(index), item)
            for index, item in enumerate(
                item for item in intent.media if item.kind == IMAGE_KIND
            )
        ]

    videos = [item for item in intent.media if item.kind == VIDEO_ROLE]
    staged: list[tuple[str, MediaItem]] = [(VIDEO_ROLE, videos[0])]
    if intent.cover is not None:
        staged.append((COVER_ROLE, intent.cover))
    return staged


def ordered_image_assets(assets: Mapping[str, StagedAsset]) -> tuple[StagedAsset, ...]:
    """The staged images in publish order. Pure, and refuses to guess.

    Order comes from the index in the role, never from iteration order, so a
    caller that rebuilt or filtered the mapping still gets the gallery the user
    composed - or an error, if the mapping cannot describe one.

    A gap or a duplicate is an upstream assembly bug, and the tempting recovery
    ("publish the ones that are there") is the worst available outcome: it
    sends a real post, to a real audience, in an order nobody chose, and looks
    like a success. `AssetError` instead, so `run_publish` reports a typed
    failure with the same machinery a failed download uses.
    """
    indexed: dict[int, StagedAsset] = {}
    for role, asset in assets.items():
        prefix, separator, suffix = role.partition(":")
        if not separator or prefix != IMAGE_ROLE_PREFIX:
            continue
        # ASCII digits only, and no zero padding: `image:01` would otherwise
        # parse to the same position as `image:1` while being a different key,
        # so the duplicate check below could never see it.
        if not (suffix.isascii() and suffix.isdigit()) or suffix != str(int(suffix)):
            raise AssetError(
                SessionStatus.FAILED,
                f"image asset role '{role}' is not '{IMAGE_ROLE_PREFIX}:<decimal index>'",
                reason="image_role_malformed",
                role=role,
            )
        index = int(suffix)
        if index in indexed:
            raise AssetError(
                SessionStatus.FAILED,
                f"two staged assets claim image position {index}",
                reason="image_role_duplicate",
                role=role,
            )
        indexed[index] = asset

    if not indexed:
        raise AssetError(
            SessionStatus.FAILED,
            "an image publish staged no images",
            reason="image_roles_missing",
        )

    expected = list(range(len(indexed)))
    if sorted(indexed) != expected:
        missing = [index for index in expected if index not in indexed]
        raise AssetError(
            SessionStatus.FAILED,
            f"staged image positions {sorted(indexed)} are not 0..{len(indexed) - 1}; "
            f"missing {missing}",
            reason="image_role_gap",
        )

    return tuple(indexed[index] for index in expected)


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
    correlation_id: str | None = None,
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
    problem = validate_intent(
        intent,
        get_intent_rules(platform),
        supported_content_types=supported_content_types_for(platform),
    )
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
                correlation_id=correlation_id,
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
