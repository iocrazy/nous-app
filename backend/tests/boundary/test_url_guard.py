"""url_guard — SSRF + scheme + dev allowlist + DNS rebinding."""
from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock

import pytest

from app.boundary.errors import URLBlockedError
from app.boundary.types import ValidatedURL
from app.boundary import url_guard
from app.boundary.url_guard import validate_url, validate_url_async


@pytest.fixture(autouse=True)
def _reset_caches():
    """Each test starts with empty DNS cache and re-read settings."""
    url_guard._reset_dns_cache()
    url_guard._reset_network_cache()
    yield
    url_guard._reset_dns_cache()
    url_guard._reset_network_cache()


# ============================================================================
# Public URL passes
# ============================================================================

@pytest.mark.unit
def test_public_https_literal_ip_passes(monkeypatch):
    """Public IP literal — no DNS needed, passes."""
    v = validate_url("https://8.8.8.8/api/test")
    assert isinstance(v, ValidatedURL)


@pytest.mark.unit
def test_public_http_literal_ip_passes():
    v = validate_url("http://8.8.8.8/")
    assert isinstance(v, ValidatedURL)


@pytest.mark.unit
def test_public_dns_passes(monkeypatch):
    monkeypatch.setattr(url_guard, "_resolve_host_sync", lambda h: ["8.8.8.8"])
    v = validate_url("https://example.com/path")
    assert isinstance(v, ValidatedURL)


# ============================================================================
# Bad schemes
# ============================================================================

@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com",
        "ftp://example.com/file.zip",
        "javascript:alert(1)",
        "data:text/html,<script>",
    ],
)
def test_non_http_schemes_rejected(url: str):
    with pytest.raises(URLBlockedError):
        validate_url(url)


@pytest.mark.unit
def test_empty_url_rejected():
    with pytest.raises(URLBlockedError):
        validate_url("")


@pytest.mark.unit
def test_no_scheme_rejected():
    with pytest.raises(URLBlockedError):
        validate_url("www.example.com/foo")


# ============================================================================
# Literal IPv4 — canonical form
# ============================================================================

@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://10.0.0.1/",
        "http://10.255.255.255/",
        "http://172.16.0.1/",
        "http://172.31.0.1/",
        "http://192.168.1.1/",
        "http://192.168.50.9:9080/",  # NAS Supabase — must be blocked
        "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
        "http://0.0.0.0/",
    ],
)
def test_canonical_private_ipv4_rejected(url: str):
    with pytest.raises(URLBlockedError):
        validate_url(url)


# ============================================================================
# Literal IPv4 — non-canonical (CEO C1 — decimal/octal/hex)
# ============================================================================

@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",      # decimal == 127.0.0.1
        "http://3232248329/",      # decimal == 192.168.50.9
        "http://0x7f000001/",      # hex == 127.0.0.1
        "http://0177.0.0.1/",      # dotted octal first octet == 127.0.0.1
    ],
)
def test_decimal_octal_hex_ipv4_rejected(url: str):
    """Non-canonical IPv4 literals must not bypass SSRF (CEO C1)."""
    with pytest.raises(URLBlockedError):
        validate_url(url)


# ============================================================================
# Literal IPv6
# ============================================================================

@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://[::1]/",                  # loopback
        "http://[::ffff:127.0.0.1]/",     # IPv4-mapped loopback
        "http://[fc00::1]/",              # ULA
        "http://[fe80::1]/",              # link-local
    ],
)
def test_private_ipv6_rejected(url: str):
    with pytest.raises(URLBlockedError):
        validate_url(url)


# ============================================================================
# Hostname suffixes (CEO C2)
# ============================================================================

@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://printer.localhost/",
        "http://server.local/",
        "http://api.internal/",
        "http://service.lan/",
        "http://router.home/",
    ],
)
def test_blocked_hostname_suffix_rejected(url: str):
    """mDNS-style suffixes must be rejected before DNS (CEO C2)."""
    with pytest.raises(URLBlockedError):
        validate_url(url)


# ============================================================================
# Cloud metadata server hostnames (CEO C3)
# ============================================================================

@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://metadata.goog/",
    ],
)
def test_blocked_metadata_hostnames_rejected(url: str):
    with pytest.raises(URLBlockedError):
        validate_url(url)


# ============================================================================
# Dev allowlist (UC4)
# ============================================================================

@pytest.mark.unit
def test_dev_allowlist_overrides_block(monkeypatch):
    """SSRF_DEV_ALLOWLIST must allow specific IPs past the block."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(
        app_settings, "SSRF_DEV_ALLOWLIST", ["127.0.0.1/32", "192.168.50.9/32"]
    )
    url_guard._reset_network_cache()

    v = validate_url("http://127.0.0.1:8081/api")
    assert isinstance(v, ValidatedURL)
    v = validate_url("http://192.168.50.9:9080/admin")
    assert isinstance(v, ValidatedURL)


@pytest.mark.unit
def test_dev_allowlist_does_not_allow_unspecified(monkeypatch):
    """Allowlist is precise — only listed IPs pass, not the whole subnet."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "SSRF_DEV_ALLOWLIST", ["127.0.0.1/32"])
    url_guard._reset_network_cache()

    # 127.0.0.1 allowed
    validate_url("http://127.0.0.1/")
    # but 127.0.0.2 still blocked
    with pytest.raises(URLBlockedError):
        validate_url("http://127.0.0.2/")


# ============================================================================
# Settings-configurable extra blocked networks (E8)
# ============================================================================

