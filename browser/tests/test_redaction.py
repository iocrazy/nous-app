import pytest

from app.redaction import redact_url_credentials, scrub, scrub_page_text

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "text,expected_absent",
    [
        ("http://alice:s3cr3t@proxy.example.com:8080", "s3cr3t"),
        ("socks5://u:p@1.2.3.4:1080 unreachable", "://u:p@"),
        (
            "net::ERR_PROXY_CONNECTION_FAILED at https://user:hunter2@gw.corp:3128/",
            "hunter2",
        ),
    ],
)
def test_credentials_are_stripped(text, expected_absent):
    assert expected_absent not in redact_url_credentials(text)
    assert "***:***@" in redact_url_credentials(text)


def test_urls_without_credentials_are_untouched():
    url = "https://creator.douyin.com/creator-micro/content/upload"
    assert redact_url_credentials(url) == url


def test_scrub_collapses_whitespace_and_truncates():
    long_text = "a" * 900
    out = scrub(long_text)
    assert len(out) <= 400
    assert scrub("line one\n\n  line two") == "line one line two"


def test_scrub_handles_empty():
    assert scrub("") == ""


# --- page text (2026-08-12) --------------------------------------------------
#
# Diagnostics captured off a login screen are a different risk from an
# exception string: the page belongs to the platform, and the two numbers a
# verification step puts on it — the code that was texted, the phone it went to
# — are the only secrets there. `scrub` alone leaves both intact.


@pytest.mark.parametrize(
    "text,secret",
    [
        ("验证码已发送至 13800001234", "13800001234"),
        ("请输入验证码 845213", "845213"),
        ("session 1234567890abcdef", "1234567890"),
    ],
)
def test_page_text_masks_the_numbers_on_a_verification_screen(text, secret):
    assert secret not in scrub_page_text(text)


def test_page_text_keeps_the_words_that_say_which_screen_it_was():
    out = scrub_page_text("身份验证 为保障账号安全，请先完成身份验证 接收短信验证码")
    assert "身份验证" in out
    assert "接收短信验证码" in out


def test_short_numbers_survive_because_they_are_diagnostics():
    # A countdown, a step counter, a truncated year. Masking these would cost
    # meaning and protect nothing — the threshold sits below a 6-digit code.
    assert scrub_page_text("重新获取 60s 第 2 步") == "重新获取 60s 第 2 步"


def test_masking_happens_before_truncation():
    """Truncating first can cut a phone number in half and leave a four-digit
    tail below the mask threshold — i.e. an unmasked fragment of the thing the
    mask exists for."""
    text = "x" * 595 + "13800001234"
    assert "1380" not in scrub_page_text(text)


def test_page_text_handles_empty():
    assert scrub_page_text("") == ""
