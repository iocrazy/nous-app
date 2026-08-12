"""The login judge decides what the user is told to do next, so it gets a table.

Every wrong verdict here has a user-visible cost: a false `success` persists
cookies that do not work, a false `qrcode_expired` clicks "refresh" out from
under someone mid-2FA, a false `waiting_scan` leaves them staring at a dead
image. It is pure - `LoginPageSnapshot -> LoginJudgement` - precisely so all of
that is testable without a browser or a Douyin account.
"""

import pytest

from app.login import LoginPageSnapshot
from app.platforms.douyin import judge_douyin_login
from app.schemas import SessionStatus

pytestmark = pytest.mark.unit

LOGIN_PAGE = "https://creator.douyin.com/"
CONSOLE = "https://creator.douyin.com/creator-micro/home"
UPLOAD = "https://creator.douyin.com/creator-micro/content/upload"


def snap(url=LOGIN_PAGE, **kwargs):
    return LoginPageSnapshot(url=url, **kwargs)


# --- waiting ---------------------------------------------------------------


def test_qrcode_on_the_login_page_is_waiting_scan():
    verdict = judge_douyin_login(snap(qrcode_visible=True, login_texts=("扫码登录",)))
    assert verdict.status is SessionStatus.WAITING_SCAN


def test_the_real_login_page_is_waiting_scan_despite_a_visible_code_field():
    """Regression guard, and the reason this suite exists.

    Measured against the live page: Douyin renders the phone-login form beside
    the QR panel, so `input[placeholder*="验证码"]` is visible the whole time
    the code is on display. An earlier draft judged that as `sms_required` and
    reported it on *every* poll of a completely normal scan - the user would be
    asked for a text message that was never sent.
    """
    verdict = judge_douyin_login(
        snap(qrcode_visible=True, login_texts=("扫码登录",), sms_input_visible=True)
    )
    assert verdict.status is SessionStatus.WAITING_SCAN


def test_a_code_field_counts_once_the_qr_panel_is_gone():
    """The platform swaps the login card out for the verification step rather
    than showing both, which is what makes the QR code a usable gate."""
    verdict = judge_douyin_login(snap(sms_input_visible=True, qrcode_visible=False))
    assert verdict.status is SessionStatus.SMS_REQUIRED


# --- identity challenge ----------------------------------------------------


def test_the_identity_chooser_outranks_the_code_field_it_renders():
    """The 2026-08-11 bug, as one assertion.

    That screen carries a code field of its own, so on the old ordering it read
    as `sms_required` — and the UI then told the user a code had been texted to
    them while the platform was still waiting for them to pick *how* to verify.
    Nothing had been sent, so nothing ever arrived.
    """
    verdict = judge_douyin_login(
        snap(
            sms_input_visible=True,
            qrcode_visible=False,
            identity_challenge_texts=("接收短信验证码", "发送短信验证"),
        )
    )
    assert verdict.status is SessionStatus.IDENTITY_CHALLENGE


def test_only_the_manual_option_is_still_an_identity_challenge():
    """An account offered only 发送短信验证 (the user texts the platform).

    We cannot complete it, but calling the screen something else would hide it:
    the flow needs to reach its typed "could not answer this" failure, not
    report a code prompt.
    """
    verdict = judge_douyin_login(
        snap(sms_input_visible=True, identity_challenge_texts=("发送短信验证",))
    )
    assert verdict.status is SessionStatus.IDENTITY_CHALLENGE
    assert "发送短信验证" in verdict.reason


def test_an_unanswered_identity_challenge_is_not_a_finished_login():
    """Same reasoning as the code field: a console URL with a verification step
    still on it is a half-finished login, and persisting its cookies creates an
    account row that never works."""
    verdict = judge_douyin_login(
        snap(url=CONSOLE, identity_challenge_texts=("接收短信验证码",))
    )
    assert verdict.status is SessionStatus.IDENTITY_CHALLENGE


def test_scan_confirmation_prompt_is_scanned_not_waiting():
    """The most confusing moment of the flow for a user: the code is scanned,
    the phone is asking for confirmation, and the QR image on screen has not
    changed. Reporting `waiting_scan` here makes the UI look stuck."""
    verdict = judge_douyin_login(
        snap(qrcode_visible=True, scanned_texts=("请在手机上确认",))
    )
    assert verdict.status is SessionStatus.SCANNED
    assert "确认" in verdict.reason


