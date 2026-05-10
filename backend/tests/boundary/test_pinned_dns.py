"""PinnedDNSResolver — per-instance DNS cache with boundary validation.

Foundation primitive for B9-B (SafeAsyncClient) and B9-D (SsrfProxy).
"""

from __future__ import annotations

import time

import pytest

from app.boundary import url_guard
from app.boundary.errors import URLBlockedError
from app.boundary.pinned_dns import PinnedDNSResolver


@pytest.fixture(autouse=True)
def _reset_caches():
    url_guard._reset_dns_cache()
    url_guard._reset_network_cache()


@pytest.mark.unit
async def test_resolves_once_and_pins(monkeypatch):
    """First request hits the resolver; second uses the cache."""
    call_count = {"n": 0}

    async def fake_resolve(host: str) -> list[str]:
        call_count["n"] += 1
        return ["8.8.8.8"]

    monkeypatch.setattr(url_guard, "_resolve_host_async", fake_resolve)

    pinner = PinnedDNSResolver()
    pin1 = await pinner.resolve_and_validate("example.com")
    pin2 = await pinner.resolve_and_validate("example.com")
    assert pin1 == "8.8.8.8"
    assert pin2 == "8.8.8.8"
    assert call_count["n"] == 1


@pytest.mark.unit
async def test_blocks_resolved_private_ip(monkeypatch):
    """Validation runs on the resolved IP — private ranges raise."""

    async def fake_resolve(host: str) -> list[str]:
        return ["192.168.50.9"]

    monkeypatch.setattr(url_guard, "_resolve_host_async", fake_resolve)

    pinner = PinnedDNSResolver()
    with pytest.raises(URLBlockedError):
        await pinner.resolve_and_validate("attacker.example.com")


@pytest.mark.unit
async def test_blocks_when_any_resolved_addr_private(monkeypatch):
    """If multiple A records and ANY is private, reject (consistent with
    validate_url_async behaviour — defends mixed-record attacks)."""

    async def fake_resolve(host: str) -> list[str]:
        return ["8.8.8.8", "10.0.0.1"]

    monkeypatch.setattr(url_guard, "_resolve_host_async", fake_resolve)

    pinner = PinnedDNSResolver()
    with pytest.raises(URLBlockedError):
        await pinner.resolve_and_validate("mixed.example.com")


@pytest.mark.unit
async def test_separate_hosts_resolved_separately(monkeypatch):
    """Cache key is host. Different hosts each resolve."""
    call_count = {"n": 0}

    async def fake_resolve(host: str) -> list[str]:
        call_count["n"] += 1
        return {"a.com": ["1.1.1.1"], "b.com": ["8.8.8.8"]}[host]

    monkeypatch.setattr(url_guard, "_resolve_host_async", fake_resolve)

    pinner = PinnedDNSResolver()
    assert await pinner.resolve_and_validate("a.com") == "1.1.1.1"
    assert await pinner.resolve_and_validate("b.com") == "8.8.8.8"
    assert call_count["n"] == 2


@pytest.mark.unit
async def test_ttl_expires(monkeypatch):
    """After TTL elapses the cache entry is re-resolved."""
    call_count = {"n": 0}

    async def fake_resolve(host: str) -> list[str]:
        call_count["n"] += 1
        return ["8.8.8.8"]

    monkeypatch.setattr(url_guard, "_resolve_host_async", fake_resolve)

    # Use a tiny TTL so we can observe expiry quickly
    pinner = PinnedDNSResolver(ttl_seconds=1)
    await pinner.resolve_and_validate("example.com")

    # Advance time past TTL by monkey-patching time.time
    real_time = time.time
    monkeypatch.setattr("app.boundary.pinned_dns.time.time", lambda: real_time() + 5)
    await pinner.resolve_and_validate("example.com")
    assert call_count["n"] == 2


@pytest.mark.unit
async def test_independent_instances_do_not_share_cache(monkeypatch):
    """Each PinnedDNSResolver has its own cache (per-client isolation)."""
    call_count = {"n": 0}

    async def fake_resolve(host: str) -> list[str]:
        call_count["n"] += 1
        return ["8.8.8.8"]

    monkeypatch.setattr(url_guard, "_resolve_host_async", fake_resolve)

    pinner_a = PinnedDNSResolver()
    pinner_b = PinnedDNSResolver()

    await pinner_a.resolve_and_validate("example.com")
    await pinner_b.resolve_and_validate("example.com")
    # Each instance resolved once (no shared cache)
    assert call_count["n"] == 2


@pytest.mark.unit
async def test_dns_failure_propagates(monkeypatch):
    """When resolver fails (NXDOMAIN), pinner raises URLBlockedError
    via validate_url_async semantics (fail closed)."""
    import socket

    async def fake_resolve(host: str) -> list[str]:
        raise socket.gaierror("NXDOMAIN")

    monkeypatch.setattr(url_guard, "_resolve_host_async", fake_resolve)

    pinner = PinnedDNSResolver()
    with pytest.raises((URLBlockedError, socket.gaierror)):
        await pinner.resolve_and_validate("nonexistent.example.com")


@pytest.mark.unit
async def test_picks_first_allowed_ip(monkeypatch):
    """When multiple public IPs returned, pin the first."""

    async def fake_resolve(host: str) -> list[str]:
        return ["8.8.8.8", "1.1.1.1"]

    monkeypatch.setattr(url_guard, "_resolve_host_async", fake_resolve)

    pinner = PinnedDNSResolver()
    ip = await pinner.resolve_and_validate("example.com")
    assert ip == "8.8.8.8"
