"""Everything `run_publish` decides before a browser exists.

`validate_intent` is the spec 7.7 fail-fast gate: whatever it fails to catch is
paid for with a browser launch and a few hundred megabytes of upload before the
same request dies anyway. `Deadline` is the spec 7.2 upper bound - the thing
that keeps a design of bounded stages from adding up to an unbounded whole.
Both are pure, so both get tables.
"""

from datetime import datetime, timezone

import pytest

from app.assets import AssetError, StagedAsset
from app.publish import (
    Deadline,
    ImageBounds,
    PublishJob,
    PublishOutcome,
    assets_to_stage,
    image_role,
    ordered_image_assets,
    supported_content_types_for,
    validate_intent,
)
from app.schemas import MediaItem, PublishIntent, SessionStatus

pytestmark = pytest.mark.unit

VIDEO_URL = "https://nous-backend:8080/signed/clip.mp4"
COVER_URL = "https://nous-backend:8080/signed/cover.jpg"
IMAGE_URL = "https://nous-backend:8080/signed/slide.jpg"

# What a platform that has an image-post publisher would hand `validate_intent`.
# Spelled out at every call site rather than patched into the module constant,
# because the *default* (video only) is itself under test below.
IMAGES_ENABLED = ("video", "images")


def video(**overrides) -> MediaItem:
    fields = {"kind": "video", "url": VIDEO_URL, "filename": "clip.mp4"}
    fields.update(overrides)
    return MediaItem(**fields)


def cover(**overrides) -> MediaItem:
    fields = {"kind": "cover", "url": COVER_URL, "filename": "cover.jpg"}
    fields.update(overrides)
    return MediaItem(**fields)


def image(index: int = 0, **overrides) -> MediaItem:
    fields = {
        "kind": "image",
        "url": f"{IMAGE_URL}?i={index}",
        "filename": f"slide-{index}.jpg",
    }
    fields.update(overrides)
    return MediaItem(**fields)


def intent(**overrides) -> PublishIntent:
    fields = {
        "content_type": "video",
        "media": [video()],
        "title": "Launch Day Recap",
    }
    fields.update(overrides)
    return PublishIntent(**fields)


def images_intent(count: int = 3, **overrides) -> PublishIntent:
    fields = {
        "content_type": "images",
        "media": [image(i) for i in range(count)],
        "title": "Launch Day Gallery",
    }
    fields.update(overrides)
    return PublishIntent(**fields)


def staged(role: str) -> StagedAsset:
    return StagedAsset(role=role, path=f"/tmp/{role}", filename=f"{role}.jpg", size_bytes=1)


# --- accepted ---------------------------------------------------------------


def test_a_plain_video_intent_is_accepted():
    assert validate_intent(intent()) is None


def test_a_video_with_a_cover_is_accepted():
    assert validate_intent(intent(cover=cover())) is None


@pytest.mark.parametrize("filename", ["a.mp4", "a.MOV", "a.m4v", "a.webm", "a.avi", "a.mkv"])
def test_every_whitelisted_video_extension_is_accepted(filename):
    assert validate_intent(intent(media=[video(filename=filename)])) is None


@pytest.mark.parametrize("filename", ["c.jpg", "c.JPEG", "c.png", "c.webp", "c.bmp"])
def test_every_whitelisted_cover_extension_is_accepted(filename):
    assert validate_intent(intent(cover=cover(filename=filename))) is None


# --- rejected ---------------------------------------------------------------


@pytest.mark.parametrize(
    "bad, expected_reason",
    [
        (dict(content_type="image"), "unsupported_content_type"),
        (dict(content_type="text"), "unsupported_content_type"),
        (dict(media=[]), "missing_video"),
        (dict(media=[cover()]), "missing_video"),
        (dict(media=[video(), video(filename="second.mp4")]), "too_many_videos"),
        (dict(title=""), "empty_title"),
        (dict(title="   \n "), "empty_title"),
        (dict(media=[video(url="file:///etc/passwd")]), "bad_video_url"),
        (dict(media=[video(url="ftp://host/clip.mp4")]), "bad_video_url"),
        (dict(media=[video(url="https:///clip.mp4")]), "bad_video_url"),
        (dict(media=[video(filename="clip.exe")]), "unsupported_video_type"),
        (dict(media=[video(filename="clip")]), "unsupported_video_type"),
        (dict(cover=cover(filename="cover.txt")), "unsupported_cover_type"),
        (dict(cover=cover(url="file:///etc/passwd")), "bad_cover_url"),
    ],
)
def test_bad_intents_are_refused_with_a_stable_reason_code(bad, expected_reason):
    problem = validate_intent(intent(**bad))
    assert problem is not None
    assert problem.reason == expected_reason
    assert problem.message


