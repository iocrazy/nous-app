"""The publish steps that read a page and decide what to do next.

`test_douyin_publish_judge.py` covers the pure verdicts; this covers the loops
built on top of them - where the retry budget, the deadline and the "refuse
rather than guess" rules live. They are driven through `tests.fakes.FakePage`,
which answers the same narrow locator surface a real page would, so the loops
can be exercised without a Chromium or an account.

What is deliberately *not* asserted: how many times a helper was called, or in
what order it touched the DOM. Those change whenever a selector moves. What is
asserted is what the caller ends up with - a typed status, a retry count, the
index of the file input that received the cover.
"""

from __future__ import annotations

import time

import pytest

from app.config import get_settings
from app.platforms import douyin_publish as dp
from app.publish import Deadline, PublishJob, PublishOutcome
from app.schemas import MediaItem, PublishIntent, SessionStatus
from app.validation import ProbeKind
from tests.fakes import FakePage

pytestmark = pytest.mark.unit

EDITOR_URL = "https://creator.douyin.com/creator-micro/content/post/video"
UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    """Real intervals, scaled down. The loops are wall-clock driven, so the only
    alternative to shortening them is a test suite that takes minutes."""
    monkeypatch.setenv("BROWSER_PUBLISH_POLL_INTERVAL_S", "0.2")
    monkeypatch.setenv("BROWSER_PUBLISH_CONFIRM_WAIT_S", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def job(**overrides) -> PublishJob:
    from app.assets import StagedAsset

    assets = {
        "video": StagedAsset(
            role="video", path="/tmp/scratch/clip.mp4", filename="clip.mp4", size_bytes=99
        )
    }
    assets.update(overrides.pop("assets", {}))
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
        environment=None,
        intent=intent,
        assets=assets,
    )


def cover_asset():
    from app.assets import StagedAsset

    return StagedAsset(
        role="cover", path="/tmp/scratch/cover.jpg", filename="cover.jpg", size_bytes=5
    )


# --- reaching the editor ----------------------------------------------------


async def test_an_editor_already_on_screen_is_reported_with_its_variant():
    page = FakePage(url=EDITOR_URL)
    arrival = await dp._await_editor(page, Deadline(5))
    assert arrival.arrived is True
    assert arrival.variant == "version_2"


async def test_an_editor_that_never_opens_is_a_timeout_bounded_by_the_deadline():
    """Spec 7.2: the reference implementation's equivalent wait has no ceiling
    at all, so an editor that never renders hangs the caller for as long as the
    process lives. The stage ceiling here is 180s by default - the deadline is
    what must actually stop it."""
    page = FakePage(url=UPLOAD_URL)
    started = time.monotonic()
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._await_editor(page, Deadline(0.5))
    elapsed = time.monotonic() - started

    assert excinfo.value.status is SessionStatus.TIMEOUT
    assert excinfo.value.detail["stage"] == "editor"
    assert elapsed < 5  # not the 180s stage ceiling


async def test_a_login_prompt_instead_of_the_editor_is_a_lost_session_not_a_timeout():
    """The two look identical to a URL poll and need opposite responses: one is
    "wait and retry", the other is "this account must re-scan a QR code". A
    session dropped mid-publish reported as `timeout` would be retried forever
    against an account that can never succeed."""
    page = FakePage(url=UPLOAD_URL, visible={"text=手机号登录"})
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._await_editor(page, Deadline(0.4))

    assert excinfo.value.status is SessionStatus.SESSION_INVALID
    assert excinfo.value.detail["reason"] == "session_lost_during_publish"


# --- waiting out the upload -------------------------------------------------


async def test_a_finished_transfer_is_recognised_without_a_retry():
    page = FakePage(url=EDITOR_URL, visible={dp.UPLOAD_DONE_SELECTOR})
    assert await dp._await_upload_complete(page, job(), Deadline(5)) == {"upload_retries": 0}


async def test_a_failed_upload_is_re_fed_to_the_pages_own_replacement_input():
    """Spec 7.4 self-heal. The failure card carries its own file input, and
    using it is the difference between a transient network blip costing one
    retry and costing the whole publish."""
    page = FakePage(
        url=EDITOR_URL,
        # Fails until something has been handed to a file input again.
        visible=lambda p: (
            {dp.UPLOAD_DONE_SELECTOR} if p.file_inputs else {dp.UPLOAD_FAILED_SELECTOR}
        ),
        counts={dp.RETRY_INPUT_SELECTOR: 1},
    )
    result = await dp._await_upload_complete(page, job(), Deadline(5))

    assert result == {"upload_retries": 1}
    selector, path, _index = page.file_inputs[0]
    assert selector == dp.RETRY_INPUT_SELECTOR
    assert path == "/tmp/scratch/clip.mp4"


