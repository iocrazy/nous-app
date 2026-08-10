"""`platform_user_id` has exactly one source per platform, and it is a cookie.

The bug this guards (2026-08-09): Douyin resolved its identity from the DOM
first (`[class^="unique_id-"]`, the 抖音号) and only fell back to the `uid_tt`
cookie when that selector missed. The two live in different namespaces, so which
one won decided *which account row the backend upserted into*:

    08-06  MioPoo  platform_user_id = 41cf16775ee3e9fdf5e021f9c1ddfc12  (cookie)
    08-09  MioPoo  platform_user_id = miopoo                            (DOM)

One account, two rows, ten publish records stranded on the first.

A test that only asserted "the cookie is used when the DOM misses" would have
passed against the broken code — that behaviour was never in doubt. So these
assert the two things that actually changed: the DOM has **no** path to the
identity key at all, and an unresolvable identity **fails** instead of
degrading.
"""

from typing import Any

import pytest

from app.login import (
    IdentityUnresolved,
    LoginDriver,
    LoginProfile,
    identity_from_cookies,
    read_profile_from_page,
)
from app.platforms import get_login_flow, login_platforms
from app.platforms.bilibili import LOGIN_SPEC as BILIBILI_SPEC
from app.platforms.douyin import LOGIN_SPEC as DOUYIN_SPEC

pytestmark = pytest.mark.unit


# --- fake page --------------------------------------------------------------


class _Locator:
    def __init__(self, text: str | None = None, attrs: dict[str, str] | None = None):
        self._text = text
        self._attrs = attrs or {}

    @property
    def first(self) -> "_Locator":
        return self

    async def count(self) -> int:
        return 1

    async def inner_text(self) -> str | None:
        return self._text

    async def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)


class _MissingLocator(_Locator):
    async def count(self) -> int:
        return 0


