"""Signing in on a platform that has no QR code at all.

Every login test that existed before this file agreed with one assumption — that
a login *is* a scan — because every fake spec declared QR selectors. Xiaohongshu
is the platform that breaks it: its creator platform ships 手机号 + 验证码 and
no scan sign-in whatsoever ([实测 2026-08-08], recorded in
`app/platforms/xiaohongshu.py`). The assumption was not just cosmetic; it was
load-bearing in three places, and all three are covered here:

1. `LoginSessionRegistry.start` failed the whole bind with "login page rendered
   no QR code" before any judgement ran, so the platform's own SMS judge was
   *never once executed in production*.
2. `LoginSession` opened at `waiting_scan` regardless, so even a fixed start
   would have shown a scan prompt as its first frame.
3. Nothing could type a phone number, so the code screen the judge reports was
   a dead end: no number, no request, no text message.

The tests are written so that reverting any one of the three turns one of them
red on its own.
"""

import dataclasses

import pytest

from app import capabilities
from app.login_sessions import (
    CODE_REQUEST_FAILED,
    PHONE_INPUT_MISSING,
    PHONE_SUBMITTED_KEY,
    LoginError,
    LoginSessionRegistry,
)
from app.platforms import get_login_flow, login_platforms
from app.schemas import SessionStatus
from tests.fakes import FakeDriver, always, driver_factory, make_spec

pytestmark = pytest.mark.unit


def sms_spec(judge=None, **kwargs):
    """A platform shaped like Xiaohongshu: no code to scan, a number to type."""
    return make_spec(
        judge=judge or always(SessionStatus.SMS_REQUIRED),
        platform="smsplatform",
        qrcode_selectors=(),
        phone_input_selectors=('input[placeholder="手机号"]',),
        sms_request_texts=("发送验证码",),
        **kwargs,
    )


def registry_for(driver):
    return LoginSessionRegistry(open_driver=driver_factory(driver))


# --- the capability declaration --------------------------------------------


def test_every_platform_declares_the_login_method_its_spec_actually_implements():
    """`capabilities.PLATFORM_LOGIN_METHODS` vs each registered `LoginFlowSpec`.

    The table is read by the backend (and through it, by the UI) to decide which
    sign-in to draw. It cannot import this module's Playwright-dependent
    neighbours, so it restates a fact that lives in the specs — and a restated
    fact is one that can go stale. This is the pin that keeps it from doing so.

    Note which direction the fix goes when this fails: the spec is the truth
    (its selectors are what the browser actually looks for), so the table gets
    corrected, never the other way round.
    """
    registered = set(login_platforms())
    assert registered, "no login flows registered — this guard would be vacuous"
    assert set(capabilities.PLATFORM_LOGIN_METHODS) == registered, (
        "PLATFORM_LOGIN_METHODS and the registered login flows have drifted apart"
    )

    for platform in sorted(registered):
        spec = get_login_flow(platform)
        assert spec is not None
        assert capabilities.login_method_for(platform) == spec.login_method, (
            f"{platform} declares "
            f"{capabilities.login_method_for(platform)!r} but its spec implements "
            f"{spec.login_method!r}"
        )


def test_the_login_method_guard_would_fail_on_a_stale_declaration():
    """Reverse verification: prove the pin above can go red.

    The realistic drift is exactly what happened to Xiaohongshu — a platform
    turns out to have no QR sign-in, its `QRCODE_SELECTORS` is emptied, and a
    declaration elsewhere keeps saying "qrcode" while a whole UI promises a code
    nothing will ever render. Simulated by emptying a QR platform's selectors
    and asserting the derived method stops matching the table.
    """
    qr_platforms = [
        p for p in login_platforms() if capabilities.login_method_for(p) == "qrcode"
    ]
    assert qr_platforms, "no QR platform left to build the counter-example from"

    platform = qr_platforms[0]
    spec = get_login_flow(platform)
    assert spec.login_method == "qrcode"

    stripped = dataclasses.replace(spec, qrcode_selectors=())
    assert stripped.login_method == "sms"
    assert stripped.login_method != capabilities.login_method_for(platform)


def test_xiaohongshu_is_the_sms_platform_and_asks_for_a_phone_number():
    """The concrete claim this whole change rests on, asserted on the real spec."""
    spec = get_login_flow("xiaohongshu")
    assert spec is not None
    assert spec.qrcode_selectors == ()
    assert spec.login_method == "sms"
    assert spec.requires_phone_number
    assert capabilities.login_method_for("xiaohongshu") == "sms"
    # The control that actually asks the platform to send a text. Without it the
    # number would go into the page and nothing would happen.
    assert spec.sms_request_texts


# --- start ------------------------------------------------------------------


async def test_an_sms_platform_starts_instead_of_failing_for_a_missing_qr_code():
    """The bug, at its origin. `start` used to raise here, every single time."""
    spec = sms_spec()
    driver = FakeDriver(spec, qrcode=None)
    registry = registry_for(driver)

    session = await registry.start(spec, None)

    assert session.is_active
    assert session.qrcode_data_url is None
    assert session.status is SessionStatus.PHONE_REQUIRED
    assert driver.closed is False
    # Nothing was read off the page for a code that does not exist: the poll in
    # `read_qrcode` sleeps between attempts, so skipping it is also the
    # difference between a prompt bind and a several-second stall.
    assert driver.read_qrcode_calls == 0