async def test_a_persistently_failing_upload_gives_up_with_a_business_reason(monkeypatch):
    """Bounded retries, not a loop that keeps re-uploading a file the platform
    has already rejected twice - each attempt costs the full transfer again."""
    monkeypatch.setenv("BROWSER_PUBLISH_UPLOAD_RETRIES", "1")
    get_settings.cache_clear()

    page = FakePage(
        url=EDITOR_URL,
        visible={dp.UPLOAD_FAILED_SELECTOR},
        counts={dp.RETRY_INPUT_SELECTOR: 1},
    )
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._await_upload_complete(page, job(), Deadline(5))

    assert excinfo.value.status is SessionStatus.FAILED
    assert excinfo.value.detail["reason"] == "upload_failed"
    assert excinfo.value.detail["retries"] == 1
    assert len(page.file_inputs) == 1  # retried once, then stopped


async def test_a_failed_upload_with_nowhere_to_retry_says_so_specifically():
    """Distinct from `upload_failed`: the platform did not reject the file, the
    page simply no longer offers a way to hand it over. Collapsing the two would
    hide a DOM change behind what looks like a flaky upload."""
    page = FakePage(url=EDITOR_URL, visible={dp.UPLOAD_FAILED_SELECTOR})
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._await_upload_complete(page, job(), Deadline(5))

    assert excinfo.value.detail["reason"] == "upload_retry_impossible"


async def test_an_upload_that_never_finishes_stops_at_the_deadline_not_its_own_ceiling():
    """The upload ceiling is 900s by default - minutes of video over a
    residential proxy. A publish whose earlier stages already spent the budget
    must not still be entitled to those 900 seconds (spec 7.2)."""
    page = FakePage(url=EDITOR_URL, visible=())
    started = time.monotonic()
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._await_upload_complete(page, job(), Deadline(0.5))
    elapsed = time.monotonic() - started

    assert excinfo.value.status is SessionStatus.TIMEOUT
    assert excinfo.value.detail["stage"] == "upload"
    assert elapsed < 5


def test_a_stage_never_gets_more_time_than_the_publish_has_left():
    deadline = Deadline(2)
    assert dp._stage_budget(deadline, 900) <= 2
    assert dp._stage_budget(deadline, 0.5) == 0.5


# --- the cover dialog -------------------------------------------------------


async def test_no_cover_asked_for_means_the_dialog_is_never_opened():
    page = FakePage(url=EDITOR_URL)
    assert await dp._set_cover(page, job(), Deadline(5)) == {"cover": "not_requested"}
    assert page.file_inputs == []


async def test_the_cover_goes_to_the_second_upload_input_not_the_first():
    """Spec 7.4, the expensive one.

    The dialog holds four hidden file inputs; [0] and [1] belong to the "AI
    reference image" panel. Using `.first` uploads successfully, reports
    success, and produces a post with no cover on it - a failure visible only on
    the published feed. The index is the whole test.
    """
    page = FakePage(
        url=EDITOR_URL,
        visible={
            f"text={dp.COVER_ENTRY_TEXT}",
            dp.COVER_MODAL_SELECTOR,
            f"text={dp.COVER_CONFIRM_BUTTON_TEXT}",
        },
        counts={dp.COVER_HIDDEN_INPUT_SELECTOR: 4},
    )
    result = await dp._set_cover(
        page, job(assets={"cover": cover_asset()}), Deadline(10)
    )

    assert result["cover"] == "applied"
    selector, path, index = page.file_inputs[0]
    assert (selector, index) == (dp.COVER_HIDDEN_INPUT_SELECTOR, 1)
    assert path == "/tmp/scratch/cover.jpg"