class _FakePage:
    """Answers a fixed selector -> text/attribute map, misses everything else."""

    def __init__(self, by_selector: dict[str, _Locator]):
        self._by_selector = by_selector
        self.url = "https://example.test/home"

    async def goto(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def wait_for_timeout(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def locator(self, selector: str) -> _Locator:
        return self._by_selector.get(selector, _MissingLocator())


class _FakeContext:
    def __init__(self, cookies: list[dict[str, Any]]):
        self._cookies = cookies

    async def cookies(self) -> list[dict[str, Any]]:
        return list(self._cookies)


def _douyin_page_showing_a_handle() -> _FakePage:
    """A live-looking creator home: the 抖音号 selector hits, as it did on 08-09."""
    return _FakePage(
        {
            '[class^="unique_id-"]': _Locator(text="抖音号：miopoo"),
            '[class^="header-"] [class^="name-"]': _Locator(text="MioPoo"),
        }
    )


# --- the structural guard ---------------------------------------------------


def test_every_registered_platform_declares_exactly_one_identity_cookie():
    assert login_platforms(), "no login flows registered - the guard would be vacuous"
    for platform in login_platforms():
        spec = get_login_flow(platform)
        assert spec is not None
        assert isinstance(spec.identity_cookie, str)
        assert spec.identity_cookie.strip(), (
            f"{platform} declares no identity cookie; a platform without a single "
            "authoritative id source is how one account becomes two rows"
        )


def test_no_platform_can_scrape_the_identity_key_off_the_page():
    """The DOM must not be *able* to name the identity field.

    Structural rather than behavioural on purpose: adding
    `"platform_user_id": (...)` back to a selector map is a one-line change that
    reads as harmless, and this is the line that stops it.
    """
    for platform in login_platforms():
        spec = get_login_flow(platform)
        assert "platform_user_id" not in spec.profile_text_selectors, platform
        assert "platform_user_id" not in spec.profile_attr_selectors, platform


def test_parse_profile_cannot_contribute_an_identity():
    """Even handed a field named `platform_user_id`, no platform returns one."""
    hostile = {
        "platform_user_id": "scraped-id",
        "platform_handle": "scraped-handle",
        "username": "Test Creator",
    }
    for platform in login_platforms():
        spec = get_login_flow(platform)
        assert spec.parse_profile(hostile).platform_user_id == "", platform


@pytest.mark.parametrize(
    "spec,cookie",
    [(DOUYIN_SPEC, "uid_tt"), (BILIBILI_SPEC, "DedeUserID")],
    ids=["douyin", "bilibili"],
)
def test_the_pinned_identity_cookie_per_platform(spec, cookie):
    """Verified against live sessions; changing one is a data migration, not a
    refactor, so the value is pinned here rather than merely non-empty."""
    assert spec.identity_cookie == cookie


# --- behaviour --------------------------------------------------------------


async def test_the_cookie_wins_even_when_the_dom_offers_an_id():
    """The reverse-verifiable one: restore the DOM-first path and this fails."""
    profile = await read_profile_from_page(
        _douyin_page_showing_a_handle(),
        DOUYIN_SPEC,
        [{"name": "uid_tt", "value": "41cf16775ee3e9fdf5e021f9c1ddfc12"}],
    )
    assert profile.platform_user_id == "41cf16775ee3e9fdf5e021f9c1ddfc12"
    # ...and the scraped 抖音号 is kept, just not as the key.
    assert profile.platform_handle == "miopoo"
    assert profile.username == "MioPoo"


async def test_a_missing_identity_cookie_yields_no_id_rather_than_the_handle():
    profile = await read_profile_from_page(
        _douyin_page_showing_a_handle(),
        DOUYIN_SPEC,
        [{"name": "sessionid", "value": "irrelevant"}],
    )
    assert profile.platform_user_id == ""
    assert profile.platform_handle == "miopoo"


def test_the_secure_variant_is_not_a_second_source():
    """`uid_tt_ss` carries the same id under the secure cookie, which is exactly
    why it is tempting to keep as a fallback. Two sources cannot be verified to
    agree on a page we do not control, so there is one."""
    assert identity_from_cookies("uid_tt", [{"name": "uid_tt_ss", "value": "ss"}]) == ""
    assert identity_from_cookies("uid_tt", [{"name": "uid_tt", "value": "v"}]) == "v"


def test_blank_and_nameless_cookies_do_not_resolve_an_identity():
    cookies = [{"value": "orphan"}, {"name": "uid_tt", "value": "   "}]
    assert identity_from_cookies("uid_tt", cookies) == ""


# --- the login boundary turns "no identity" into a typed failure ------------


def _driver(page: _FakePage, cookies: list[dict[str, Any]]) -> LoginDriver:
    return LoginDriver(DOUYIN_SPEC, None, None, _FakeContext(cookies), page)


async def test_login_read_profile_raises_when_the_identity_is_unresolved():
    with pytest.raises(IdentityUnresolved) as exc:
        await _driver(_douyin_page_showing_a_handle(), []).read_profile()
    assert exc.value.cookie == "uid_tt"
    assert exc.value.platform == "douyin"
    assert IdentityUnresolved.reason == "identity_unresolved"


async def test_login_read_profile_returns_the_cookie_identity():
    profile = await _driver(
        _douyin_page_showing_a_handle(), [{"name": "uid_tt", "value": "abc"}]
    ).read_profile()
    assert profile.platform_user_id == "abc"


async def test_a_scrape_that_blows_up_still_identifies_the_account(monkeypatch):
    """Display scraping is allowed to fail; identity is not, and it owes nothing
    to the DOM — so even a scrape that raises must still bind the right account
    rather than degrade to a nameless *new* one."""
    import app.login

    async def boom(_page, _spec, _cookies):
        raise RuntimeError("the console moved everything again")

    monkeypatch.setattr(app.login, "read_profile_from_page", boom)

    profile = await _driver(
        _FakePage({}), [{"name": "uid_tt", "value": "abc"}]
    ).read_profile()
    assert profile == LoginProfile(platform_user_id="abc")


async def test_a_scrape_that_blows_up_with_no_cookie_is_still_a_typed_failure(
    monkeypatch,
):
    import app.login

    async def boom(_page, _spec, _cookies):
        raise RuntimeError("the console moved everything again")

    monkeypatch.setattr(app.login, "read_profile_from_page", boom)

    with pytest.raises(IdentityUnresolved):
        await _driver(_FakePage({}), []).read_profile()