async def test_a_qr_platform_with_no_code_still_fails(caplog):
    """Reverse verification for the fix above.

    The change must not become "never mind if there is no QR code". A platform
    that *declares* a scan sign-in and then renders nothing has broken selectors
    or a page that never loaded, and improvising a different sign-in for it
    would hide that.
    """
    spec = make_spec()  # QR selectors declared
    driver = FakeDriver(spec, qrcode=None)
    registry = registry_for(driver)

    with pytest.raises(LoginError) as exc:
        await registry.start(spec, None)

    assert exc.value.status is SessionStatus.FAILED
    assert "no QR code" in exc.value.message
    assert driver.closed is True


# --- the phone-number step --------------------------------------------------


async def test_polling_asks_for_the_phone_number_before_it_asks_for_a_code():
    """The judge says `sms_required` from the first poll — truthfully.

    Xiaohongshu renders the phone field and the code field on one form, so
    "there is a code input on screen" is true before anybody has entered
    anything. Reporting it as `sms_required` would tell the user to type a code
    that cannot exist. The session knows what the page cannot: nobody has given
    us a number yet.
    """
    spec = sms_spec()
    driver = FakeDriver(spec, qrcode=None)
    registry = registry_for(driver)
    session = await registry.start(spec, None)

    snapshot = await session.poll_status()

    assert snapshot.status is SessionStatus.PHONE_REQUIRED
    assert snapshot.qrcode_data_url is None
    # And critically: nothing pressed "send me a code" on the user's behalf.
    # Pressing it with an empty number sends no text but *does* latch
    # `code_requested`, which is the UI's licence to say a message is on its way
    # — the same false claim the Douyin identity chooser made (2026-08-11).
    assert driver.code_request_clicks == 0
    assert snapshot.detail.get("code_requested") is None


async def test_submitting_the_number_requests_a_code_and_moves_on():
    spec = sms_spec()
    driver = FakeDriver(spec, qrcode=None, code_request_text="发送验证码")
    registry = registry_for(driver)
    session = await registry.start(spec, None)

    snapshot = await session.submit_phone("13800000000")

    assert driver.filled_phones == ["13800000000"]
    assert driver.code_request_clicks == 1
    assert snapshot.status is SessionStatus.SMS_REQUIRED
    assert snapshot.detail[PHONE_SUBMITTED_KEY] is True
    # Latched from the click landing, so the UI may now say a text was asked for.
    assert snapshot.detail["code_requested"] is True

    # And the gate opens: further polls report the code step rather than looping
    # back to the number.
    assert (await session.poll_status()).status is SessionStatus.SMS_REQUIRED


async def test_the_code_is_requested_once_no_matter_how_many_polls_follow():
    """Each press is a real text message to a real phone."""
    spec = sms_spec()
    driver = FakeDriver(spec, qrcode=None, code_request_text="发送验证码")
    registry = registry_for(driver)
    session = await registry.start(spec, None)

    await session.submit_phone("13800000000")
    await session.poll_status()
    await session.poll_status()

    assert driver.code_request_clicks == 1


async def test_a_missing_phone_field_is_reported_not_waited_out():
    """Every selector on this platform is unverified against a completed bind.

    So the one thing a wrong guess may not do is look like patience. The user
    gets a typed reason and keeps the form; the session stays alive so they can
    retry inside the same TTL.
    """
    spec = sms_spec()
    driver = FakeDriver(spec, qrcode=None, phone_input_present=False)
    registry = registry_for(driver)
    session = await registry.start(spec, None)

    snapshot = await session.submit_phone("13800000000")

    assert snapshot.status is SessionStatus.PHONE_REQUIRED
    assert snapshot.detail["reason"] == PHONE_INPUT_MISSING
    assert snapshot.detail["terminal"] is False
    # Nothing was pressed: there is no point asking the platform to text a
    # number it was never given, and a press that lands would latch
    # `code_requested` on a login that has none.
    assert driver.code_request_clicks == 0
    assert session.is_active


async def test_a_number_that_could_not_be_submitted_never_claims_a_code_was_sent():
    """The number went in, the platform's own button did not respond.

    The failure that matters is the one this rules out: reporting `sms_required`
    here would put a code field in front of someone whose phone will never ring.
    """
    spec = sms_spec()
    driver = FakeDriver(spec, qrcode=None, code_request_text=None)
    registry = registry_for(driver)
    session = await registry.start(spec, None)

    snapshot = await session.submit_phone("13800000000")

    assert snapshot.status is SessionStatus.PHONE_REQUIRED
    assert snapshot.detail["reason"] == CODE_REQUEST_FAILED
    assert snapshot.detail.get("code_requested") is None
    assert session.is_active


async def test_a_released_session_answers_the_phone_form_instead_of_vanishing():
    spec = sms_spec()
    driver = FakeDriver(spec, qrcode=None)
    registry = registry_for(driver)
    session = await registry.start(spec, None)
    await session.release(SessionStatus.TIMEOUT, "login session expired")

    snapshot = await session.submit_phone("13800000000")

    assert snapshot.status is SessionStatus.TIMEOUT
    assert snapshot.detail["released"] is True
    assert snapshot.detail["terminal"] is True


# --- QR platforms are untouched ---------------------------------------------


async def test_a_qr_platform_never_enters_the_phone_state():
    """The gate is keyed on the platform declaring a phone field, not on status.

    Douyin reaches `sms_required` through its identity chooser, where the code
    *has* been requested on the user's behalf — inserting a "type your number"
    step there would be a regression, so the gate must not fire.
    """
    spec = make_spec(judge=always(SessionStatus.SMS_REQUIRED))
    assert not spec.requires_phone_number
    driver = FakeDriver(spec, code_request_text="获取验证码")
    registry = registry_for(driver)
    session = await registry.start(spec, None)

    snapshot = await session.poll_status()

    assert snapshot.status is SessionStatus.SMS_REQUIRED
    assert driver.code_request_clicks == 1