async def test_a_cover_dialog_with_too_few_inputs_refuses_rather_than_guessing():
    """If the dialog's layout changed, the fallback everyone reaches for is
    `.first` - which is precisely the AI-reference input. Refusing leaves a
    draft to inspect; guessing publishes a coverless post nobody notices."""
    page = FakePage(
        url=EDITOR_URL,
        visible={
            f"text={dp.COVER_ENTRY_TEXT}",
            dp.COVER_MODAL_SELECTOR,
            f"text={dp.COVER_CONFIRM_BUTTON_TEXT}",
        },
        counts={dp.COVER_HIDDEN_INPUT_SELECTOR: 1},
    )
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_cover(page, job(assets={"cover": cover_asset()}), Deadline(10))

    assert excinfo.value.detail["reason"] == "cover_input_missing"
    assert excinfo.value.detail["inputs"] == 1
    assert page.file_inputs == []  # nothing was uploaded to the wrong input


async def test_a_missing_cover_entry_point_fails_instead_of_publishing_uncovered():
    page = FakePage(url=EDITOR_URL, visible=())
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._set_cover(page, job(assets={"cover": cover_asset()}), Deadline(5))

    assert excinfo.value.detail["reason"] == "cover_entry_missing"


# --- visibility and download permission -------------------------------------


async def test_a_request_matching_the_platform_default_touches_no_control():
    """Why refusing on a missing control is affordable: the ordinary publish
    does not depend on these selectors at all."""
    page = FakePage(url=EDITOR_URL, visible=())
    notes = await dp._apply_options(page, job(), Deadline(5))

    assert notes == {"visibility": "platform_default", "allow_download": "platform_default"}
    assert page.clicks == []


async def test_a_private_post_is_refused_when_its_control_cannot_be_found():
    """The asymmetry that decides this behaviour: a post the user marked
    `private` going out publicly cannot be taken back, while a refused publish
    costs an inspection of a draft."""
    page = FakePage(url=EDITOR_URL, visible=())
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._apply_options(page, job(visibility="private"), Deadline(5))

    assert excinfo.value.detail["reason"] == "visibility_control_missing"
    assert excinfo.value.detail["requested_visibility"] == "private"


async def test_a_private_post_is_applied_through_the_radio_wrapper():
    """Spec 7.4: Semi renders the label as `.semi-radio-addon`, which often
    carries `pointer-events: none` - clicking it burns the full actionability
    timeout and then fails, which reads like a hung page. The wrapper is the
    interactive element."""
    page = FakePage(url=EDITOR_URL, visible={dp.SEMI_RADIO_SELECTOR})
    notes = await dp._apply_options(page, job(visibility="private"), Deadline(5))

    assert notes["visibility"] == "applied"
    assert dp.SEMI_RADIO_SELECTOR in page.clicks


async def test_a_download_permission_change_is_refused_without_its_toggle():
    page = FakePage(url=EDITOR_URL, visible=())
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._apply_options(page, job(allow_download=False), Deadline(5))

    assert excinfo.value.detail["reason"] == "download_control_missing"
    assert excinfo.value.detail["requested_allow_download"] is False


async def test_a_toggle_already_in_the_requested_state_is_left_alone():
    """Clicking a switch that is already correct turns the setting *off*. For
    "allow others to save this video" that is a change the user never asked
    for, and one no error message would ever report."""
    label = f"text={dp.DOWNLOAD_TOGGLE_TEXTS[0]}"
    page = FakePage(
        url=EDITOR_URL,
        visible={label},
        counts={dp.SWITCH_NEAR_LABEL_XPATH: 1, dp.SEMI_SWITCH_INPUT_SELECTOR: 1},
        attributes={dp.SWITCH_NEAR_LABEL_XPATH: {"class": "semi-switch"}},
    )
    notes = await dp._apply_options(page, job(allow_download=False), Deadline(5))

    assert notes["allow_download"] == "applied"
    assert page.clicks == []


async def test_a_toggle_in_the_wrong_state_is_clicked():
    label = f"text={dp.DOWNLOAD_TOGGLE_TEXTS[0]}"
    page = FakePage(
        url=EDITOR_URL,
        visible={label},
        counts={dp.SWITCH_NEAR_LABEL_XPATH: 1, dp.SEMI_SWITCH_INPUT_SELECTOR: 1},
        attributes={
            dp.SWITCH_NEAR_LABEL_XPATH: {"class": "semi-switch semi-switch-checked"}
        },
    )
    await dp._apply_options(page, job(allow_download=False), Deadline(5))
    assert dp.SEMI_SWITCH_INPUT_SELECTOR in page.clicks


# --- confirming the publish -------------------------------------------------