# --- the content-type gate is per platform, not global ----------------------
#
# `supported_content_types_for` is the seam: `validate_intent` takes the answer
# as a parameter, and this function is what `run_publish` fills it from. These
# tests pin the *lookup*, because that is where the per-platform claim lives.


def test_douyin_declares_both_content_types_after_t7():
    """The declaration a user can currently act on.

    This was `test_douyin_declares_video_only_today` and its docstring said the
    flip "is T7's job and must happen in the same PR as the backend profile" -
    this edit *is* that flip. `_drive_images` (T3) landed first; the backend
    guard in `backend/tests/test_capability_matches_browser.py` is what makes
    "same PR" mechanical rather than a promise in a comment.

    Still pinning the exact tuple: everything downstream - the backend profile,
    `GET /distribution/capabilities`, the greyed-out Images tab - is derived
    from this one value, so it should never move by accident.
    """
    assert supported_content_types_for("douyin") == ("video", "images")


def test_an_unknown_platform_publishes_nothing():
    """A platform nobody declared has no publisher, so the honest answer to
    "can it post this?" is no - for every content type, video included.

    Not `SUPPORTED_CONTENT_TYPES`: falling back to the global tuple would have
    the gate claim "video is fine" about a code path that does not exist, so a
    typo in a platform name would reach the registry lookup with the neutral
    gate already satisfied. Absence collapses toward refusal, as it does for
    `PlatformIntentRules`.
    """
    assert supported_content_types_for("nosuchplatform") == ()

    problem = validate_intent(
        intent(), supported_content_types=supported_content_types_for("nosuchplatform")
    )
    assert problem is not None
    assert problem.reason == "unsupported_content_type"


def test_a_platform_that_declares_only_video_refuses_an_image_post():
    """The reason the declaration is per platform rather than one module-level
    tuple: Douyin has now learned galleries, and a *global* set would have
    opened this gate for every platform with a registered publisher - including
    ones that have not written a line of gallery code. The gate would be
    answering "does anybody support this?" while the caller asked about one
    account's platform.

    That day has arrived, which is why this test no longer uses Douyin: it now
    states the property directly with a video-only declaration. Leaving it
    pointed at Douyin would have turned it into an assertion about a platform
    that *does* support images - green for the wrong reason, and no longer a
    guard on per-platform scoping at all.
    """
    problem = validate_intent(
        intent(content_type="images", media=[image(0)]),
        supported_content_types=("video",),
    )
    assert problem is not None
    assert problem.reason == "unsupported_content_type"


def test_douyin_now_accepts_an_image_post_through_the_neutral_gate():
    """The user-visible effect of the T7 flip at this layer: a well-formed
    gallery intent is no longer refused by the neutral gate for Douyin.

    Paired with the test above so the two directions are pinned separately -
    "Douyin accepts images" and "a video-only platform still refuses them" are
    different claims, and collapsing them into one test loses whichever half
    stops being checked.
    """
    problem = validate_intent(
        intent(content_type="images", media=[image(0)]),
        supported_content_types=supported_content_types_for("douyin"),
    )
    assert problem is None


def test_the_lookup_follows_the_capability_table_rather_than_a_hardcoded_list(
    monkeypatch,
):
    """Proves the two answers above come from `capabilities.py` and are not
    `platform == "douyin"` written a longer way. Declare a fictional platform
    that takes images, and the lookup - and the gate fed from it - follow.
    """
    import app.capabilities as caps

    monkeypatch.setitem(caps.PLATFORM_CONTENT_TYPES, "testonly", ("images",))

    assert supported_content_types_for("testonly") == ("images",)
    assert validate_intent(
        intent(content_type="images", media=[image(0)]),
        supported_content_types=supported_content_types_for("testonly"),
    ) is None
    # ...and the same table refuses what it does not list.
    problem = validate_intent(
        intent(), supported_content_types=supported_content_types_for("testonly")
    )
    assert problem is not None
    assert problem.reason == "unsupported_content_type"


