"""Regression test for SSRF_PREFER_IPV4 path in PinnedDNSResolver.

Production symptom (2026-05-12, post-PR #249 deploy):
yt-dlp bilibili download timed out 20s on `Read timed out` from inside
the backend container. NAS host curl was 0.12s. Bilibili AAAA records
resolve to IPv6 first; Docker bridge has no IPv6 egress → pinned IP
went to IPv6 → asyncio.open_connection in SsrfProxy hangs.

Fix: SSRF_PREFER_IPV4 (default True) stably reorders the resolved addr
list so IPv4 comes first.
"""

from __future__ import annotations


import pytest

from app.boundary.pinned_dns import PinnedDNSResolver


@pytest.fixture(autouse=True)
def _clear_cache():
    yield


async def _stub_resolve(_host: str) -> list[str]:
    # AAAA first (what bilibili commonly returns from China Telecom)
    return [
        "240e:978:90d:1000::74",
        "240e:978:90d:1000::85",
        "111.45.18.100",
        "111.45.18.101",
    ]


async def test_ipv4_preferred_when_flag_on(monkeypatch):
    """Default SSRF_PREFER_IPV4=True: pinned addr is the first IPv4 even
    though AAAA records come back first from getaddrinfo."""
    monkeypatch.setattr(
        "app.boundary.pinned_dns._ug._resolve_host_async", _stub_resolve
    )
    monkeypatch.setattr(
        "app.boundary.pinned_dns._ug._check_resolved_addrs",
        lambda addrs, host: None,
    )

    pd = PinnedDNSResolver()
    pd.clear()
    pinned = await pd.resolve_and_validate("www.bilibili.com")
    assert pinned == "111.45.18.100", f"expected IPv4 pin, got {pinned!r}"


async def test_ipv6_kept_when_flag_off(monkeypatch):
    """SSRF_PREFER_IPV4=False: legacy behaviour, addrs[0] wins (here IPv6)."""
    monkeypatch.setattr(
        "app.boundary.pinned_dns._ug._resolve_host_async", _stub_resolve
    )
    monkeypatch.setattr(
        "app.boundary.pinned_dns._ug._check_resolved_addrs",
        lambda addrs, host: None,
    )
    monkeypatch.setattr("app.boundary.pinned_dns.settings.SSRF_PREFER_IPV4", False)

    pd = PinnedDNSResolver()
    pd.clear()
    pinned = await pd.resolve_and_validate("ipv6host.example")
    assert pinned == "240e:978:90d:1000::74"


async def test_ipv4_only_list_unaffected(monkeypatch):
    """When all addrs are IPv4, ordering must not change."""

    async def _v4_only(_host: str) -> list[str]:
        return ["1.2.3.4", "5.6.7.8"]

    monkeypatch.setattr("app.boundary.pinned_dns._ug._resolve_host_async", _v4_only)
    monkeypatch.setattr(
        "app.boundary.pinned_dns._ug._check_resolved_addrs",
        lambda addrs, host: None,
    )

    pd = PinnedDNSResolver()
    pd.clear()
    assert await pd.resolve_and_validate("v4only.example") == "1.2.3.4"
