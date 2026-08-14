"""The three publish-form fields added on top of title/description/cover.

They are grouped in one file because what makes them interesting is how they
*differ*, and that comparison is invisible when each lives next to its own
step:

    self declaration  →  every failure fails the publish
    scheduled time    →  every failure fails the publish
    collection        →  every failure is reported and skipped

The rule behind the split is the cost of the cheap mistake. An undeclared
AI-generated video and a post that goes out twelve hours early are both live,
visible and unrecallable; a post filed in no collection is a minute of manual
filing. So the first two refuse to guess and the third degrades - and the tests
that pin *that* are the ones worth having, more than any selector.

Everything below the pure section drives `tests.fakes.FakePage`, so the loops,
the deadline slices and the refusals are exercised without a Chromium or an
account.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.config import get_settings
from app.platforms import douyin_publish as dp
from app.publish import Deadline, PublishJob, validate_intent
from app.schemas import EnvironmentConfig, MediaItem, PublishIntent, SessionStatus
from tests.fakes import FakePage

pytestmark = pytest.mark.unit

EDITOR_URL = "https://creator.douyin.com/creator-micro/content/post/video"
MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setenv("BROWSER_PUBLISH_POLL_INTERVAL_S", "0.2")
    monkeypatch.setenv("BROWSER_PUBLISH_CONFIRM_WAIT_S", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def job(*, environment: EnvironmentConfig | None = None, **overrides) -> PublishJob:
    from app.assets import StagedAsset

    intent = PublishIntent(
        content_type="video",
        media=[
            MediaItem(
                kind="video",
                url="https://nous-backend:8080/signed/clip.mp4",
                filename="clip.mp4",
            )
        ],
        title="Launch Day Recap",
        **overrides,
    )
    return PublishJob(
        platform="douyin",
        storage_state={"cookies": []},
        environment=environment,
        intent=intent,
        assets={
            "video": StagedAsset(
                role="video", path="/tmp/scratch/clip.mp4", filename="clip.mp4", size_bytes=99
            )
        },
    )


# =============================================================================
# pure: self declaration
# =============================================================================


@pytest.mark.parametrize("option", dp.SELF_DECLARATION_OPTIONS)
def test_every_declaration_the_platform_offers_can_be_selected(option):
    """The table that matters: each of the platform's own six strings, in, and
    the same string back out.

    Written against the whole tuple rather than six literals so that adding a
    seventh option to the module without teaching the matcher about it fails
    here rather than in production.
    """
    choice = dp.judge_self_declaration(option, dp.SELF_DECLARATION_OPTIONS)
    assert choice.option == option


def test_the_platform_offers_exactly_the_six_declarations_read_off_the_page():
    """A count, not a spelling check.

    These were read off a live publish page; the risk is not that one is
    mistyped - it is that someone adds a plausible-looking seventh from memory,
    or drops one during a merge, and the dialog then contains an option this
    code silently cannot select.
    """
    assert len(dp.SELF_DECLARATION_OPTIONS) == 6
    assert len(set(dp.SELF_DECLARATION_OPTIONS)) == 6
    assert "内容由AI生成" in dp.SELF_DECLARATION_OPTIONS
    assert "无需添加自主声明" in dp.SELF_DECLARATION_OPTIONS


def test_the_clicked_string_is_the_dialogs_own_copy_not_the_callers():
    """The caller's spelling is a lookup key; the click targets what the dialog
    actually rendered. If the platform ever adds a trailing space or a variant
    character to a label, matching on the caller's string would stop finding a
    row that is right there on screen."""
    on_screen = ["内容由 AI 生成"]
    choice = dp.judge_self_declaration("内容由AI生成", on_screen)
    assert choice.option == "内容由 AI 生成"


def test_a_half_width_comma_still_selects_the_right_declaration():
    """`虚构演绎,仅供娱乐` is what someone types without switching input mode.
    Refusing it would be a punctuation-shaped compliance failure - the user
    said what they meant, and the only disagreement is about a comma."""
    choice = dp.judge_self_declaration("虚构演绎,仅供娱乐", dp.SELF_DECLARATION_OPTIONS)
    assert choice.option == "虚构演绎，仅供娱乐"


def test_a_declaration_that_is_not_one_of_the_six_selects_nothing():
    """No nearest-match. A fuzzy matcher that answered "AI generated" with
    内容含营销推广信息 would declare something the user never said, and the post
    carries that statement publicly."""
    choice = dp.judge_self_declaration("AI generated", dp.SELF_DECLARATION_OPTIONS)
    assert choice.option is None
    assert "not one of the platform's declarations" in choice.reason


def test_a_valid_declaration_missing_from_the_dialog_selects_nothing():
    """Distinct from an unknown value: the request was legal and the dialog
    changed under us. Collapsing the two would hide a platform redesign behind
    what reads like a caller's typo."""
    choice = dp.judge_self_declaration("内容由AI生成", ["无需添加自主声明"])
    assert choice.option is None
    assert "on screen" in choice.reason