def test_scheduling_without_platform_rules_is_refused_not_published_immediately():
    """Guards the one failure the user cannot undo, and pins the *default*.

    Publishing a post scheduled for tomorrow *now* is not a partial success -
    the audience has already seen it by the time anyone notices. Whether a
    scheduled time is legal is a platform question (Douyin: 2h..14d), so it is
    answered by `PlatformIntentRules`. What this test fixes is what happens when
    a platform supplies none: refusal. The tempting alternative reading - "no
    rules, so nothing to check" - publishes immediately, which is precisely the
    unrecoverable outcome.
    """
    problem = validate_intent(
        intent(scheduled_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    )
    assert problem is not None
    assert problem.reason == "scheduling_not_supported"


def test_a_platform_that_cannot_schedule_refuses_even_with_rules_registered():
    """Registering rules is not the same as supporting scheduling. A platform
    whose rules exist for its other options must still refuse a scheduled post
    rather than have `supports_scheduling` default in its favour."""
    from app.publish import PlatformIntentRules

    rules = PlatformIntentRules(supports_scheduling=False, check=lambda _i, _n: None)
    problem = validate_intent(
        intent(scheduled_at=datetime(2030, 1, 1, tzinfo=timezone.utc)), rules
    )
    assert problem is not None
    assert problem.reason == "scheduling_not_supported"


def test_platform_rules_can_refuse_an_intent_the_neutral_gate_accepts():
    """The extension point itself: everything channel-neutral is fine, and the
    platform still gets the last word. Without this seam a platform's window or
    vocabulary would have to live in `validate_intent`, which is how the neutral
    layer starts accumulating one platform's knowledge."""
    from app.publish import IntentProblem, PlatformIntentRules

    rules = PlatformIntentRules(
        supports_scheduling=True,
        check=lambda _intent, _now: IntentProblem("platform_says_no", "not on my watch"),
    )
    problem = validate_intent(intent(), rules)
    assert problem is not None
    assert problem.reason == "platform_says_no"


def test_a_file_url_is_refused_before_anything_could_read_it():
    """`file://` in a media row would turn a bad backend record into a read of
    the container's own filesystem. Rejected at the pure layer, so no code path
    that opens a URL is ever reached with one."""
    problem = validate_intent(intent(media=[video(url="file:///proc/self/environ")]))
    assert problem is not None
    assert problem.reason == "bad_video_url"


# --- staging plan -----------------------------------------------------------


def test_only_the_video_is_staged_when_no_cover_was_asked_for():
    plan = assets_to_stage(intent())
    assert [role for role, _ in plan] == ["video"]


def test_the_video_is_staged_before_the_cover():
    """Order is the download order, and the video is the one worth failing on
    first: it is orders of magnitude larger, so discovering it is unreachable
    after paying for the cover is the wrong way round."""
    plan = assets_to_stage(intent(cover=cover()))
    assert [role for role, _ in plan] == ["video", "cover"]


def test_media_items_of_other_kinds_are_not_staged():
    extra = MediaItem(kind="thumbnail", url=COVER_URL, filename="thumb.jpg")
    plan = assets_to_stage(intent(media=[video(), extra]))
    assert [role for role, _ in plan] == ["video"]


# --- image posts: the content-type gate ------------------------------------


def test_an_image_post_is_refused_by_a_platform_that_only_declares_video():
    """The capability is a claim about a publisher that exists.

    The neutral layer below understands image posts from this commit onwards -
    that is what makes this test worth writing. Without a per-platform answer,
    teaching `publish.py` about galleries would let *every* registered platform
    accept one, including the ones whose only code path drives a video form
    (spec §1.1 defect 2). T1 replaces the lookup; the answer for a video-only
    platform must not change when it does.
    """
    problem = validate_intent(images_intent(), supported_content_types=("video",))
    assert problem is not None
    assert problem.reason == "unsupported_content_type"


def test_the_default_content_type_answer_still_refuses_image_posts():
    """No caller supplies the platform's list yet, so the module default is what
    production actually runs. It says video, and stays saying video until T7
    flips it in the same PR as the backend profile."""
    problem = validate_intent(images_intent())
    assert problem is not None
    assert problem.reason == "unsupported_content_type"


def test_a_platform_with_no_declared_content_types_publishes_nothing():
    """The post-T1 shape of "platform not in the table". Empty means refuse, so
    an unregistered platform cannot inherit another one's capability."""
    problem = validate_intent(intent(), supported_content_types=())
    assert problem is not None
    assert problem.reason == "unsupported_content_type"
    assert problem.message


# --- image posts: the intent gate ------------------------------------------


def test_an_image_post_is_accepted_once_the_platform_declares_it():
    assert validate_intent(images_intent(), supported_content_types=IMAGES_ENABLED) is None


def test_a_single_image_is_a_legal_gallery():
    assert (
        validate_intent(images_intent(count=1), supported_content_types=IMAGES_ENABLED)
        is None
    )


@pytest.mark.parametrize("filename", ["s.jpg", "s.JPEG", "s.png", "s.webp", "s.bmp"])
def test_every_whitelisted_image_extension_is_accepted(filename):
    problem = validate_intent(
        images_intent(media=[image(filename=filename)]),
        supported_content_types=IMAGES_ENABLED,
    )
    assert problem is None


def test_an_image_post_with_a_cover_is_refused():
    """Spec D4. Douyin's video cover is a fifth file uploaded into a dialog;
    an image post's cover is its first image, and the frontend already says so.
    Accepting a `cover` here would stage a file no publisher can place, so the
    request is wrong at the shape level and is told so rather than half-honoured.
    """
    problem = validate_intent(
        images_intent(cover=cover()), supported_content_types=IMAGES_ENABLED
    )
    assert problem is not None
    assert problem.reason == "cover_not_supported_for_images"


def test_a_video_cover_is_still_accepted():
    """The other side of the same coin: the D4 refusal is scoped to image
    posts, and must not have taken video covers down with it."""
    assert validate_intent(intent(cover=cover())) is None


@pytest.mark.parametrize(
    "bad, expected_reason",
    [
        (dict(media=[]), "too_few_images"),
        (dict(media=[image(0), video()]), "unexpected_media_kind"),
        (dict(media=[image(0), cover()]), "unexpected_media_kind"),
        (dict(title=""), "empty_title"),
        (dict(title="   \n "), "empty_title"),
        (dict(media=[image(0, filename="slide.mp4")]), "unsupported_image_type"),
        (dict(media=[image(0, filename="slide")]), "unsupported_image_type"),
        (dict(media=[image(0, url="file:///etc/passwd")]), "bad_image_url"),
        (dict(media=[image(0, url="ftp://host/slide.jpg")]), "bad_image_url"),
    ],
)
def test_bad_image_intents_are_refused_with_a_stable_reason_code(bad, expected_reason):
    problem = validate_intent(
        images_intent(**bad), supported_content_types=IMAGES_ENABLED
    )
    assert problem is not None
    assert problem.reason == expected_reason
    assert problem.message


def test_a_bad_image_says_which_one():
    """"One of your fifteen images has the wrong extension" is a request the
    caller has to bisect by hand. R2 in the spec asks the upload path to name
    the failing image; the gate that runs before it can do the same for free."""
    problem = validate_intent(
        images_intent(media=[image(0), image(1), image(2, filename="slide.gif")]),
        supported_content_types=IMAGES_ENABLED,
    )
    assert problem is not None
    assert problem.reason == "unsupported_image_type"
    assert problem.message.startswith("image 2:")


def test_the_image_count_ceiling_is_a_parameter_not_a_literal():
    """The real ceiling is unmeasured (`[TO-VERIFY]` V2) and lands on the
    platform profile in T4. The browser gate is the backstop, so it has to take
    the number from its caller rather than pin one of its own."""
    two = ImageBounds(minimum=1, maximum=2)
    assert (
        validate_intent(
            images_intent(count=2), supported_content_types=IMAGES_ENABLED, image_bounds=two
        )
        is None
    )
    problem = validate_intent(
        images_intent(count=3), supported_content_types=IMAGES_ENABLED, image_bounds=two
    )
    assert problem is not None
    assert problem.reason == "too_many_images"


def test_the_image_count_floor_is_a_parameter_too():
    floor = ImageBounds(minimum=2, maximum=9)
    problem = validate_intent(
        images_intent(count=1), supported_content_types=IMAGES_ENABLED, image_bounds=floor
    )
    assert problem is not None
    assert problem.reason == "too_few_images"


def test_the_default_ceiling_refuses_an_unbounded_gallery():
    problem = validate_intent(
        images_intent(count=36), supported_content_types=IMAGES_ENABLED
    )
    assert problem is not None
    assert problem.reason == "too_many_images"


def test_a_video_intent_is_unaffected_by_image_bounds():
    assert (
        validate_intent(intent(), image_bounds=ImageBounds(minimum=5, maximum=5)) is None
    )


# --- image posts: the staging plan ------------------------------------------


def test_every_image_is_staged_under_its_own_indexed_role():
    plan = assets_to_stage(images_intent(count=3))
    assert [role for role, _ in plan] == ["image:0", "image:1", "image:2"]
    assert [item.filename for _, item in plan] == [
        "slide-0.jpg",
        "slide-1.jpg",
        "slide-2.jpg",
    ]


def test_no_cover_is_staged_for_an_image_post():
    """`validate_intent` already refuses a cover on an image post, but the two
    are separate functions and T3 calls both. Staging a cover here would put a
    file in the scratch directory that the gate says cannot exist."""
    plan = assets_to_stage(images_intent(cover=cover()))
    assert [role for role, _ in plan] == ["image:0", "image:1", "image:2"]


def test_the_role_format_has_a_single_definition():
    assert [image_role(i) for i in (0, 1, 10)] == ["image:0", "image:1", "image:10"]


# --- image posts: reading the order back ------------------------------------


def test_staged_images_come_back_in_index_order_not_mapping_order():
    """The whole point of D2. Insertion order happens to work in CPython, which
    is exactly why it is dangerous: one `dict(...)` rebuild, filter or merge
    upstream and the gallery goes out in an order the user never composed, with
    nothing failing."""
    shuffled = {
        "image:2": staged("image:2"),
        "image:0": staged("image:0"),
        "image:1": staged("image:1"),
    }
    assert [asset.role for asset in ordered_image_assets(shuffled)] == [
        "image:0",
        "image:1",
        "image:2",
    ]


def test_a_single_staged_image_is_returned():
    assert [a.role for a in ordered_image_assets({"image:0": staged("image:0")})] == [
        "image:0"
    ]


def test_double_digit_positions_sort_numerically_not_lexically():
    """`sorted()` on the raw keys would put `image:10` before `image:2`."""
    assets = {image_role(i): staged(image_role(i)) for i in range(12)}
    assert [a.role for a in ordered_image_assets(assets)] == [image_role(i) for i in range(12)]


def test_a_gap_in_the_positions_raises_instead_of_publishing_what_is_left():
    """The reverse-verification the spec asks for by name.

    Returning the two images that *are* there sends a real post to a real
    audience in an order nobody chose, and reports success. There is no safe
    recovery from an assembly bug at this point, so there is no recovery.
    """
    with pytest.raises(AssetError) as caught:
        ordered_image_assets({"image:0": staged("image:0"), "image:2": staged("image:2")})
    assert caught.value.detail["reason"] == "image_role_gap"
    assert caught.value.status is SessionStatus.FAILED


def test_a_zero_padded_position_is_refused_rather_than_aliased():
    """`image:01` and `image:1` are different keys that mean the same position.
    Refusing the non-canonical spelling is what keeps two entries from claiming
    one slot in the first place."""
    with pytest.raises(AssetError) as caught:
        ordered_image_assets({"image:0": staged("image:0"), "image:01": staged("image:01")})
    assert caught.value.detail["reason"] == "image_role_malformed"


@pytest.mark.parametrize("role", ["image:", "image:x", "image:-1", "image:1.0", "image:١"])
def test_a_role_that_is_not_a_decimal_index_raises(role):
    with pytest.raises(AssetError) as caught:
        ordered_image_assets({role: staged(role)})
    assert caught.value.detail["reason"] == "image_role_malformed"


def test_two_assets_claiming_one_position_raise():
    """`ordered_image_assets` reads `.items()` off a `Mapping`, and a `Mapping`
    is an interface, not necessarily a `dict` literal. The guard is what makes
    "one position, one image" a property of this function rather than a
    property of whatever the caller happened to build it from."""

    class DoubleBooked(dict):
        def items(self):
            return [("image:0", staged("image:0")), ("image:0", staged("image:0"))]

    with pytest.raises(AssetError) as caught:
        ordered_image_assets(DoubleBooked())
    assert caught.value.detail["reason"] == "image_role_duplicate"


def test_staging_no_images_at_all_raises():
    with pytest.raises(AssetError) as caught:
        ordered_image_assets({})
    assert caught.value.detail["reason"] == "image_roles_missing"


def test_non_image_roles_are_ignored_but_do_not_count_as_images():
    """A video publish's assets must not accidentally satisfy this reader."""
    with pytest.raises(AssetError) as caught:
        ordered_image_assets({"video": staged("video"), "cover": staged("cover")})
    assert caught.value.detail["reason"] == "image_roles_missing"


def test_an_unrelated_role_alongside_images_is_skipped():
    assets = {
        "cover": staged("cover"),
        "image:1": staged("image:1"),
        "image:0": staged("image:0"),
    }
    assert [a.role for a in ordered_image_assets(assets)] == ["image:0", "image:1"]


# --- deadline ---------------------------------------------------------------


class FakeClock:
    """Stands in for the `time` module inside `publish`."""

    def __init__(self, now: float = 1_000.0):
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr("app.publish.time", fake)
    return fake


def test_a_fresh_deadline_offers_its_whole_budget(clock):
    deadline = Deadline(600)
    assert deadline.remaining() == 600
    assert deadline.expired() is False


def test_a_stage_ceiling_below_the_remaining_budget_is_used_as_is(clock):
    deadline = Deadline(600)
    assert deadline.slice_ms(10_000) == 10_000


def test_a_stage_cannot_ask_for_more_time_than_the_publish_has_left(clock):
    """The reason the budget is shared rather than per-stage.

    Douyin's form wait is minutes by necessity (the editor renders only after
    the transfer finishes). If a late stage could still claim its full ceiling,
    a publish whose upload ate the budget would run for the sum of every stage
    ceiling instead of the total the endpoint promised its caller.
    """
    deadline = Deadline(600)
    clock.advance(598)
    assert deadline.slice_ms(120_000) == 2_000


def test_an_exhausted_deadline_hands_out_zero_rather_than_a_negative_timeout(clock):
    """A negative `timeout=` is not "no time left" to Playwright - depending on
    the call it means "wait forever", which turns the end of the budget into an
    unbounded wait (spec 7.2, inverted)."""
    deadline = Deadline(600)
    clock.advance(601)
    assert deadline.remaining() == 0.0
    assert deadline.expired() is True
    assert deadline.slice_ms(30_000) == 0


def test_a_deadline_overrun_by_a_long_way_still_reports_zero(clock):
    deadline = Deadline(600)
    clock.advance(10_000)
    assert deadline.slice_ms(30_000) == 0


def test_expiry_is_inclusive_at_the_boundary(clock):
    deadline = Deadline(600)
    clock.advance(600)
    assert deadline.expired() is True


# --- outcome ----------------------------------------------------------------


def test_only_published_counts_as_success():
    """`success` is what the backend branches on; deriving it from anything but
    the enum member would let a new status default to "it worked"."""
    for status in SessionStatus:
        outcome = PublishOutcome(status=status, message="x")
        assert outcome.success is (status is SessionStatus.PUBLISHED)


def test_a_job_carries_no_wire_types_into_a_platform():
    """`PublishJob` is the boundary: a publisher receives staged files, never a
    URL to fetch, so no platform module can grow its own download path."""
    job = PublishJob(
        platform="douyin",
        storage_state={"cookies": []},
        environment=None,
        intent=intent(),
        assets={},
    )
    assert job.assets == {}
    with pytest.raises(Exception):
        job.platform = "kuaishou"  # frozen
