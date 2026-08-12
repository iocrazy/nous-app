"""The 身份验证 screen: click it, and never claim a code nobody asked for.

Observed on a real Douyin bind (2026-08-11, user screenshot). Between "scanned"
and "signed in" the platform inserted:

    身份验证
    为保障账号安全，请先完成身份验证，以确保为本人操作
    [ 接收短信验证码  > ]      the platform texts the account's phone
    [ 发送短信验证    > ]      the USER texts the platform from that phone

The login flow clicked neither, so **the platform never sent anything**. Worse,
that screen renders a code field of its own, so the judge answered
`sms_required` and the modal said "the platform sent a code to the phone number
on this account" — a sentence that was false, in front of a user who then waited
out the full five-minute TTL for a message that had never been requested.

These tests drive the real judge, the real `LoginDriver` and the real
`LoginSession` over a scripted page, because the parts that were wrong are the
seams between them: the judge that mislabelled the screen, the driver that had
no click for it, and the session that reported a state it had not earned. The
one thing that cannot be tested here is whether the captions match the live DOM
— that is stated in the PR, and the design falls to the safe side when they do
not (a typed failure the user can see, never a silent wait).
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.login import LoginDriver
from app.login_sessions import (
    IDENTITY_CHALLENGE_BLOCKED,
    IDENTITY_CHALLENGE_STALLED,
    IDENTITY_CHALLENGE_UNCLICKABLE,
    MAX_IDENTITY_CHALLENGE_POLLS,
    PAGE_EVIDENCE_KEY,
    LoginSession,
)
from app.platforms.douyin import LOGIN_SPEC
from app.schemas import SessionStatus
from tests.fakes import FakePage

pytestmark = pytest.mark.unit

LOGIN_URL = "https://creator.douyin.com/"

# How the fake keys a `get_by_text` call.
RECEIVE = "text=接收短信验证码"
SEND_YOURSELF = "text=发送短信验证"
GET_CODE = "text=获取验证码"
# `SMS_INPUT_SELECTORS[0]` — the field that made the chooser read as
# `sms_required` in the first place. Present on the chooser on purpose.
CODE_FIELD = 'input[placeholder*="验证码"]'


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch):
    """The post-click waits are real seconds in production. Not here."""
    monkeypatch.setenv("BROWSER_LOGIN_SMS_SETTLE_S", "0")
    monkeypatch.setenv("BROWSER_LOGIN_CHALLENGE_PROGRESS_POLL_S", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def session_over(page: FakePage) -> LoginSession:
    """A real session on a real driver, over a scripted page."""
    driver = LoginDriver(LOGIN_SPEC, None, None, None, page)
    return LoginSession("sess-1", LOGIN_SPEC, driver, "data:image/png;base64,QR0")


def chooser_then_code_form(page: FakePage) -> set[str]:
    """The observed two-screen flow: chooser → code form with a send button.

    Expressed as a function of what has been clicked rather than as a fixed
    sequence, so a test that *does not* click stays on the chooser forever —
    which is precisely the bug's shape and must remain reachable.
    """
    if RECEIVE in page.clicks:
        return {CODE_FIELD, GET_CODE}
    return {RECEIVE, SEND_YOURSELF, CODE_FIELD}


# --- the click ---------------------------------------------------------------


async def test_the_chooser_is_answered_by_clicking_receive_sms():
    page = FakePage(url=LOGIN_URL, visible=chooser_then_code_form)

    snapshot = await session_over(page).poll_status()

    assert RECEIVE in page.clicks
    # The other card reverses the flow: the human texts the platform from the
    # bound handset. An unattended login cannot finish it, so clicking it would
    # strand the session on a screen with no way forward.
    assert SEND_YOURSELF not in page.clicks
    assert snapshot.status is SessionStatus.SMS_REQUIRED


async def test_the_send_code_button_on_the_next_screen_is_pressed_too():
    """Two clicks, not one.

    The chooser hands over to a form that sends nothing until 获取验证码 is
    pressed (same shape the reference project drives on Douyin's publish-side
    SMS gate). Stopping after the first click would leave the user waiting on a
    code again — the same bug one screen later.
    """
    page = FakePage(url=LOGIN_URL, visible=chooser_then_code_form)

    await session_over(page).poll_status()

    assert page.clicks == [RECEIVE, GET_CODE]


async def test_captions_are_matched_exactly():
    """`exact=True` on every clicked caption.

    Playwright's substring mode also matches every *ancestor* containing the
    text, so `.first` on a generous match can resolve to the card stack (or the
    body) rather than the card. The house scars are 「允许」⊂「不允许」 and
    「重新上传」⊂「清空并重新上传」; this screen adds a third way to lose.
    """
    page = FakePage(url=LOGIN_URL, visible=chooser_then_code_form)

    await session_over(page).poll_status()

    asked = dict(page.text_queries)
    for caption in ("接收短信验证码", "发送短信验证", "获取验证码"):
        assert asked.get(caption) is True, f"{caption} was not matched exactly"


# --- what the user is told ---------------------------------------------------


async def test_the_chooser_is_never_reported_as_sms_required():
    """The guard for the actual bug.

    While the platform is showing its menu, nothing has been sent to anybody.
    Reporting `sms_required` there is what licensed "the platform sent a code to
    the phone number on this account", and the user waited out the TTL for it.
    """
    # Only the option we cannot drive is on offer, so nothing gets clicked and
    # the screen stays up — the state the old code reported as `sms_required`.
    page = FakePage(url=LOGIN_URL, visible={SEND_YOURSELF, CODE_FIELD})

    snapshot = await session_over(page).poll_status()

    assert snapshot.status is SessionStatus.IDENTITY_CHALLENGE
    assert snapshot.detail.get("code_requested") is not True


async def test_a_requested_code_is_marked_as_requested():
    page = FakePage(url=LOGIN_URL, visible=chooser_then_code_form)

    snapshot = await session_over(page).poll_status()

    # The evidence is our own landed click, not anything read off the page.
    assert snapshot.status is SessionStatus.SMS_REQUIRED
    assert snapshot.detail["code_requested"] is True


async def test_a_code_screen_we_never_asked_for_is_not_marked_as_requested():
    """A code field with no chooser and no send button.

    We did not ask for anything here and cannot prove the platform did, so the
    flag stays absent and the UI keeps its neutral copy. Being wrong in this
    direction costs a vaguer sentence; being wrong in the other direction is
    the bug.
    """
    page = FakePage(url=LOGIN_URL, visible={CODE_FIELD})

    snapshot = await session_over(page).poll_status()

    assert snapshot.status is SessionStatus.SMS_REQUIRED
    assert "code_requested" not in snapshot.detail


async def test_the_code_is_requested_once_however_long_the_page_lingers():
    """One click per login: each one is a real text message to a real phone."""
    page = FakePage(url=LOGIN_URL, visible=chooser_then_code_form)
    session = session_over(page)

    for _ in range(4):
        await session.poll_status()

    assert page.clicks.count(GET_CODE) == 1
    assert page.clicks.count(RECEIVE) == 1


# --- when the screen does not cooperate --------------------------------------


async def test_an_unanswerable_chooser_ends_in_a_typed_failure():
    """Not a silent wait. The state we refuse to go back to.

    An account offered only 发送短信验证 has no path an unattended login can
    take. Sitting on it until the TTL is indistinguishable, from the user's
    side, from the original bug — so a bounded run of unanswered polls becomes
    a `failed` that says which options were actually on offer.
    """
    page = FakePage(url=LOGIN_URL, visible={SEND_YOURSELF, CODE_FIELD})
    session = session_over(page)

    for _ in range(MAX_IDENTITY_CHALLENGE_POLLS - 1):
        assert (await session.poll_status()).status is SessionStatus.IDENTITY_CHALLENGE
    final = await session.poll_status()

    assert final.status is SessionStatus.FAILED
    assert final.detail["reason"] == IDENTITY_CHALLENGE_UNCLICKABLE
    # Which options the platform actually showed — the difference between "our
    # caption is stale" and "this account is only offered the manual flow".
    assert final.detail["options_seen"] == ["发送短信验证"]
    assert final.detail.get("code_requested") is not True


async def test_a_chooser_that_never_moves_after_the_click_also_fails_typed():
    """Clicked, and the platform stayed put. Different reason, same discipline.

    The remedy the user is given differs (retry vs. "that screen changed"), so
    the two are not collapsed into one message.
    """
    # Clicking lands, but the chooser never goes away.
    page = FakePage(url=LOGIN_URL, visible={RECEIVE, SEND_YOURSELF, CODE_FIELD})
    session = session_over(page)

    final = None
    for _ in range(MAX_IDENTITY_CHALLENGE_POLLS):
        final = await session.poll_status()

    assert final is not None
    assert final.status is SessionStatus.FAILED
    assert final.detail["reason"] == IDENTITY_CHALLENGE_STALLED
    # Still exactly one click on the caption itself, however many polls a stuck
    # screen takes. (The escalation re-clicks a *different* node — the card —
    # and is capped at one of its own; see the evidence tests below.)
    assert page.clicks.count(RECEIVE) == 1


async def test_the_qr_code_is_dropped_while_the_challenge_is_up():
    """Nothing on that screen is scannable; showing the old image invites a
    scan that cannot help."""
    page = FakePage(url=LOGIN_URL, visible={SEND_YOURSELF, CODE_FIELD})
    session = session_over(page)

    snapshot = await session.poll_status()

    assert snapshot.status is SessionStatus.IDENTITY_CHALLENGE
    assert snapshot.qrcode_data_url is None


# --- what a failure leaves behind (2026-08-12) -------------------------------
#
# The 08-11 fix above made the stall *typed*. It shipped, and the next real
# bind stalled anyway: click reported as landed, page unmoved, user with no
# code screen and no text message, 27 seconds start to finish. `detail` said
# `identity_challenge_stalled` and nothing else — true, and not enough to
# choose between "the click missed its handler", "the platform moved to a
# screen our captions still match", "the platform escalated to a slider" and
# "15 seconds was not long enough". These pin the evidence that separates them.


def stalling_chooser() -> FakePage:
    """A chooser that never moves, with a probe-able DOM behind it."""
    page = FakePage(url=LOGIN_URL, visible={RECEIVE, SEND_YOURSELF, CODE_FIELD})
    page.dom_probe = {
        "接收短信验证码": [
            {
                "tag": "span",
                "chain": ["span", "div[role=button]", "div", "div", "body"],
                "size": [96, 22],
                "in_viewport": True,
                "disabled": False,
                "pointer_events": "none",
                "covered_by": "div[role=dialog]",
                "button_ancestor": "div[role=button]",
            }
        ]
    }
    return page


async def run_until_it_gives_up(page: FakePage):
    session = session_over(page)
    snapshot = None
    for _ in range(MAX_IDENTITY_CHALLENGE_POLLS):
        snapshot = await session.poll_status()
    assert snapshot is not None
    return snapshot


async def test_a_stall_carries_the_shape_of_the_node_we_clicked():
    """Covered? Styled unclickable? Is there a button ancestor at all?

    These are the three facts that decide whether the click was the problem,
    and none of them survives the failure — the session is torn down, so if the
    record does not carry them, nobody will ever have them.
    """
    snapshot = await run_until_it_gives_up(stalling_chooser())

    target = next(
        t
        for t in snapshot.detail[PAGE_EVIDENCE_KEY]["click_targets"]
        if t["caption"] == "接收短信验证码"
    )
    shape = target["shapes"][0]
    assert shape["covered_by"] == "div[role=dialog]"
    assert shape["pointer_events"] == "none"
    assert shape["button_ancestor"] == "div[role=button]"


async def test_an_unanswerable_chooser_records_the_page_too():
    """Both terminal reasons carry evidence, not just the stalled one.

    `unclickable` has its own open question — is the caption stale, or does
    this account really only get the manual option? — and the answer is on the
    page it gave up on.
    """
    page = FakePage(url=LOGIN_URL, visible={SEND_YOURSELF, CODE_FIELD})

    snapshot = await run_until_it_gives_up(page)

    assert snapshot.detail["reason"] == IDENTITY_CHALLENGE_UNCLICKABLE
    assert PAGE_EVIDENCE_KEY in snapshot.detail


async def test_capturing_the_page_never_replaces_the_failure():
    """Diagnostics run on a path that is already failing.

    A reader that raised here would trade a typed, explained failure for a
    driver error about the diagnostics — losing the reason *and* the evidence.
    """

    class Exploding(FakePage):
        def locator(self, selector):
            raise RuntimeError("renderer went away")

    page = Exploding(url=LOGIN_URL, visible={RECEIVE, SEND_YOURSELF})

    snapshot = await run_until_it_gives_up(page)

    assert snapshot.status is SessionStatus.FAILED
    assert snapshot.detail["reason"] == IDENTITY_CHALLENGE_STALLED
    # Something is recorded either way — "the capture itself failed" is a
    # finding, an absent key is a gap.
    assert snapshot.detail[PAGE_EVIDENCE_KEY]


async def test_page_text_is_captured_without_the_numbers_on_it():
    """The verification screen's two numbers are the two that must not be kept.

    The code that was texted and the phone it went to are the only secrets on
    that page, and this record travels: `detail` reaches the browser tab
    through `task_tracking.metadata`.
    """
    page = FakePage(
        url=LOGIN_URL,
        visible={RECEIVE, SEND_YOURSELF},
        texts={"body": "验证码 已发送至 13800001234，请输入 845213"},
    )

    snapshot = await run_until_it_gives_up(page)

    text = snapshot.detail[PAGE_EVIDENCE_KEY]["visible_text"]
    assert "13800001234" not in text
    assert "845213" not in text
    # …while the words that say which screen this was survive intact.
    assert "已发送至" in text


async def test_a_challenge_we_cannot_complete_is_named_as_such():
    """A slider is not a stall, and telling the user to rescan wastes their
    time — rescanning produces the same screen.

    The classification only ever rewrites the wording of a failure that has
    already been decided, so a stale caption here costs one sentence and can
    never block a sign-in.
    """
    page = FakePage(
        url=LOGIN_URL,
        visible={RECEIVE, SEND_YOURSELF},
        texts={"body": "请完成安全验证 拖动下方滑块完成拼图"},
    )

    snapshot = await run_until_it_gives_up(page)

    assert snapshot.status is SessionStatus.FAILED
    assert snapshot.detail["reason"] == IDENTITY_CHALLENGE_BLOCKED
    assert snapshot.detail["blocking_marker"] == "滑块"


# --- the click, hardened -----------------------------------------------------


async def test_the_click_is_followed_by_a_wait_for_the_page_to_answer():
    """Not a fixed sleep: whether the click worked is a question the page can
    be asked, and the answer is what a later failure has to report."""
    page = FakePage(url=LOGIN_URL, visible=chooser_then_code_form)

    snapshot = await session_over(page).poll_status()

    assert snapshot.status is SessionStatus.SMS_REQUIRED
    assert page.clicks == [RECEIVE, GET_CODE]


async def test_a_page_that_moves_is_never_re_clicked():
    """The escalation is for a page that did not answer. One that did must not
    get a second click — that would be a second text message."""
    page = FakePage(url=LOGIN_URL, visible=chooser_then_code_form)
    session = session_over(page)

    for _ in range(3):
        await session.poll_status()

    assert page.clicks.count(RECEIVE) == 1
    assert "escalated_click" not in (await session.poll_status()).detail


async def test_the_stall_budget_is_raised_but_still_ends():
    """~36s at the backend's 3s poll interval, and it still terminates.

    The old ceiling (5 polls, ~15s) called the login off while the user was
    still reading the screen. Raising it is the change; keeping a ceiling at
    all is the discipline — "keep polling and hope" is indistinguishable, from
    outside, from the original bug where nobody ever clicked.
    """
    backend_poll_interval_s = 3.0
    budget = MAX_IDENTITY_CHALLENGE_POLLS * backend_poll_interval_s
    assert 30 <= budget <= 60

    page = FakePage(url=LOGIN_URL, visible={RECEIVE, SEND_YOURSELF, CODE_FIELD})
    session = session_over(page)

    for _ in range(MAX_IDENTITY_CHALLENGE_POLLS - 1):
        assert (await session.poll_status()).status is SessionStatus.IDENTITY_CHALLENGE
    assert (await session.poll_status()).status is SessionStatus.FAILED