@pytest.mark.parametrize("requested", [None, "", "   "])
def test_no_declaration_requested_selects_nothing_without_complaint(requested):
    choice = dp.judge_self_declaration(requested, dp.SELF_DECLARATION_OPTIONS)
    assert choice.option is None
    assert "no declaration requested" in choice.reason


# =============================================================================
# pure: platform_options parsing
# =============================================================================


def test_an_absent_key_and_an_explicit_no_declaration_are_different_things():
    """The distinction the whole step rests on.

    `无需添加自主声明` is a declaration the user chose and the platform records.
    An absent key means nobody touches the control. Treating the first as the
    second would silently downgrade an explicit statement into a default; the
    reverse would make every ordinary publish assert something about its
    content.
    """
    assert dp.read_platform_options({}).self_declaration is None
    assert dp.read_platform_options({"self_declaration": None}).self_declaration is None
    assert (
        dp.read_platform_options({"self_declaration": "无需添加自主声明"}).self_declaration
        == "无需添加自主声明"
    )


@pytest.mark.parametrize("raw", [None, {}, {"other": "x"}])
def test_options_absent_altogether_parse_to_nothing_requested(raw):
    options = dp.read_platform_options(raw)
    assert (options.self_declaration, options.collection, options.bad_types) == (None, None, ())


def test_an_empty_string_is_read_as_nothing_requested():
    options = dp.read_platform_options({"self_declaration": "  ", "collection": ""})
    assert options.self_declaration is None
    assert options.collection is None


def test_a_non_string_option_is_kept_as_a_complaint_rather_than_dropped():
    """`{"self_declaration": true}` is a caller bug. Answering it with "no
    declaration requested" hides that bug behind a post that went out
    undeclared, which is the failure the whole field exists to prevent."""
    options = dp.read_platform_options({"self_declaration": True, "collection": 7})
    assert options.bad_types == ("self_declaration", "collection")


# =============================================================================
# pure: the scheduling window
# =============================================================================


def test_no_scheduled_time_is_an_immediate_publish_not_a_window_violation():
    assert dp.judge_schedule_window(None, NOW).ok is True


def test_a_naive_scheduled_time_is_refused_rather_than_assumed_to_be_utc():
    """The most expensive assumption available here. Reading a wall-clock time
    in the wrong zone is an eight-hour error in a field nobody re-checks, and
    the post is out before anyone notices. Refusing costs one clear error."""
    verdict = dp.judge_schedule_window(datetime(2026, 8, 8, 12, 0), NOW)
    assert verdict.ok is False
    assert verdict.reason == "schedule_naive_datetime"


@pytest.mark.parametrize(
    "lead, ok, reason",
    [
        (timedelta(minutes=30), False, "schedule_too_soon"),
        (timedelta(hours=1, minutes=59), False, "schedule_too_soon"),
        # The platform's own floor, exactly. Refused here on purpose - see the
        # slack test below.
        (dp.SCHEDULE_MIN_LEAD, False, "schedule_too_soon"),
        (dp.SCHEDULE_MIN_LEAD + dp.SCHEDULE_LEAD_SLACK, True, "scheduled"),
        (timedelta(days=3), True, "scheduled"),
        (dp.SCHEDULE_MAX_LEAD, True, "scheduled"),
        (dp.SCHEDULE_MAX_LEAD + timedelta(minutes=1), False, "schedule_too_far"),
        (timedelta(days=30), False, "schedule_too_far"),
        (timedelta(hours=-1), False, "schedule_too_soon"),
    ],
)
def test_the_scheduling_window_is_checked_at_its_boundaries(lead, ok, reason):
    verdict = dp.judge_schedule_window(NOW + lead, NOW)
    assert (verdict.ok, verdict.reason) == (ok, reason)


