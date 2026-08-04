"""The judgement rule is the highest-risk piece of the whole service.

A false "invalid" tells a user to re-scan a QR code for a healthy account; a
false "valid" makes publishing fail later with a confusing DOM error. Both are
decided by one pure function, so it gets a table.
"""

import pytest

from app.platforms.douyin import judge_douyin_session

pytestmark = pytest.mark.unit

UPLOAD = "https://creator.douyin.com/creator-micro/content/upload"


@pytest.mark.parametrize(
    "url",
    [
        UPLOAD,
        UPLOAD + "/",
        UPLOAD + "?enter_from=publish_page",
        UPLOAD + "#step=1",
        # Gray-released second variant of the publish page.
        "https://creator.douyin.com/creator-micro/content/upload/version_2",
    ],
)
def test_upload_page_without_login_text_is_valid(url):
    verdict = judge_douyin_session(url, [])
    assert verdict.valid is True


@pytest.mark.parametrize("marker", ["手机号登录", "扫码登录", "二维码失效"])
def test_visible_login_text_invalidates_even_on_the_upload_path(marker):
    verdict = judge_douyin_session(UPLOAD, [marker])
    assert verdict.valid is False
    assert marker in verdict.reason


def test_login_redirect_carrying_the_path_in_a_query_param_is_invalid():
    """Regression guard for the substring-matching trap.

    The logged-out redirect preserves the upload path inside `redirect_url`, so
    a naive `"content/upload" in url` check reports a dead session as valid.
    Judgement must look at the parsed path only.
    """
    url = (
        "https://creator.douyin.com/"
        "?redirect_url=https%3A%2F%2Fcreator.douyin.com%2Fcreator-micro%2Fcontent%2Fupload"
    )
    verdict = judge_douyin_session(url, [])
    assert verdict.valid is False
    assert "upload page" in verdict.reason


def test_login_landing_page_is_invalid():
    assert judge_douyin_session("https://creator.douyin.com/", []).valid is False


def test_home_page_after_redirect_is_invalid():
    verdict = judge_douyin_session(
        "https://creator.douyin.com/creator-micro/home", []
    )
    assert verdict.valid is False
    assert "/creator-micro/home" in verdict.reason


def test_foreign_host_is_invalid_even_with_a_matching_path():
    verdict = judge_douyin_session(
        "https://evil.example.com/creator-micro/content/upload", []
    )
    assert verdict.valid is False
    assert "creator host" in verdict.reason


@pytest.mark.parametrize("url", ["", "not-a-url", "://broken"])
def test_unparseable_urls_are_invalid_not_crashes(url):
    assert judge_douyin_session(url, []).valid is False


def test_host_match_is_case_insensitive():
    assert judge_douyin_session(
        "https://CREATOR.DOUYIN.COM/creator-micro/content/upload", []
    ).valid is True
