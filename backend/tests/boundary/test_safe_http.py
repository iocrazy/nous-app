"""SafeAsyncClient — drop-in httpx.AsyncClient replacement that enforces
the boundary policy on every HTTP transaction.

Tests cover the 3 critical guarantees:
1. Initial URL is validated (URLBlockedError if private)
2. Every redirect Location is re-validated (no 302→private bypass)
3. Authorization / Cookie / Proxy-Authorization stripped on cross-origin
   redirect (per OpenClaw redirect-headers.ts)

The PinnedDNS transport integration is exercised through the resolver
mock — we don't need a real DNS lookup in unit tests.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.boundary import url_guard
from app.boundary.errors import URLBlockedError
from app.boundary.safe_http import SafeAsyncClient, safe_async_client


@pytest.fixture(autouse=True)
def _reset_boundary_caches(monkeypatch):
    url_guard._reset_dns_cache()
    url_guard._reset_network_cache()

    # All hostnames in tests resolve to a public IP via mock so the
    # boundary URL validator passes them without real network calls.
    async def _fake_resolve(host: str) -> list[str]:
        # Specific hostnames for negative-path tests
        mapping = {
            "blocked.example.com": ["10.0.0.1"],
            "redirect-target-private.example.com": ["192.168.50.9"],
        }
        return mapping.get(host, ["8.8.8.8"])

    monkeypatch.setattr(url_guard, "_resolve_host_async", _fake_resolve)
    yield


# ============================================================================
# Initial URL validation
# ============================================================================


@pytest.mark.unit
async def test_initial_literal_private_ip_blocked():
    """Direct private IP literal must fail before any network call."""
    async with safe_async_client() as client:
        with pytest.raises(URLBlockedError):
            await client.get("http://192.168.50.9/admin")


@pytest.mark.unit
async def test_initial_dns_resolves_to_private_blocked():
    """Hostname resolving to private IP rejected at request time."""
    async with safe_async_client() as client:
        with pytest.raises(URLBlockedError):
            await client.get("http://blocked.example.com/")


@pytest.mark.unit
@respx.mock
async def test_initial_public_url_passes():
    """Normal public URL is allowed through."""
    respx.get("https://example.com/").mock(return_value=httpx.Response(200, text="ok"))
    async with safe_async_client() as client:
        r = await client.get("https://example.com/")
    assert r.status_code == 200
    assert r.text == "ok"


# ============================================================================
# Redirect Location validation
# ============================================================================


@pytest.mark.unit
@respx.mock
async def test_redirect_to_private_ip_blocked():
    """A 302 to a private IP must be rejected by the redirect hook."""
    respx.get("https://attacker.example.com/").mock(
        return_value=httpx.Response(
            302, headers={"Location": "http://192.168.50.9/admin"}
        )
    )
    async with safe_async_client() as client:
        with pytest.raises(URLBlockedError):
            await client.get("https://attacker.example.com/")


@pytest.mark.unit
@respx.mock
async def test_redirect_to_dns_private_blocked():
    """302 to a hostname that resolves private must be rejected."""
    respx.get("https://attacker.example.com/").mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "https://redirect-target-private.example.com/x"},
        )
    )
    async with safe_async_client() as client:
        with pytest.raises(URLBlockedError):
            await client.get("https://attacker.example.com/")


@pytest.mark.unit
@respx.mock
async def test_redirect_to_public_followed():
    """Public-to-public redirect followed normally."""
    respx.get("https://a.example.com/").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://b.example.com/dest"}
        )
    )
    respx.get("https://b.example.com/dest").mock(
        return_value=httpx.Response(200, text="final")
    )
    async with safe_async_client() as client:
        r = await client.get("https://a.example.com/")
    assert r.status_code == 200
    assert r.text == "final"


# ============================================================================
# Cross-origin sensitive header stripping
# ============================================================================


@pytest.mark.unit
@respx.mock
async def test_authorization_stripped_on_cross_origin_redirect():
    """Authorization header MUST NOT survive cross-origin redirect."""
    captured_authorization: list[str | None] = []

    def _capture_first(request):
        captured_authorization.append(request.headers.get("Authorization"))
        return httpx.Response(
            302, headers={"Location": "https://second.example.com/dest"}
        )

    def _capture_second(request):
        captured_authorization.append(request.headers.get("Authorization"))
        return httpx.Response(200, text="ok")

    respx.get("https://first.example.com/").mock(side_effect=_capture_first)
    respx.get("https://second.example.com/dest").mock(side_effect=_capture_second)

    async with safe_async_client() as client:
        await client.get(
            "https://first.example.com/",
            headers={"Authorization": "Bearer secret-token-12345"},
        )

    assert captured_authorization[0] == "Bearer secret-token-12345"
    # Cross-origin redirect — Authorization MUST be gone
    assert captured_authorization[1] is None


@pytest.mark.unit
@respx.mock
async def test_cookie_stripped_on_cross_origin_redirect():
    captured_cookies: list[str | None] = []

    def _first(request):
        captured_cookies.append(request.headers.get("Cookie"))
        return httpx.Response(
            302, headers={"Location": "https://other.example.com/dest"}
        )

    def _second(request):
        captured_cookies.append(request.headers.get("Cookie"))
        return httpx.Response(200)

    respx.get("https://origin.example.com/").mock(side_effect=_first)
    respx.get("https://other.example.com/dest").mock(side_effect=_second)

    async with safe_async_client() as client:
        await client.get(
            "https://origin.example.com/",
            headers={"Cookie": "session=secret-session-id"},
        )

    assert captured_cookies[0] == "session=secret-session-id"
    assert captured_cookies[1] is None


@pytest.mark.unit
@respx.mock
async def test_authorization_preserved_on_same_origin_redirect():
    """Same-origin (host+port+scheme match) redirect keeps the header."""
    captured_authorization: list[str | None] = []

    def _capture(request):
        captured_authorization.append(request.headers.get("Authorization"))
        if request.url.path == "/a":
            return httpx.Response(302, headers={"Location": "https://example.com/b"})
        return httpx.Response(200)

    respx.route().mock(side_effect=_capture)

    async with safe_async_client() as client:
        await client.get(
            "https://example.com/a",
            headers={"Authorization": "Bearer keep-me"},
        )

    # Both requests on same origin — Authorization preserved
    assert captured_authorization[0] == "Bearer keep-me"
    assert captured_authorization[1] == "Bearer keep-me"


# ============================================================================
# Max redirects
# ============================================================================


@pytest.mark.unit
@respx.mock
async def test_max_redirects_enforced():
    """Default max_redirects 10. Excess raises TooManyRedirects."""
    # Set up a self-loop
    respx.get("https://loop.example.com/").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://loop.example.com/"}
        )
    )
    async with safe_async_client(max_redirects=3) as client:
        with pytest.raises(httpx.TooManyRedirects):
            await client.get("https://loop.example.com/")


# ============================================================================
# Class form
# ============================================================================


@pytest.mark.unit
async def test_safe_async_client_class_form_works():
    """SafeAsyncClient(...) class instantiation works alongside the factory."""
    client = SafeAsyncClient()
    try:
        assert isinstance(client, httpx.AsyncClient)
    finally:
        await client.aclose()
