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
    IDENTITY_CHALLENGE_STALLED,
    IDENTITY_CHALLENGE_UNCLICKABLE,
    MAX_IDENTITY_CHALLENGE_POLLS,
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
    """The post-click settle is a real 3s in production. Not here."""
    monkeypatch.setenv("BROWSER_LOGIN_SMS_SETTLE_S", "0")
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
    # Still exactly one click, even across five polls of a stuck screen.
    assert page.clicks.count(RECEIVE) == 1


async def test_the_qr_code_is_dropped_while_the_challenge_is_up():
    """Nothing on that screen is scannable; showing the old image invites a
    scan that cannot help."""
    page = FakePage(url=LOGIN_URL, visible={SEND_YOURSELF, CODE_FIELD})
    session = session_over(page)

    snapshot = await session.poll_status()

    assert snapshot.status is SessionStatus.IDENTITY_CHALLENGE
    assert snapshot.qrcode_data_url is None