def test_the_floor_carries_slack_because_the_check_runs_before_the_upload():
    """Why the effective minimum is not the platform's 2 hours.

    This check happens before a browser exists (spec 7.7); the upload that
    follows is minutes. A request at exactly 2h00m would pass here and then be
    rejected by the platform *after* several hundred megabytes had transferred -
    the most expensive way to discover a boundary. The ceiling needs no
    equivalent slack: time passing moves the target closer, so 14d only gets
    safer while the upload runs.
    """
    assert dp.SCHEDULE_MIN_LEAD == timedelta(hours=2)
    assert dp.SCHEDULE_MAX_LEAD == timedelta(days=14)
    assert dp.SCHEDULE_LEAD_SLACK > timedelta(0)

    verdict = dp.judge_schedule_window(NOW + dp.SCHEDULE_MIN_LEAD, NOW)
    assert verdict.ok is False
    assert "2h" in verdict.message  # the platform's floor is quoted, not hidden


def test_a_window_message_says_how_far_off_the_request_actually_was():
    """A refusal the caller can act on. "too far ahead" without a number leaves
    the user guessing at a value the UI could have clamped."""
    verdict = dp.judge_schedule_window(NOW + timedelta(days=30), NOW)
    assert "14d" in verdict.message
    assert "30d" in verdict.message


# =============================================================================
# pure: formatting the time the picker reads
# =============================================================================


def test_the_time_is_typed_in_the_browser_contexts_own_timezone():
    """The picker shows wall-clock time in the context's zone. 04:00Z is noon in
    Shanghai, and typing "04:00" there schedules a post for the middle of the
    night - a value nobody chose and cannot recall."""
    moment = datetime(2026, 8, 8, 4, 0, tzinfo=timezone.utc)
    assert dp.format_schedule_input(moment, "Asia/Shanghai") == "2026-08-08 12:00"