async def test_the_redirect_to_the_content_manager_is_what_confirms_a_publish():
    """Nothing on the editor page says a post went out; the platform's own
    redirect is the only signal. Reporting success off a click that landed
    would report success for every publish that silently failed."""
    page = FakePage(
        url=lambda p: MANAGE_URL if f"text={dp.PUBLISH_BUTTON_TEXT}" in p.clicks else EDITOR_URL,
        visible={f"text={dp.PUBLISH_BUTTON_TEXT}"},
    )
    result = await dp._confirm_publish(page, Deadline(5))

    assert result["confirm_attempts"] == 1
    assert MANAGE_URL in result["final_url"]


async def test_a_publish_that_never_lands_times_out_saying_where_it_got_stuck():
    page = FakePage(url=EDITOR_URL, visible={f"text={dp.PUBLISH_BUTTON_TEXT}"})
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._confirm_publish(page, Deadline(0.6))

    assert excinfo.value.status is SessionStatus.TIMEOUT
    assert excinfo.value.detail["page_state"] == "editing"


async def test_a_verification_challenge_is_reported_rather_than_retried_blindly():
    """This endpoint has no channel to supply an SMS code, so retrying until the
    deadline would report `timeout` for a publish that was one code away.
    Checked only after a click failed to land - the same field is present but
    inert on a healthy page, which is how an early login judge ended up
    reporting `sms_required` on every poll."""
    page = FakePage(
        url=EDITOR_URL,
        # The SMS field the platform module itself declares - not a copy, so a
        # selector change is not silently absorbed by this test.
        visible={dp.douyin.SMS_INPUT_SELECTORS[0], f"text={dp.PUBLISH_BUTTON_TEXT}"},
    )
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._confirm_publish(page, Deadline(5))

    assert excinfo.value.detail["reason"] == "sms_verification_required"
    assert excinfo.value.status is SessionStatus.FAILED


async def test_a_missing_cover_complaint_is_self_healed_with_the_recommended_frame():
    """The platform refuses to publish without a cover, and a publish request
    that never asked for one would otherwise die at the last click. The
    recommended frame is the platform's own suggestion, so accepting it is not
    a guess."""
    landed = f"text={dp.CONFIRM_BUTTON_TEXT}"
    page = FakePage(
        url=lambda p: MANAGE_URL if dp.RECOMMEND_COVER_SELECTOR in p.clicks else EDITOR_URL,
        visible={
            f"text={dp.PUBLISH_BUTTON_TEXT}",
            f"text={dp.COVER_REQUIRED_TEXT}",
            dp.RECOMMEND_COVER_SELECTOR,
            landed,
        },
    )
    result = await dp._confirm_publish(page, Deadline(10))

    assert result["recovered_cover"] is True
    assert result["confirm_attempts"] == 2


# --- exception to status ----------------------------------------------------


def test_a_typed_step_failure_keeps_its_status_and_detail():
    outcome = dp._outcome_from_exception(
        dp.StepFailure(SessionStatus.SESSION_INVALID, "gone", reason="session_lost_during_publish")
    )
    assert outcome.status is SessionStatus.SESSION_INVALID
    assert outcome.detail["reason"] == "session_lost_during_publish"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("net::ERR_PROXY_CONNECTION_FAILED at https://creator.douyin.com/", SessionStatus.PROXY_FAILED),
        ("net::ERR_TUNNEL_CONNECTION_FAILED", SessionStatus.PROXY_FAILED),
        ("Timeout 30000ms exceeded.", SessionStatus.TIMEOUT),
        ("TargetClosedError: page crashed", SessionStatus.FAILED),
    ],
)
def test_an_untyped_crash_is_classified_the_same_way_validation_classifies_it(raw, expected):
    """One classifier, shared with the session validator. A second copy here is
    how `proxy_failed` ends up meaning different things depending on which
    endpoint produced it - the two-implementations failure of spec 7.1, applied
    to error handling."""
    outcome = dp._outcome_from_exception(RuntimeError(raw))
    assert outcome.status is expected
    assert dp._KIND_TO_STATUS[ProbeKind.PROXY_FAILED] is SessionStatus.PROXY_FAILED


def test_proxy_credentials_never_reach_an_outcome_message():
    outcome = dp._outcome_from_exception(
        RuntimeError("net::ERR_TUNNEL_CONNECTION_FAILED via http://alice:s3cr3t@proxy:8080")
    )
    assert "s3cr3t" not in outcome.message
    assert "alice" not in outcome.message


def test_a_crash_outcome_is_never_reported_as_a_success():
    outcome: PublishOutcome = dp._outcome_from_exception(RuntimeError("boom"))
    assert outcome.success is False
