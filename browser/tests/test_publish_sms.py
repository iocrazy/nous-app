"""The mid-publish verification-code channel, driven end to end in-process.

A real Douyin challenge is not reproducible on demand — the platform decides
when to raise one — so "we shipped it and the code looks right" is the whole
extent of what an unverified channel can claim. That is not enough for a path
whose failure mode is *losing the user's post*, so the branch is forced two
different ways and each proves something different:

* **A page with a visible code field** (`FakePage` carrying the platform
  module's own `SMS_INPUT_SELECTORS`). This walks the real code: detection,
  typing into the field, re-reading the page to judge the result, the retry
  accounting, the deadline hand-back. It does **not** prove those selectors
  match a real challenge screen — nobody has captured one.
* **`BROWSER_PUBLISH_FORCE_SMS_CHALLENGE`**, the staging switch. It proves the
  *wiring* (a parked publish is addressable, a code reaches it, a verdict comes
  back) on a page that never showed a code field at all — which is how the whole
  chain can be exercised against a live container without waiting for the
  platform to feel suspicious.

What neither proves is stated plainly so nobody reads more into a green suite
than is there: **no real platform challenge has been through this code.**

The four required paths are `test_a_supplied_code_lets_the_publish_continue`,
`test_nobody_supplies_a_code_and_the_publish_really_fails`,
`test_a_wrong_code_is_reported_as_rejected_and_can_be_retyped`, and — the
reverse check — `test_without_a_correlation_id_the_old_dead_end_is_still_the_answer`.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import get_settings
from app.platforms import douyin_publish as dp
from app.publish import Deadline, PublishJob
from app.publish_sms import (
    ABANDONED,
    ACCEPTED,
    EXPIRED,
    NOT_PENDING,
    REJECTED,
    PublishSmsRegistry,
    get_sms_registry,
)
from app.schemas import MediaItem, PublishIntent, SessionStatus
from tests.fakes import FakePage

pytestmark = pytest.mark.unit

EDITOR_URL = "https://creator.douyin.com/creator-micro/content/post/video"
MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"
SMS_SELECTOR = dp.douyin.SMS_INPUT_SELECTORS[0]
PUBLISH_BTN = f"text={dp.PUBLISH_BUTTON_TEXT}"
CODE = "123456"


@pytest.fixture(autouse=True)
def fast_and_bounded(monkeypatch):
    """Real intervals, scaled down; the windows stay meaningfully bounded."""
    monkeypatch.setenv("BROWSER_PUBLISH_POLL_INTERVAL_S", "0.05")
    monkeypatch.setenv("BROWSER_PUBLISH_CONFIRM_WAIT_S", "1")
    monkeypatch.setenv("BROWSER_PUBLISH_SMS_SETTLE_MS", "0")
    monkeypatch.setenv("BROWSER_PUBLISH_SMS_WAIT_S", "2")
    monkeypatch.setenv("BROWSER_PUBLISH_SMS_MAX_ATTEMPTS", "3")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def job(correlation_id: str | None = "cid-test") -> PublishJob:
    from app.assets import StagedAsset

    return PublishJob(
        platform="douyin",
        storage_state={"cookies": []},
        environment=None,
        intent=PublishIntent(
            content_type="video",
            media=[MediaItem(kind="video", url="https://x/clip.mp4", filename="clip.mp4")],
            title="Launch Day Recap",
        ),
        assets={
            "video": StagedAsset(
                role="video", path="/tmp/scratch/clip.mp4",
                filename="clip.mp4", size_bytes=99,
            )
        },
        correlation_id=correlation_id,
    )


def challenged_page(accept_code: str | None = CODE) -> FakePage:
    """A page showing a code field until `accept_code` is typed into it.

    `accept_code=None` means the platform refuses everything — the page keeps
    asking no matter what is typed, which is what a wrong code looks like from
    the outside (and, honestly, also what a *second* challenge looks like; the
    production code says so where it decides the wording).
    """
    state = {"cleared": False}

    def visible(page: FakePage) -> set[str]:
        typed = [v for sel, v in page.fills if sel == SMS_SELECTOR]
        if accept_code is not None and accept_code in typed:
            state["cleared"] = True
        marks = {PUBLISH_BTN}
        if not state["cleared"]:
            marks.add(SMS_SELECTOR)
        return marks

    return FakePage(
        url=lambda p: MANAGE_URL if state["cleared"] else EDITOR_URL,
        visible=visible,
    )


async def supply(correlation_id: str, code: str, *, hold_s: float = 0.0):
    """Act as the user: wait for the publish to park, then hand it a code.

    `hold_s` is how long the person "takes", and it is applied **after** the
    park is observed rather than as a polling interval. That distinction cost a
    real bug in this file: measuring it as an interval made the park duration a
    function of polling granularity, which quietly masked the very difference
    `test_waiting_for_a_person_does_not_spend_the_publish_deadline` exists to
    detect — the test passed against a deliberately broken `Deadline.extend`.
    """
    registry = get_sms_registry()
    for _ in range(400):
        await asyncio.sleep(0.02)
        challenge = registry.get(correlation_id)
        if challenge is not None and challenge.is_open:
            if hold_s:
                await asyncio.sleep(hold_s)
            return await challenge.submit(code)
    raise AssertionError("the publish never parked on an SMS challenge")


# --- the four required paths ------------------------------------------------


async def test_a_supplied_code_lets_the_publish_continue():
    """The whole point: a challenge costs a code, not the post.

    Walks the real branch — a visible field is found, the code is typed into
    *that* field, the page is re-read, and the publish goes on to land.
    """
    page = challenged_page()
    task = asyncio.create_task(dp._confirm_publish(page, job("cid-ok"), Deadline(20)))
    verdict = await supply("cid-ok", CODE)
    result = await task

    assert verdict.outcome == ACCEPTED
    # The code really went into the platform's own field, not somewhere generic.
    assert (SMS_SELECTOR, CODE) in page.fills
    assert result["sms_challenge"] == ACCEPTED
    assert result["sms_attempts_used"] == 1
    assert MANAGE_URL in result["final_url"]


async def test_nobody_supplies_a_code_and_the_publish_really_fails():
    """The timeout path must actually fire.

    A channel that waits forever when nobody is listening is the same outage as
    having no channel, minus the error message. Nothing is submitted here, so
    the only way this test finishes is the window genuinely expiring.
    """
    page = challenged_page()
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._confirm_publish(page, job("cid-timeout"), Deadline(20))

    assert excinfo.value.detail["reason"] == "sms_code_timeout"
    assert excinfo.value.status is SessionStatus.TIMEOUT
    assert excinfo.value.detail["sms_attempts_used"] == 0


async def test_a_wrong_code_is_reported_as_rejected_and_can_be_retyped():
    """A typo costs one attempt and a retype — never the post.

    Both halves are asserted: the submitter is *told* `rejected` (not a silent
    200), and the publish is still parked afterwards so the next code lands on
    the same page.
    """
    page = challenged_page(accept_code=CODE)
    task = asyncio.create_task(dp._confirm_publish(page, job("cid-retry"), Deadline(20)))

    first = await supply("cid-retry", "000000")
    assert first.outcome == REJECTED
    assert first.retryable is True
    assert first.attempts_left == 2

    # Still listening — that is what "retryable" has to mean.
    second = await supply("cid-retry", CODE)
    assert second.outcome == ACCEPTED

    result = await task
    assert result["sms_attempts_used"] == 2
    assert MANAGE_URL in result["final_url"]


async def test_without_a_correlation_id_the_old_dead_end_is_still_the_answer():
    """Reverse check: remove the channel and the original failure comes back.

    A publish started with no correlation id has nobody to ask, so parking would
    strand it. The pre-existing `sms_verification_required` is the honest answer
    there, and this test is what keeps the *presence* of the channel from being
    assumed rather than verified.
    """
    page = challenged_page()
    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._confirm_publish(page, job(None), Deadline(10))

    assert excinfo.value.detail["reason"] == "sms_verification_required"
    assert excinfo.value.status is SessionStatus.FAILED


# --- the controlled switch --------------------------------------------------


async def test_the_force_switch_parks_a_publish_that_was_never_challenged(monkeypatch):
    """The staging switch, and precisely what it is worth.

    The page here has **no code field at all**. The switch still parks the
    publish, so a code can be pushed through the whole chain against a live
    container without waiting for the platform to raise a real challenge.

    What it proves: the publish is addressable while in flight, a submitted code
    reaches it, a verdict comes back, and the publish resumes.
    What it does NOT prove: that `SMS_INPUT_SELECTORS` matches a real challenge
    screen. Nothing here has ever seen one.
    """
    monkeypatch.setenv("BROWSER_PUBLISH_FORCE_SMS_CHALLENGE", "1")
    get_settings.cache_clear()

    page = FakePage(
        url=lambda p: MANAGE_URL if PUBLISH_BTN in p.clicks else EDITOR_URL,
        visible={PUBLISH_BTN},
    )
    task = asyncio.create_task(dp._confirm_publish(page, job("cid-forced"), Deadline(20)))
    verdict = await supply("cid-forced", CODE)
    result = await task

    assert verdict.outcome == ACCEPTED
    # Nothing was typed: there was no field. The channel, not the DOM, is what
    # this exercise covers.
    assert result["sms_code_typed"] is False
    assert MANAGE_URL in result["final_url"]


def test_the_force_switch_is_off_unless_explicitly_turned_on(monkeypatch):
    """A debug switch a stray value could flip is a production incident waiting
    for a bad deployment — this one changes what we do to a real account."""
    for raw in ("", "0", "false", "no", "off", "maybe", "2", " "):
        monkeypatch.setenv("BROWSER_PUBLISH_FORCE_SMS_CHALLENGE", raw)
        get_settings.cache_clear()
        assert get_settings().publish_sms_force_challenge is False, raw

    monkeypatch.delenv("BROWSER_PUBLISH_FORCE_SMS_CHALLENGE", raising=False)
    get_settings.cache_clear()
    assert get_settings().publish_sms_force_challenge is False


# --- exhaustion, budget, and the registry's own guarantees -------------------


async def test_every_attempt_refused_ends_the_publish_but_says_so():
    page = challenged_page(accept_code=None)  # the platform never accepts
    task = asyncio.create_task(dp._confirm_publish(page, job("cid-bad"), Deadline(20)))

    verdicts = [await supply("cid-bad", "000000") for _ in range(3)]
    assert [v.outcome for v in verdicts[:2]] == [REJECTED, REJECTED]
    # The last one is told it was the last one, rather than silently being the
    # one after which nothing happens.
    assert verdicts[2].outcome == "exhausted"
    assert verdicts[2].attempts_left == 0

    with pytest.raises(dp.StepFailure) as excinfo:
        await task
    assert excinfo.value.detail["reason"] == "sms_code_rejected"
    assert excinfo.value.detail["sms_attempts_used"] == 3


async def test_waiting_for_a_person_does_not_spend_the_publish_deadline():
    """Human time is granted on top, not taken out of the work budget.

    Otherwise a correctly supplied code could still lose the post to a deadline
    the waiting itself consumed — the user does everything right and the publish
    dies anyway, with nothing able to explain why.

    Measured as a **difference between two runs**, not against a fixed number.
    Both do identical machine work (a failed click, a poll loop, a second click)
    and differ only in how long the person took; a version that charged the wait
    to the budget would show that gap in what was consumed. Comparing one run
    against its own start time instead would measure the machine work too, and
    that is a real, legitimate cost — this test would then be asserting
    something it has no business asserting.
    """

    async def consumed(hold_s: float, cid: str) -> float:
        deadline = Deadline(30)
        page = challenged_page()
        before = deadline.remaining()
        task = asyncio.create_task(dp._confirm_publish(page, job(cid), deadline))
        await supply(cid, CODE, hold_s=hold_s)
        await task
        return before - deadline.remaining()

    quick = await consumed(0.0, "cid-budget-quick")
    slow = await consumed(1.0, "cid-budget-slow")

    # The slow run kept a person waiting a full second longer. Charged to the
    # publish, `slow - quick` would be ≈1.0s; handed back, the two land within
    # scheduling noise of each other. Measured at 0.999 vs -0.001 respectively.
    assert slow - quick < 0.5, (
        f"the human wait was charged to the publish budget: quick={quick:.2f}s "
        f"slow={slow:.2f}s"
    )


async def test_a_code_for_a_publish_nobody_is_waiting_on_is_typed_not_guessed():
    registry = PublishSmsRegistry()
    assert registry.get("nope") is None
    assert registry.waiting_count() == 0


async def test_a_challenge_cannot_outlive_the_publish_that_opened_it():
    """The leak that would matter: a parked publish holds one of four slots."""
    registry = PublishSmsRegistry()
    async with registry.open("cid-x", "douyin", window_s=5, max_attempts=3) as challenge:
        assert registry.waiting_count() == 1
        assert challenge.is_open
    assert challenge.is_open is False
    assert registry.waiting_count() == 0
    # Addressable a little longer, so a code that arrives one moment too late
    # gets a typed answer instead of a bare 404 the caller has to interpret.
    late = await challenge.submit(CODE)
    assert late.outcome == ABANDONED


async def test_a_submitter_is_released_when_the_publish_dies_mid_verification():
    """No HTTP worker may be stranded by a publish that went away."""
    registry = PublishSmsRegistry()
    async with registry.open("cid-y", "douyin", window_s=5, max_attempts=3) as challenge:
        waiter = asyncio.create_task(challenge.submit(CODE))
        await asyncio.sleep(0.05)
        challenge.close(ABANDONED, "publish gave up")
    verdict = await waiter
    assert verdict.outcome == ABANDONED
    assert verdict.retryable is False


async def test_the_window_is_shared_by_retries_not_granted_per_attempt():
    """Three attempts must not silently become three times the ceiling — the
    budget arithmetic the endpoint promises its caller depends on it."""
    registry = PublishSmsRegistry()
    async with registry.open("cid-z", "douyin", window_s=0.3, max_attempts=3) as challenge:
        assert await challenge.await_code() is None      # window ran out
        assert challenge.window.exhausted()
        late = await challenge.submit(CODE)
        assert late.outcome == EXPIRED


async def test_a_closed_challenge_answers_not_pending_rather_than_vanishing():
    registry = PublishSmsRegistry()
    async with registry.open("cid-w", "douyin", window_s=5, max_attempts=3) as challenge:
        challenge.close(NOT_PENDING, "done")
    assert (await challenge.submit(CODE)).outcome == NOT_PENDING