def test_a_time_already_in_the_platforms_zone_is_not_shifted_again():
    from zoneinfo import ZoneInfo

    moment = datetime(2026, 8, 8, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert dp.format_schedule_input(moment, "Asia/Shanghai") == "2026-08-08 12:00"


def test_a_missing_timezone_falls_back_to_the_platforms_own():
    """`EnvironmentConfig.timezone_id` is optional, and the browser context
    falls back the same way. Two different fallbacks would schedule the post
    for one time and display it as another."""
    moment = datetime(2026, 8, 8, 4, 0, tzinfo=timezone.utc)
    assert dp.format_schedule_input(moment, None) == "2026-08-08 12:00"
    assert dp.PLATFORM_TIMEZONE == "Asia/Shanghai"


def test_an_unusable_timezone_id_falls_back_instead_of_crashing():
    """A typo in per-account configuration must not become an exception three
    minutes into a publish, when the video has already been uploaded."""
    moment = datetime(2026, 8, 8, 4, 0, tzinfo=timezone.utc)
    assert dp.format_schedule_input(moment, "Mars/Olympus_Mons") == "2026-08-08 12:00"


# =============================================================================
# pure: the platform half of the spec 7.7 gate
# =============================================================================


def test_douyin_declares_itself_able_to_schedule():
    assert dp.DOUYIN_INTENT_RULES.supports_scheduling is True


def test_an_unknown_declaration_is_refused_before_a_browser_starts():
    """Fail fast (spec 7.7). The alternative is discovering it after the upload,
    inside a dialog, where the only remaining answer is to abandon the run."""
    problem = dp.check_intent(
        job(platform_options={"self_declaration": "AI generated"}).intent, NOW
    )
    assert problem is not None
    assert problem.reason == "unknown_self_declaration"
    # The message lists the legal values, so the caller can fix it in one pass.
    assert "内容由AI生成" in problem.message


def test_a_non_string_option_is_refused_before_a_browser_starts():
    problem = dp.check_intent(job(platform_options={"collection": 7}).intent, NOW)
    assert problem is not None
    assert problem.reason == "bad_platform_option"


def test_an_out_of_window_schedule_is_refused_before_a_browser_starts():
    problem = dp.check_intent(
        job(scheduled_at=NOW + timedelta(days=30)).intent, NOW
    )
    assert problem is not None
    assert problem.reason == "schedule_too_far"


def test_an_ordinary_intent_passes_the_platform_gate_untouched():
    assert dp.check_intent(job().intent, NOW) is None


def test_a_legal_declaration_and_collection_pass_the_gate():
    intent = job(
        platform_options={"self_declaration": "内容由AI生成", "collection": "Weekly Recap"},
        scheduled_at=NOW + timedelta(days=1),
    ).intent
    assert dp.check_intent(intent, NOW) is None


def test_the_neutral_gate_runs_the_platform_rules_but_only_after_its_own():
    """Ordering, not decoration. "there is no video in this request" is a more
    useful answer than "your scheduled time is 40 minutes too soon" when both
    are true - the second one is noise until the first is fixed."""
    intent = PublishIntent(
        content_type="video",
        media=[],
        title="",
        scheduled_at=NOW + timedelta(minutes=30),
    )
    problem = validate_intent(intent, dp.DOUYIN_INTENT_RULES, NOW)
    assert problem is not None
    assert problem.reason == "missing_video"


def test_a_platform_that_can_schedule_is_allowed_to(monkeypatch):
    intent = job(scheduled_at=NOW + timedelta(days=1)).intent
    assert validate_intent(intent, dp.DOUYIN_INTENT_RULES, NOW) is None


def test_scheduling_without_registered_rules_is_refused_not_published_now():
    """The fail-closed default. A publisher that forgets to register rules must
    degrade into refusing scheduled posts, never into publishing them
    immediately - the second failure is the one the audience sees."""
    intent = job(scheduled_at=NOW + timedelta(days=1)).intent
    problem = validate_intent(intent, None, NOW)
    assert problem is not None
    assert problem.reason == "scheduling_not_supported"


def test_the_registry_hands_back_douyins_rules():
    """`run_publish` looks them up by platform name; a rules object that exists
    but is not reachable from the registry gates nothing."""
    from app.platforms import get_intent_rules

    assert get_intent_rules("douyin") is dp.DOUYIN_INTENT_RULES
    assert get_intent_rules("DOUYIN ") is dp.DOUYIN_INTENT_RULES
    assert get_intent_rules("myspace") is None


# =============================================================================
# driver: the self-declaration dialog
# =============================================================================

DECLARATION_ENTRY = f"text={dp.SELF_DECLARATION_ENTRY_TEXT}"
DECLARATION_CONFIRM = f"text={dp.SELF_DECLARATION_CONFIRM_TEXT}"


def declaration_page(*, options=dp.SELF_DECLARATION_OPTIONS, closes: bool = True) -> FakePage:
    """A page whose declaration dialog behaves. `closes` is the honest half:
    the dialog disappears once 确定 is clicked, which is the only evidence the
    choice registered."""
    base = {DECLARATION_ENTRY, DECLARATION_CONFIRM, dp.SEMI_RADIO_SELECTOR}
    base |= {f"text={option}" for option in options}

    def visible(page: FakePage) -> set[str]:
        confirmed = closes and DECLARATION_CONFIRM in page.clicks
        return base if confirmed else base | {dp.SELF_DECLARATION_MODAL_SELECTOR}

    return FakePage(url=EDITOR_URL, visible=visible)


async def test_no_declaration_requested_leaves_the_control_untouched():
    """Why refusing on a missing control is affordable: an ordinary publish does
    not open this dialog at all, so it never depends on these selectors."""
    page = FakePage(url=EDITOR_URL, visible=())
    assert await dp._set_self_declaration(page, job(), Deadline(5)) == {
        "self_declaration": "not_requested"
    }
    assert page.clicks == []


async def test_an_explicit_no_declaration_is_a_real_click():
    """`无需添加自主声明` is not "leave it alone". The user chose it and the
    platform records it, so the dialog has to be opened and the row clicked."""
    page = declaration_page()
    result = await dp._set_self_declaration(
        page, job(platform_options={"self_declaration": "无需添加自主声明"}), Deadline(10)
    )

    assert result["self_declaration"] == "applied"
    assert result["self_declaration_value"] == "无需添加自主声明"
    assert DECLARATION_ENTRY in page.clicks
    assert DECLARATION_CONFIRM in page.clicks


@pytest.mark.parametrize("option", dp.SELF_DECLARATION_OPTIONS)
async def test_each_declaration_makes_it_through_the_dialog(option):
    page = declaration_page()
    result = await dp._set_self_declaration(
        page, job(platform_options={"self_declaration": option}), Deadline(10)
    )
    assert result == {"self_declaration": "applied", "self_declaration_value": option}


async def test_a_missing_declaration_entry_point_fails_the_publish():
    page = FakePage(url=EDITOR_URL, visible=())
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_self_declaration(
            page, job(platform_options={"self_declaration": "内容由AI生成"}), Deadline(5)
        )

    assert excinfo.value.status is SessionStatus.FAILED
    assert excinfo.value.detail["reason"] == "self_declaration_entry_missing"
    assert excinfo.value.detail["requested_self_declaration"] == "内容由AI生成"


async def test_a_dialog_that_never_opens_fails_the_publish():
    page = FakePage(url=EDITOR_URL, visible={DECLARATION_ENTRY})
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_self_declaration(
            page, job(platform_options={"self_declaration": "内容由AI生成"}), Deadline(5)
        )
    assert excinfo.value.detail["reason"] == "self_declaration_dialog_missing"


async def test_a_declaration_the_dialog_no_longer_offers_fails_with_what_it_did_offer():
    """The redesign case. Reporting which options *were* on screen is what turns
    "the publish failed" into a five-minute fix rather than a bisect through a
    live account."""
    page = declaration_page(options=("无需添加自主声明", "内容为转载信息"))
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_self_declaration(
            page, job(platform_options={"self_declaration": "内容由AI生成"}), Deadline(5)
        )

    assert excinfo.value.detail["reason"] == "self_declaration_option_missing"
    assert excinfo.value.detail["options_on_screen"] == [
        "内容为转载信息",
        "无需添加自主声明",
    ]


async def test_a_dialog_still_on_screen_after_confirming_fails_the_publish():
    """The subtle one. The click landed, the driver could report success, and
    the post would go out undeclared while `detail` claimed otherwise. The
    dialog closing is the only evidence the platform accepted the choice.
    """
    page = declaration_page(closes=False)
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_self_declaration(
            page, job(platform_options={"self_declaration": "内容由AI生成"}), Deadline(5)
        )

    assert excinfo.value.detail["reason"] == "self_declaration_dialog_stuck"


async def test_a_declaration_that_will_not_confirm_fails_the_publish():
    page = declaration_page()
    page.counts[DECLARATION_CONFIRM] = 0
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_self_declaration(
            page, job(platform_options={"self_declaration": "内容由AI生成"}), Deadline(5)
        )
    assert excinfo.value.detail["reason"] == "self_declaration_confirm_missing"


# =============================================================================
# driver: the collection dropdown
# =============================================================================

COLLECTION_ENTRY = f"text={dp.COLLECTION_ENTRY_TEXT}"


async def test_no_collection_requested_touches_nothing():
    page = FakePage(url=EDITOR_URL, visible=())
    assert await dp._set_collection(page, job(), Deadline(5)) == {"collection": "not_requested"}
    assert page.clicks == []


async def test_a_collection_that_exists_is_selected():
    page = FakePage(
        url=EDITOR_URL,
        visible={COLLECTION_ENTRY, dp.COLLECTION_OPTION_SELECTOR},
    )
    result = await dp._set_collection(
        page, job(platform_options={"collection": "Weekly Recap"}), Deadline(10)
    )

    assert result == {"collection": "applied", "collection_requested": "Weekly Recap"}
    assert COLLECTION_ENTRY in page.clicks


async def test_a_collection_that_cannot_be_found_is_skipped_and_reported():
    """The asymmetry with the declaration step, stated as a test.

    A published post filed in no collection can be added to one from the
    platform's post list afterwards; failing here would instead discard a
    finished upload and leave a draft to clean up. The skip is *reported* -
    `detail` says `not_found` and carries the name - so the caller can surface
    "published, but the collection was not found" rather than plain success.
    """
    page = FakePage(url=EDITOR_URL, visible={COLLECTION_ENTRY})
    result = await dp._set_collection(
        page, job(platform_options={"collection": "Nonexistent"}), Deadline(5)
    )

    assert result == {"collection": "not_found", "collection_requested": "Nonexistent"}


async def test_the_collection_name_is_matched_anchored_not_as_a_substring():
    """A `has_text` substring would also match "Weekly Recap 2026".

    Filing a post under the wrong collection is worse than filing it under
    none: the skip is reported and visible, while a wrong match looks exactly
    like a right one and nobody re-checks it.
    """
    import re

    calls: list[Any] = []

    class Recording(FakePage):
        def locator(self, selector: str):
            located = super().locator(selector)
            original = located.filter

            def filter(**kwargs):
                calls.append(kwargs.get("has_text"))
                return original(**kwargs)

            located.filter = filter
            return located

    page = Recording(url=EDITOR_URL, visible={COLLECTION_ENTRY, dp.COLLECTION_OPTION_SELECTOR})
    await dp._set_collection(
        page, job(platform_options={"collection": "Weekly Recap"}), Deadline(10)
    )

    pattern = next(c for c in calls if isinstance(c, re.Pattern))
    assert pattern.match("Weekly Recap")
    assert pattern.match("  Weekly Recap  ")  # the option's own padding
    assert not pattern.match("Weekly Recap 2026")


async def test_a_missing_collection_control_is_skipped_and_reported():
    page = FakePage(url=EDITOR_URL, visible=())
    result = await dp._set_collection(
        page, job(platform_options={"collection": "Weekly Recap"}), Deadline(5)
    )
    assert result == {"collection": "control_missing", "collection_requested": "Weekly Recap"}


async def test_the_collection_step_never_raises_whatever_the_page_does():
    """Belt and braces on the degradation rule: an unexpected exception from a
    locator must not turn filing into a failed publish either."""

    class Exploding(FakePage):
        def get_by_text(self, text: str, exact: bool = False):
            raise RuntimeError("Target page, context or browser has been closed")

    result = await dp._set_collection(
        Exploding(url=EDITOR_URL, visible={COLLECTION_ENTRY}),
        job(platform_options={"collection": "Weekly Recap"}),
        Deadline(5),
    )
    assert result["collection"] == "error"


# =============================================================================
# driver: the scheduled-publish controls
# =============================================================================

SCHEDULE_RADIO = f"text={dp.SCHEDULE_RADIO_TEXT}"


async def test_an_immediate_publish_never_touches_the_schedule_controls():
    page = FakePage(url=EDITOR_URL, visible=())
    assert await dp._set_schedule(page, job(), Deadline(5)) == {"schedule": "immediate"}
    assert page.clicks == []


async def test_a_scheduled_publish_switches_the_radio_and_types_the_time():
    page = FakePage(
        url=EDITOR_URL,
        visible={dp.SEMI_RADIO_SELECTOR, dp.SCHEDULE_INPUT_SELECTORS[0]},
    )
    moment = datetime(2026, 8, 8, 4, 0, tzinfo=timezone.utc)
    result = await dp._set_schedule(page, job(scheduled_at=moment), Deadline(10))

    assert result["schedule"] == "scheduled"
    assert result["scheduled_input"] == "2026-08-08 12:00"
    assert "2026-08-08 12:00" in page.keyboard.typed
    # Select-all before typing: the picker pre-seeds itself with "now plus two
    # hours", so typing alone appends to an existing timestamp.
    assert "Control+KeyA" in page.keyboard.pressed


async def test_the_typed_time_follows_the_accounts_own_timezone():
    """Per-account environments can set a different zone, and the picker reads
    whatever the context runs in. Formatting in a zone the context is not using
    schedules the post for an instant nobody chose."""
    page = FakePage(
        url=EDITOR_URL,
        visible={dp.SEMI_RADIO_SELECTOR, dp.SCHEDULE_INPUT_SELECTORS[0]},
    )
    moment = datetime(2026, 8, 8, 4, 0, tzinfo=timezone.utc)
    result = await dp._set_schedule(
        page,
        job(scheduled_at=moment, environment=EnvironmentConfig(timezone_id="UTC")),
        Deadline(10),
    )
    assert result["scheduled_input"] == "2026-08-08 04:00"
    assert result["scheduled_timezone"] == "UTC"


async def test_a_missing_schedule_radio_fails_rather_than_publishing_now():
    """The failure this step exists for. Falling through to the publish button
    would send a post booked for tomorrow morning out immediately, and the
    audience has seen it by the time anyone notices."""
    page = FakePage(url=EDITOR_URL, visible=())
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_schedule(
            page, job(scheduled_at=NOW + timedelta(days=1)), Deadline(5)
        )

    assert excinfo.value.status is SessionStatus.FAILED
    assert excinfo.value.detail["reason"] == "schedule_control_missing"


async def test_a_missing_time_field_fails_rather_than_leaving_the_default():
    """Switching the radio without filling the field leaves the picker's own
    default - "now plus two hours" - which is a time the user never asked for
    and which no error would ever report."""
    page = FakePage(url=EDITOR_URL, visible={dp.SEMI_RADIO_SELECTOR})
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_schedule(
            page, job(scheduled_at=NOW + timedelta(days=1)), Deadline(5)
        )

    assert excinfo.value.detail["reason"] == "schedule_input_missing"


# =============================================================================
# the whole drive: which failures reach the caller as a failed publish
# =============================================================================


MUSIC_ENTRY = f"text={dp.MUSIC_ENTRY_TEXT}"
MUSIC_SEARCH = dp.MUSIC_SEARCH_INPUT_SELECTORS[0]
MUSIC_ROW_0 = f'[{dp.MUSIC_ROW_ATTRIBUTE}="0"]'


def happy_page(**overrides) -> FakePage:
    """A page on which a full publish succeeds, so a single knocked-out control
    is the only difference between a test that publishes and one that does
    not."""
    declaration = {f"text={option}" for option in dp.SELF_DECLARATION_OPTIONS}
    base = {
        dp.FILE_INPUT_SELECTOR,
        dp.TITLE_INPUT_SELECTOR,
        dp.DESCRIPTION_EDITOR_SELECTOR,
        dp.UPLOAD_DONE_SELECTOR,
        DECLARATION_ENTRY,
        DECLARATION_CONFIRM,
        dp.SEMI_RADIO_SELECTOR,
        dp.SCHEDULE_INPUT_SELECTORS[0],
        f"text={dp.PUBLISH_BUTTON_TEXT}",
    } | declaration
    missing = set(overrides.pop("missing", ()))

    def visible(page: FakePage) -> set[str]:
        state = set(base)
        if DECLARATION_CONFIRM not in page.clicks:
            state.add(dp.SELF_DECLARATION_MODAL_SELECTOR)
        # The music dialog: open once 「选择音乐」 has been clicked, closed again
        # once a result row has. Modelled rather than left permanently on
        # screen, because "the dialog never closed" is a real failure this
        # module refuses to publish through.
        if MUSIC_ENTRY in page.clicks and MUSIC_ROW_0 not in page.clicks:
            state.add(MUSIC_SEARCH)
        return state - missing

    def url(page: FakePage) -> str:
        return MANAGE_URL if f"text={dp.PUBLISH_BUTTON_TEXT}" in page.clicks else EDITOR_URL

    page = FakePage(url=url, visible=visible, **overrides)
    # [实测 2026-08-12, T0] 「选择音乐」 matches twice — the block heading and
    # the button. Encoded here so every drive-level test runs against the real
    # ambiguity rather than a convenient single match.
    # `missing` has to reach this one through `counts`: the entry is found by
    # counting text matches, not by a visibility check.
    page.counts.setdefault(MUSIC_ENTRY, 0 if MUSIC_ENTRY in missing else 2)
    page.music_rows = ("Dream It Possible", "Dream It Possible (Live)")
    # The preview only starts naming the track once its row was clicked.
    page.music_mentions = lambda pg, needle: (
        1 if MUSIC_ROW_0 in pg.clicks and needle == "Dream It Possible" else 0
    )
    return page


async def test_a_publish_carrying_every_new_field_reports_all_three():
    """The positive control for the two tests below: on a page where every
    control is present, all three fields are applied and each says so in
    `detail`."""
    outcome = await dp._drive(
        happy_page(),
        job(
            platform_options={"self_declaration": "内容由AI生成", "collection": "Weekly Recap"},
            scheduled_at=datetime(2026, 8, 8, 4, 0, tzinfo=timezone.utc),
        ),
        Deadline(20),
    )

    assert outcome.status is SessionStatus.PUBLISHED
    assert outcome.detail["self_declaration_value"] == "内容由AI生成"
    assert outcome.detail["schedule"] == "scheduled"
    assert outcome.detail["scheduled_input"] == "2026-08-08 12:00"


async def test_a_self_declaration_that_cannot_be_set_fails_the_whole_publish():
    """**The single most important test in this file.**

    A declaration that could not be set must stop the publish - not warn, not
    degrade, not get filed in `detail` next to a `published` status. A post that
    should have declared itself AI-generated and went out undeclared is a
    compliance exposure that stays live until a human notices, and nothing
    downstream re-reads `detail` looking for one. The upload is already paid for
    at this point, which is exactly the pressure that makes "just publish it
    anyway" tempting.
    """
    page = happy_page(missing={DECLARATION_ENTRY})
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._drive(
            page, job(platform_options={"self_declaration": "内容由AI生成"}), Deadline(20)
        )

    assert excinfo.value.detail["stage"] == "self_declaration"
    # And nothing clicked the publish button on the way out.
    assert f"text={dp.PUBLISH_BUTTON_TEXT}" not in page.clicks
    # The typed outcome the endpoint would return is a failure, not a success
    # with a note attached.
    outcome = dp._outcome_from_exception(excinfo.value)
    assert outcome.success is False
    assert outcome.status is SessionStatus.FAILED


async def test_a_collection_that_cannot_be_set_still_publishes():
    """The mirror image, on the same page shape. Filing is recoverable; the
    post goes out and `detail` records what did not happen."""
    page = happy_page(missing={COLLECTION_ENTRY})
    outcome = await dp._drive(
        page, job(platform_options={"collection": "Weekly Recap"}), Deadline(20)
    )

    assert outcome.status is SessionStatus.PUBLISHED
    assert outcome.detail["collection"] == "control_missing"
    assert outcome.detail["collection_requested"] == "Weekly Recap"


async def test_music_that_cannot_be_selected_fails_the_whole_publish():
    """**The guard for the music field**, and the counterpart of the
    declaration test above.

    A user who typed a track name did it because a post published on 原声
    reaches fewer people - that is the only reason the field exists. Publishing
    anyway would produce a post that looks completely fine and quietly
    under-performs, with nothing anywhere saying the music was dropped, and the
    platform does not let a published post swap its track afterwards.

    Removing this refusal (returning a `music: control_missing` note the way
    the collection step does) turns this test red - which is the point.
    """
    page = happy_page(missing={MUSIC_ENTRY})
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._drive(
            page, job(platform_options={"music": "Dream It Possible"}), Deadline(20)
        )

    assert excinfo.value.detail["stage"] == "music"
    assert excinfo.value.detail["reason"] == "music_entry_missing"
    # Nothing went out: the publish button was never clicked.
    assert f"text={dp.PUBLISH_BUTTON_TEXT}" not in page.clicks
    outcome = dp._outcome_from_exception(excinfo.value)
    assert outcome.success is False


async def test_a_publish_that_asked_for_music_reports_which_track_it_got():
    """The positive control: on a page with the dialog, the drive publishes and
    `detail` names the track and how it was matched."""
    outcome = await dp._drive(
        happy_page(),
        job(platform_options={"music": "dream it possible"}),
        Deadline(20),
    )

    assert outcome.status is SessionStatus.PUBLISHED
    assert outcome.detail["music"] == "applied"
    assert outcome.detail["music_selected"] == "Dream It Possible"
    # Case and whitespace are not a disagreement about which song this is.
    assert outcome.detail["music_match"] == "exact"


async def test_a_publish_without_music_never_touches_the_dialog():
    """Why refusing above is affordable: the ordinary publish does not depend
    on a single music selector."""
    page = happy_page()
    outcome = await dp._drive(page, job(), Deadline(20))

    assert outcome.status is SessionStatus.PUBLISHED
    assert outcome.detail["music"] == "not_requested"
    assert MUSIC_ENTRY not in page.clicks


async def test_a_schedule_that_cannot_be_set_fails_the_whole_publish():
    page = happy_page(missing={dp.SCHEDULE_INPUT_SELECTORS[0]})
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._drive(
            page,
            job(scheduled_at=datetime(2026, 8, 8, 4, 0, tzinfo=timezone.utc)),
            Deadline(20),
        )

    assert excinfo.value.detail["stage"] == "schedule"
    assert f"text={dp.PUBLISH_BUTTON_TEXT}" not in page.clicks
