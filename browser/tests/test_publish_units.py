"""Everything `run_publish` decides before a browser exists.

`validate_intent` is the spec 7.7 fail-fast gate: whatever it fails to catch is
paid for with a browser launch and a few hundred megabytes of upload before the
same request dies anyway. `Deadline` is the spec 7.2 upper bound - the thing
that keeps a design of bounded stages from adding up to an unbounded whole.
Both are pure, so both get tables.
"""

from datetime import datetime, timezone

import pytest

from app.publish import (
    Deadline,
    PublishJob,
    PublishOutcome,
    assets_to_stage,
    validate_intent,
)
from app.schemas import MediaItem, PublishIntent, SessionStatus

pytestmark = pytest.mark.unit

VIDEO_URL = "https://nous-backend:8080/signed/clip.mp4"
COVER_URL = "https://nous-backend:8080/signed/cover.jpg"


def video(**overrides) -> MediaItem:
    fields = {"kind": "video", "url": VIDEO_URL, "filename": "clip.mp4"}
    fields.update(overrides)
    return MediaItem(**fields)


def cover(**overrides) -> MediaItem:
    fields = {"kind": "cover", "url": COVER_URL, "filename": "cover.jpg"}
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
