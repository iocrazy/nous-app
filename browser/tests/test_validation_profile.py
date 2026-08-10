"""Profile scraping during session validation.

The DOM-level scrape is covered by test_douyin_profile.py. What matters here is
the wrapper's contract, and one clause of it above all:

    **A failed profile scrape must never change a validation verdict.**

Profile selectors are CSS-Modules hashes that move on every console deploy
(see the douyin platform module). If a stale selector could turn a live session
into `session_invalid`, a console redesign would silently log every account out
and send users off re-scanning QR codes to fix a cosmetic problem.
"""

from typing import Any

import pytest

from app import validation
from app.login import LoginProfile

pytestmark = pytest.mark.unit


class _FakeContext:
    def __init__(self, cookies: list[dict[str, Any]] | None = None, boom: bool = False):
        self._cookies = cookies or []
        self._boom = boom

    async def cookies(self) -> list[dict[str, Any]]:
        if self._boom:
            raise RuntimeError("context is gone")
        return self._cookies


def _stub_scrape(monkeypatch, result):
    """Replace the DOM scrape; `result` is a LoginProfile or an exception."""

    async def fake(page, spec, cookies):
        if isinstance(result, BaseException):
            raise result
        return result

    # Patched where it is looked up: the helper imports it lazily inside the
    # function body, so the name must be replaced on the defining module.
    import app.login

    monkeypatch.setattr(app.login, "read_profile_from_page", fake)


async def test_returns_fields_when_scrape_succeeds(monkeypatch):
    _stub_scrape(
        monkeypatch,
        LoginProfile(
            platform_user_id="MS4wLjAB", username="Test Creator", avatar_url="https://x/a.jpg"
        ),
    )
    got = await validation._read_profile_best_effort("douyin", object(), _FakeContext())
    assert got == {
        "platform_user_id": "MS4wLjAB",
        "username": "Test Creator",
        "avatar_url": "https://x/a.jpg",
        "platform_handle": None,
    }


async def test_scrape_exception_yields_none_and_does_not_propagate(monkeypatch):
    """The clause this whole module exists for."""
    _stub_scrape(monkeypatch, RuntimeError("selector vanished after a redesign"))
    got = await validation._read_profile_best_effort("douyin", object(), _FakeContext())
    assert got is None


async def test_unreadable_cookies_still_scrapes(monkeypatch):
    """A dead context costs the cookie fallback, not the whole profile."""
    _stub_scrape(monkeypatch, LoginProfile(platform_user_id="", username="Named", avatar_url=None))
    got = await validation._read_profile_best_effort(
        "douyin", object(), _FakeContext(boom=True)
    )
    assert got == {
        "platform_user_id": None,
        "username": "Named",
        "avatar_url": None,
        "platform_handle": None,
    }


async def test_all_fields_empty_returns_none(monkeypatch):
    """Every selector missed. Returning {} would let a caller overwrite a good
    stored username with nothing — indistinguishable from a real empty name."""
    _stub_scrape(monkeypatch, LoginProfile(platform_user_id="", username="", avatar_url=None))
    got = await validation._read_profile_best_effort("douyin", object(), _FakeContext())
    assert got is None


async def test_unknown_platform_returns_none():
    """No login flow registered — nothing to scrape, and not an error."""
    got = await validation._read_profile_best_effort(
        "not-a-platform", object(), _FakeContext()
    )
    assert got is None
