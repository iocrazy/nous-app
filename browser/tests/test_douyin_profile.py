"""Profile parsing: pure, and split down the middle.

Display fields (username / avatar / 抖音号) are best-effort by contract: a
console redesign that breaks a selector must produce a nameless account, never
fail a login the user already completed.

The identity key is not one of them, and `parse_douyin_profile` cannot even see
it — it takes no cookies and returns no `platform_user_id`. That half is
covered by `test_identity_single_source.py`.
"""

import pytest

from app.platforms.douyin import parse_douyin_profile

pytestmark = pytest.mark.unit


def test_scraped_fields_are_used_as_is():
    profile = parse_douyin_profile(
        {
            "username": "  Test Creator  ",
            "platform_handle": "抖音号：test_creator_01",
            "avatar_url": "https://p.example.com/avatar.jpg",
        }
    )
    assert profile.username == "Test Creator"
    assert profile.platform_handle == "test_creator_01"
    assert profile.avatar_url == "https://p.example.com/avatar.jpg"


def test_the_scraped_handle_never_becomes_the_identity_key():
    """The regression this whole change exists for.

    On 2026-08-09 a scraped 抖音号 (`miopoo`) landed in `platform_user_id` and
    the backend upserted it as a *second* account next to the same user's
    cookie-keyed row (`41cf16…`), splitting the publish history.
    """
    profile = parse_douyin_profile({"platform_handle": "抖音号：miopoo"})
    assert profile.platform_handle == "miopoo"
    assert profile.platform_user_id == ""


@pytest.mark.parametrize("prefix", ["抖音号：", "抖音号:", "抖音号", "ID：", "ID:"])
def test_handle_label_prefixes_are_stripped(prefix):
    profile = parse_douyin_profile({"platform_handle": prefix + "abc123"})
    assert profile.platform_handle == "abc123"


def test_everything_missing_yields_empty_fields_not_an_error():
    profile = parse_douyin_profile({})
    assert profile.platform_user_id == ""
    assert profile.platform_handle == ""
    assert profile.username == ""
    assert profile.avatar_url is None


@pytest.mark.parametrize("avatar", ["//p.example.com/a.jpg", "/static/a.jpg", "a.jpg", ""])
def test_unusable_avatar_values_become_none(avatar):
    """A relative or protocol-less src renders as a broken image in the UI,
    which is worse than showing no avatar at all."""
    assert parse_douyin_profile({"avatar_url": avatar}).avatar_url is None


def test_data_uri_avatar_is_kept():
    profile = parse_douyin_profile({"avatar_url": "data:image/png;base64,AAA"})
    assert profile.avatar_url == "data:image/png;base64,AAA"