# --- success ---------------------------------------------------------------


@pytest.mark.parametrize("url", [CONSOLE, UPLOAD, CONSOLE + "?tab=1"])
def test_console_page_with_no_login_prompt_is_success(url):
    assert judge_douyin_login(snap(url=url)).status is SessionStatus.SUCCESS


def test_login_page_root_is_never_success():
    """The login screen lives at the site root; the console lives under
    /creator-micro. That path split is the only thing separating them."""
    assert judge_douyin_login(snap(url=LOGIN_PAGE)).status is not SessionStatus.SUCCESS


def test_console_url_with_a_visible_login_prompt_is_not_success():
    """A logged-out shell can render the console URL while the login card is
    still up. The URL alone is not evidence."""
    verdict = judge_douyin_login(snap(url=CONSOLE, login_texts=("扫码登录",), qrcode_visible=True))
    assert verdict.status is SessionStatus.WAITING_SCAN


def test_console_url_still_asking_for_a_code_is_not_success():
    """Half-finished 2FA on a console URL. Calling this success hands the
    backend cookies that fail on first use, with nothing to point at."""
    verdict = judge_douyin_login(snap(url=CONSOLE, sms_input_visible=True))
    assert verdict.status is SessionStatus.SMS_REQUIRED


# --- expiry / 2FA ordering -------------------------------------------------


def test_expired_caption_is_qrcode_expired():
    verdict = judge_douyin_login(snap(expired_texts=("二维码失效",), login_texts=("扫码登录",)))
    assert verdict.status is SessionStatus.QRCODE_EXPIRED
    assert "二维码失效" in verdict.reason


def test_sms_outranks_an_expired_code():
    """Ordering guard. A consumed QR code shows the expired caption while the
    platform is asking for a text message - and the expired branch *clicks
    refresh*, which would tear down the 2FA the user is halfway through."""
    verdict = judge_douyin_login(
        snap(expired_texts=("二维码失效",), sms_input_visible=True)
    )
    assert verdict.status is SessionStatus.SMS_REQUIRED


def test_expired_outranks_scanned():
    verdict = judge_douyin_login(
        snap(expired_texts=("二维码失效",), scanned_texts=("扫码成功",))
    )
    assert verdict.status is SessionStatus.QRCODE_EXPIRED


# --- fallbacks -------------------------------------------------------------


def test_unrecognisable_page_on_the_platform_keeps_waiting_but_says_so():
    """Usually a mid-render frame. It must keep the caller polling, and the
    reason must name the situation - a generic message here is what makes a
    stale selector list look like a patient user."""
    verdict = judge_douyin_login(snap(url=LOGIN_PAGE))
    assert verdict.status is SessionStatus.WAITING_SCAN
    assert "no recognisable login state" in verdict.reason


def test_navigating_off_the_platform_is_failed():
    verdict = judge_douyin_login(snap(url="https://evil.example.com/creator-micro/home"))
    assert verdict.status is SessionStatus.FAILED
    assert "unexpected host" in verdict.reason


@pytest.mark.parametrize("url", ["", "not-a-url", "://broken"])
def test_unparseable_urls_are_judged_not_crashed(url):
    assert judge_douyin_login(snap(url=url)).status is SessionStatus.FAILED


def test_host_match_is_case_insensitive():
    verdict = judge_douyin_login(snap(url="https://CREATOR.DOUYIN.COM/creator-micro/home"))
    assert verdict.status is SessionStatus.SUCCESS


def test_every_verdict_is_a_member_of_the_shared_enum():
    """No platform-specific status may escape (design doc 6.1a)."""
    cases = [
        snap(),
        snap(url=CONSOLE),
        snap(qrcode_visible=True),
        snap(scanned_texts=("扫码成功",)),
        snap(expired_texts=("二维码失效",)),
        snap(sms_input_visible=True),
        snap(url="https://example.com/"),
    ]
    for case in cases:
        assert judge_douyin_login(case).status in set(SessionStatus)