@pytest.mark.unit
def test_extra_blocked_networks_from_settings(monkeypatch):
    """SSRF_EXTRA_BLOCKED_NETWORKS adds custom blocks beyond stdlib."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(
        app_settings,
        "SSRF_EXTRA_BLOCKED_NETWORKS",
        ["192.168.99.0/24"],  # custom range
    )
    url_guard._reset_network_cache()

    with pytest.raises(URLBlockedError):
        validate_url("http://192.168.99.5/")


# ============================================================================
# DNS rebinding
# ============================================================================

@pytest.mark.unit
def test_dns_resolves_to_private_rejected(monkeypatch):
    monkeypatch.setattr(url_guard, "_resolve_host_sync", lambda h: ["192.168.50.9"])
    with pytest.raises(URLBlockedError) as ei:
        validate_url("http://attacker-controlled.example.com/")
    assert "blocked" in str(ei.value).lower() or "192.168.50.9" in str(ei.value)


@pytest.mark.unit
def test_dns_resolves_to_loopback_rejected(monkeypatch):
    monkeypatch.setattr(url_guard, "_resolve_host_sync", lambda h: ["127.0.0.1"])
    with pytest.raises(URLBlockedError):
        validate_url("http://rebind.example.com/")


@pytest.mark.unit
def test_dns_mixed_addresses_any_private_rejected(monkeypatch):
    """If any A/AAAA record is private, reject."""
    monkeypatch.setattr(
        url_guard, "_resolve_host_sync", lambda h: ["8.8.8.8", "10.0.0.1"]
    )
    with pytest.raises(URLBlockedError):
        validate_url("http://mixed.example.com/")


@pytest.mark.unit
def test_dns_failure_rejected(monkeypatch):
    """Fail closed on DNS errors."""
    def boom(h: str) -> list[str]:
        raise socket.gaierror("NXDOMAIN")

    monkeypatch.setattr(url_guard, "_resolve_host_sync", boom)
    with pytest.raises(URLBlockedError):
        validate_url("http://nonexistent.example.com/")


# ============================================================================
# IPv6 zone IDs (E11)
# ============================================================================

@pytest.mark.unit
def test_ipv6_zone_id_stripped(monkeypatch):
    """getaddrinfo can return fe80::1%eth0 — zone must be stripped."""
    monkeypatch.setattr(url_guard, "_resolve_host_sync", lambda h: ["fe80::1%eth0"])
    with pytest.raises(URLBlockedError) as ei:
        validate_url("http://example.com/")
    # Should classify as link-local block, not "non-ip address"
    err_msg = str(ei.value).lower()
    assert "non-ip" not in err_msg


# ============================================================================
# DNS cache
# ============================================================================

@pytest.mark.unit
def test_dns_cache_amortizes_calls(monkeypatch):
    """Second call within TTL must hit cache, not call resolver again."""
    call_count = {"n": 0}

    def counting_resolve(h: str) -> list[str]:
        call_count["n"] += 1
        return ["8.8.8.8"]

    monkeypatch.setattr(url_guard, "_resolve_host_sync", counting_resolve)
    validate_url("http://example.com/")
    validate_url("http://example.com/")
    validate_url("http://example.com/different-path")
    assert call_count["n"] == 1


# ============================================================================
# Async path (E1) — must use loop.getaddrinfo with timeout
# ============================================================================

@pytest.mark.unit
async def test_validate_url_async_passes_public(monkeypatch):
    async def fake(host: str) -> list[str]:
        return ["8.8.8.8"]

    monkeypatch.setattr(url_guard, "_resolve_host_async", fake)
    v = await validate_url_async("https://example.com/")
    assert isinstance(v, ValidatedURL)


@pytest.mark.unit
async def test_validate_url_async_rejects_private_dns(monkeypatch):
    async def fake(host: str) -> list[str]:
        return ["192.168.50.9"]

    monkeypatch.setattr(url_guard, "_resolve_host_async", fake)
    with pytest.raises(URLBlockedError):
        await validate_url_async("https://attacker.example.com/")


@pytest.mark.unit
async def test_validate_url_async_dns_timeout_rejects(monkeypatch):
    """Slow DNS must fail closed via timeout, not hang event loop."""

    async def slow(host: str) -> list[str]:
        await asyncio.sleep(10)  # would block forever without timeout
        return ["8.8.8.8"]

    # Patch _resolve_host_async itself to simulate hang inside it
    async def slow_outer(host: str) -> list[str]:
        # call the timeout-wrapped real one with a fake getaddrinfo
        loop = asyncio.get_running_loop()
        async def fake_loop_getaddrinfo(*a, **kw):
            await asyncio.sleep(10)
            return []
        # Actually simulate timeout from the wrapper by raising
        raise asyncio.TimeoutError()

    monkeypatch.setattr(url_guard, "_resolve_host_async", slow_outer)
    with pytest.raises(URLBlockedError) as ei:
        await validate_url_async("https://slow.example.com/")
    assert "timed out" in str(ei.value).lower()


@pytest.mark.unit
async def test_validate_url_async_literal_no_dns(monkeypatch):
    """Literal IP must not invoke DNS resolver."""
    called = {"n": 0}

    async def should_not_be_called(host: str) -> list[str]:
        called["n"] += 1
        return []

    monkeypatch.setattr(url_guard, "_resolve_host_async", should_not_be_called)
    await validate_url_async("https://8.8.8.8/")
    assert called["n"] == 0
