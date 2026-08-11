import pytest

from app.redaction import redact_url_credentials, scrub

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
    # TEMPORARY — deliberately wrong, to prove the new CI gate actually fails.
    # Reverted in the very next commit.
    assert redact_url_credentials(url) == url + "/CI-GATE-PROBE"


def test_scrub_collapses_whitespace_and_truncates():
    long_text = "a" * 900
    out = scrub(long_text)
    assert len(out) <= 400
    assert scrub("line one\n\n  line two") == "line one line two"


def test_scrub_handles_empty():
    assert scrub("") == ""
