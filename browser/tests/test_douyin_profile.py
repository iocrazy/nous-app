"""Profile parsing: pure, and best-effort by contract.

A console redesign that breaks a display-name selector must produce a nameless
account, never fail a login the user already completed successfully.
"""

import pytest

from app.platforms.douyin import parse_douyin_profile

pytestmark = pytest.mark.unit


def test_scraped_fields_are_used_as_is():
    profile = parse_douyin_profile(
        {
            "username": "  Test Creator  ",
            "platform_user_id": "抖音号：test_creator_01",
            "avatar_url": "https://p.example.com/avatar.jpg",
        },
        [],
    )
    assert profile.username == "Test Creator"
    assert profile.platform_user_id == "test_creator_01"
    assert profile.avatar_url == "https://p.example.com/avatar.jpg"


@pytest.mark.parametrize("prefix", ["抖音号：", "抖音号:", "抖音号", "ID：", "ID:"])
def test_id_label_prefixes_are_stripped(prefix):
    profile = parse_douyin_profile({"platform_user_id": prefix + "abc123"}, [])
    assert profile.platform_user_id == "abc123"


def test_cookie_backs_up_a_missing_scraped_id():
    """The identifier is what the backend keys the account on, so it must not
    depend on a header layout that Douyin is free to change."""
    profile = parse_douyin_profile(
        {"username": "Test Creator"},
        [{"name": "uid_tt", "value": "hashed-user-id"}, {"name": "sessionid", "value": "x"}],
    )
    assert profile.platform_user_id == "hashed-user-id"


def test_scraped_id_wins_over_the_cookie():
    profile = parse_douyin_profile(
        {"platform_user_id": "抖音号：readable_id"},
        [{"name": "uid_tt", "value": "hashed-user-id"}],
    )
    assert profile.platform_user_id == "readable_id"


def test_everything_missing_yields_empty_fields_not_an_error():
    profile = parse_douyin_profile({}, [])
    assert profile.platform_user_id == ""
    assert profile.username == ""
    assert profile.avatar_url is None


@pytest.mark.parametrize("avatar", ["//p.example.com/a.jpg", "/static/a.jpg", "a.jpg", ""])
def test_unusable_avatar_values_become_none(avatar):
    """A relative or protocol-less src renders as a broken image in the UI,
    which is worse than showing no avatar at all."""
    assert parse_douyin_profile({"avatar_url": avatar}, []).avatar_url is None


def test_data_uri_avatar_is_kept():
    profile = parse_douyin_profile({"avatar_url": "data:image/png;base64,AAA"}, [])
    assert profile.avatar_url == "data:image/png;base64,AAA"


def test_cookies_without_names_do_not_crash_the_lookup():
    profile = parse_douyin_profile({}, [{"value": "orphan"}, {"name": "uid_tt_ss", "value": "ss-id"}])
    assert profile.platform_user_id == "ss-id"
