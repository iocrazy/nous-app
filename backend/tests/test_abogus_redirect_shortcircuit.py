"""Tests for the douyin share-link redirect short-circuit.

Background (2026-06-10): douyin short links resolve through a 3-hop
chain — v.douyin.com 302 → iesdouyin.com/share/video/{id} 302 →
www.douyin.com/video/{id} (a full HTML page). ABogus only needs the
aweme_id, which the FIRST Location already carries, yet
``_resolve_aweme_id`` followed the whole chain (8-22s observed under
douyin-side throttling). Now it reads redirects hop-by-hop and stops
as soon as a Location matches.

Also pins the boundary opt-out this relies on: ``SafeAsyncClient``
honors an explicit ``follow_redirects=False`` (returning the raw 3xx
issues no further requests — strictly safer than the validate-each-hop
loop; the initial URL is still SSRF-validated).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.boundary import url_guard
from app.boundary.safe_http import safe_async_client
from app.services.media.parsers.douyin_parse.abogus_parser import ABogusDouyinParser

AWEME_ID = "7629876976207465061"
SHORT_URL = "https://v.douyin.com/e6Udi1oig_s/"
SHARE_LOCATION = f"https://www.iesdouyin.com/share/video/{AWEME_ID}/?from_ssr=1&did=x"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """All test hostnames resolve to a public IP so the SSRF validator
    passes without real DNS (same pattern as tests/boundary/test_safe_http)."""
    url_guard._reset_dns_cache()
    url_guard._reset_network_cache()

    async def _fake_resolve(host: str) -> list[str]:
        return ["8.8.8.8"]

    monkeypatch.setattr(url_guard, "_resolve_host_async", _fake_resolve)


def test_match_aweme_id_patterns():
    m = ABogusDouyinParser._match_aweme_id
    assert m(SHARE_LOCATION) == AWEME_ID
    assert m(f"https://www.douyin.com/video/{AWEME_ID}") == AWEME_ID
    assert m(f"https://www.douyin.com/note/{AWEME_ID}") == AWEME_ID
    assert m(f"https://www.iesdouyin.com/share/slides/{AWEME_ID}") == AWEME_ID
    assert m("https://v.douyin.com/abc123/") is None


@pytest.mark.asyncio
@respx.mock
async def test_resolve_short_circuits_on_first_location():
    """Only the short link itself is fetched — the iesdouyin and
    douyin.com hops are NOT requested (no respx mock registered for
    them: a request would raise)."""
    route = respx.get(SHORT_URL).mock(
        return_value=httpx.Response(302, headers={"Location": SHARE_LOCATION})
    )

    aweme_id = await ABogusDouyinParser._resolve_aweme_id(SHORT_URL, "test-ua")

    assert aweme_id == AWEME_ID
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_resolve_skips_network_when_url_already_matches():
    """A canonical douyin.com/video URL resolves with ZERO requests."""
    aweme_id = await ABogusDouyinParser._resolve_aweme_id(
        f"https://www.douyin.com/video/{AWEME_ID}?from=share", "test-ua"
    )
    assert aweme_id == AWEME_ID


@pytest.mark.asyncio
async def test_resolve_bare_digits_passthrough():
    assert await ABogusDouyinParser._resolve_aweme_id(AWEME_ID, "ua") == AWEME_ID


@pytest.mark.asyncio
@respx.mock
async def test_resolve_falls_back_to_final_url_tail():
    """Redirect chain whose Locations never match a pattern: follow to
    the terminal response and fall back to the digit-tail heuristic."""
    respx.get("https://v.douyin.com/opaque/").mock(
        return_value=httpx.Response(
            302, headers={"Location": f"https://share.example.com/x/{AWEME_ID}"}
        )
    )
    respx.get(f"https://share.example.com/x/{AWEME_ID}").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )

    aweme_id = await ABogusDouyinParser._resolve_aweme_id(
        "https://v.douyin.com/opaque/", "test-ua"
    )
    assert aweme_id == AWEME_ID


@pytest.mark.asyncio
@respx.mock
async def test_safe_client_honors_explicit_no_follow():
    """Boundary pin: follow_redirects=False returns the raw 302 (no
    second request), while the default still follows."""
    respx.get("https://a.example.com/").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://b.example.com/dest"}
        )
    )
    respx.get("https://b.example.com/dest").mock(
        return_value=httpx.Response(200, text="final")
    )

    async with safe_async_client() as client:
        raw = await client.get("https://a.example.com/", follow_redirects=False)
        assert raw.status_code == 302
        assert raw.headers["Location"] == "https://b.example.com/dest"

        followed = await client.get("https://a.example.com/")
        assert followed.status_code == 200
