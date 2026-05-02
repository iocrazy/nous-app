"""URL boundary validator — SSRF defense.

See docs/architecture/boundary-layer.md for the full contract.

Rejects:
- non-http(s) schemes
- empty / non-string input
- literal private/loopback/link-local/multicast/reserved IPv4 + IPv6
- decimal / octal / hex IPv4 literals (e.g. http://2130706433/ = 127.0.0.1)
- .localhost / .local / .internal hostname suffixes (mDNS bypass)
- AWS / GCP / Azure metadata-server hostnames
- networks listed in settings.SSRF_EXTRA_BLOCKED_NETWORKS (default: 192.168.50.0/24)

Allows (override):
- networks listed in settings.SSRF_DEV_ALLOWLIST (must be empty in prod)

Two entry points:
- validate_url(raw)        — sync, for workflow / CLI / non-async callers
- validate_url_async(raw)  — async, mandatory for FastAPI handlers
                             (sync getaddrinfo blocks the event loop)

DNS resolution is cached for SSRF_DNS_CACHE_TTL_SECONDS to amortize cost
and partially defend DNS rebinding within the cache window.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from functools import lru_cache
from urllib.parse import urlparse

from app.boundary.errors import URLBlockedError
from app.boundary.types import ValidatedURL
from app.core.config import settings

ALLOWED_SCHEMES = {"http", "https"}

# Hostname suffixes that resolve via mDNS / .internal directories.
# Even if the literal IP isn't private, these point to local infrastructure.
BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".intranet",
    ".corp",
    ".private",
)

# Cloud metadata server hostnames — same risk as 169.254.169.254.
BLOCKED_METADATA_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "metadata",  # short form sometimes resolves
    # Azure IMDS reaches via 169.254.169.254 — IP path catches it; no DNS form.
})


def _parse_networks(cidrs: list[str]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse CIDR strings to network objects, skipping invalid entries with a warning."""
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for c in cidrs or []:
        try:
            nets.append(ipaddress.ip_network(c.strip(), strict=False))
        except (ValueError, TypeError):
            # Surface invalid config but don't crash boundary
            pass
    return tuple(nets)


# Lazy-init network lists from settings so test monkeypatching works.
_extra_blocked_cache: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] | None = None
_dev_allow_cache: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] | None = None


def _get_extra_blocked() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    global _extra_blocked_cache
    if _extra_blocked_cache is None:
        _extra_blocked_cache = _parse_networks(settings.SSRF_EXTRA_BLOCKED_NETWORKS)
    return _extra_blocked_cache


def _get_dev_allow() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    global _dev_allow_cache
    if _dev_allow_cache is None:
        _dev_allow_cache = _parse_networks(settings.SSRF_DEV_ALLOWLIST)
    return _dev_allow_cache


def _reset_network_cache() -> None:
    """Test-only: re-read settings (call after monkeypatching)."""
    global _extra_blocked_cache, _dev_allow_cache
    _extra_blocked_cache = None
    _dev_allow_cache = None


def _is_dev_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if ``ip`` is in the explicit dev allowlist."""
    for net in _get_dev_allow():
        if ip.version == net.version and ip in net:
            return True
    return False


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if IP is in any unsafe range (ignoring dev allowlist —
    caller must check that first)."""
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return True
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    for net in _get_extra_blocked():
        if ip.version == net.version and ip in net:
            return True
    if isinstance(ip, ipaddress.IPv6Address):
        # IPv4-mapped IPv6 (::ffff:127.0.0.1) — re-check via IPv4
        if ip.ipv4_mapped is not None and _is_blocked_ip(ip.ipv4_mapped):
            return True
        # IPv4-compatible IPv6 (deprecated form ::127.0.0.1) — best-effort check
        try:
            if int(ip) < (1 << 32):
                v4 = ipaddress.IPv4Address(int(ip))
                if _is_blocked_ip(v4):
                    return True
        except (ValueError, ipaddress.AddressValueError):
            pass
    return False


def _check_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, host: str) -> None:
    """Apply allowlist + blocklist policy. Raise URLBlockedError on reject."""
    if _is_dev_allowed(ip):
        return
    if _is_blocked_ip(ip):
        raise URLBlockedError(f"blocked address for host {host}")


def _try_decimal_octal_hex_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Detect non-canonical IPv4 literals that bypass naive validators.

    Examples (all == 127.0.0.1):
        2130706433       (decimal)
        017700000001     (octal)
        0x7f000001       (hex)
        0177.0.0.1       (octal in dotted form)

    Returns the IPv4Address if host parses as a non-canonical literal,
    None otherwise (caller falls back to DNS).
    """
    h = host.strip()
    if not h or "." in h and not any(c.isdigit() for c in h):
        return None

    # Pure-numeric forms (no dots): decimal / hex.
    if h.isdigit():
        try:
            n = int(h)
            if 0 <= n <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(n)
        except (ValueError, ipaddress.AddressValueError):
            return None

    if h.lower().startswith("0x"):
        try:
            n = int(h, 16)
            if 0 <= n <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(n)
        except (ValueError, ipaddress.AddressValueError):
            return None

    # Dotted form with leading zeros (octal) or hex parts.
    # Parse each part with the right base; track whether ANY part was non-canonical.
    # Canonical-only (e.g. "127.0.0.1") returns None — caller's ipaddress.ip_address
    # already handles it.
    if "." in h:
        parts = h.split(".")
        if len(parts) == 4:
            try:
                ints: list[int] = []
                any_non_canonical = False
                for p in parts:
                    if p.lower().startswith("0x"):
                        n = int(p, 16)
                        any_non_canonical = True
                    elif len(p) > 1 and p.startswith("0"):
                        # leading-zero octet (e.g. "0177") — parse as octal.
                        n = int(p, 8)
                        any_non_canonical = True
                    elif p.isdigit():
                        # canonical decimal octet (covers "0", "127", "255")
                        n = int(p, 10)
                    else:
                        return None
                    if not (0 <= n <= 255):
                        return None
                    ints.append(n)
                if not any_non_canonical:
                    return None  # let ipaddress.ip_address handle canonical
                packed = (ints[0] << 24) | (ints[1] << 16) | (ints[2] << 8) | ints[3]
                return ipaddress.IPv4Address(packed)
            except (ValueError, ipaddress.AddressValueError):
                return None

    return None


def _is_blocked_hostname(host: str) -> str | None:
    """Check hostname against suffix and metadata-server blocklists.
    Returns reason string if blocked, None if allowed."""
    h = host.lower().rstrip(".")
    if h in BLOCKED_METADATA_HOSTNAMES:
        return f"blocked metadata hostname: {h}"
    for suffix in BLOCKED_SUFFIXES:
        if h.endswith(suffix):
            return f"blocked hostname suffix {suffix}: {h}"
    return None


def _strip_ipv6_zone(addr: str) -> str:
    """Remove IPv6 zone ID (e.g. fe80::1%eth0 → fe80::1) before parsing."""
    return addr.split("%", 1)[0]


def _validate_url_pre_dns(raw: str) -> tuple[str, ipaddress.IPv4Address | ipaddress.IPv6Address | None]:
    """Shared validation up to (but not including) DNS resolution.

    Returns (hostname, literal_ip_or_None). Raises URLBlockedError on reject.
    """
    if not raw or not isinstance(raw, str):
        raise URLBlockedError("empty or non-string url")

    try:
        parsed = urlparse(raw)
    except ValueError as e:
        raise URLBlockedError(f"unparseable url: {e}") from e

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise URLBlockedError(f"scheme not allowed: {parsed.scheme!r}")

    host = parsed.hostname
    if not host:
        raise URLBlockedError("missing hostname")

    # Hostname suffix / metadata-server check (cheaper than IP check).
    reason = _is_blocked_hostname(host)
    if reason is not None:
        raise URLBlockedError(reason)

    # Try canonical IPv4/IPv6 literal first.
    try:
        literal = ipaddress.ip_address(_strip_ipv6_zone(host))
    except ValueError:
        literal = None

    # Try decimal/octal/hex IPv4 literal.
    if literal is None:
        literal = _try_decimal_octal_hex_ipv4(host)

    if literal is not None:
        _check_ip(literal, host)

    return host, literal


# ---------- DNS cache (TTL-aware) ----------

_dns_cache: dict[str, tuple[float, list[str]]] = {}


def _cache_get(host: str) -> list[str] | None:
    entry = _dns_cache.get(host)
    if entry is None:
        return None
    expires_at, addrs = entry
    if time.time() > expires_at:
        _dns_cache.pop(host, None)
        return None
    return addrs


def _cache_put(host: str, addrs: list[str]) -> None:
    expires_at = time.time() + settings.SSRF_DNS_CACHE_TTL_SECONDS
    _dns_cache[host] = (expires_at, addrs)


def _reset_dns_cache() -> None:
    """Test-only."""
    _dns_cache.clear()


def _resolve_host_sync(host: str) -> list[str]:
    """Sync DNS resolution. Caller handles socket.gaierror.

    Split out for monkeypatching in tests."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [_strip_ipv6_zone(info[4][0]) for info in infos]


async def _resolve_host_async(host: str) -> list[str]:
    """Async DNS resolution via running loop.

    Wrapped in asyncio.wait_for(timeout=settings.SSRF_DNS_TIMEOUT_SECONDS)
    so a misconfigured resolver cannot hang the event loop indefinitely.
    """
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(
        loop.getaddrinfo(host, None, type=socket.SOCK_STREAM),
        timeout=settings.SSRF_DNS_TIMEOUT_SECONDS,
    )
    return [_strip_ipv6_zone(info[4][0]) for info in infos]


def _check_resolved_addrs(addrs: list[str], host: str) -> None:
    """Apply IP policy to every resolved address. Reject if ANY is blocked
    (defends mixed-record DNS attacks)."""
    if not addrs:
        raise URLBlockedError(f"dns returned no addresses for {host}")
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as e:
            raise URLBlockedError(
                f"dns returned non-ip address for {host}: {addr!r}"
            ) from e
        _check_ip(ip, host)


def validate_url(raw: str) -> ValidatedURL:
    """Sync entry point. Use from workflow / CLI / non-async callers.

    DO NOT call from FastAPI async handlers — sync getaddrinfo blocks
    the event loop. Use validate_url_async there.
    """
    host, literal = _validate_url_pre_dns(raw)

    if literal is not None:
        # Literal IP — already checked in pre-dns. No DNS needed.
        return ValidatedURL(raw)

    cached = _cache_get(host)
    if cached is not None:
        _check_resolved_addrs(cached, host)
        return ValidatedURL(raw)

    try:
        addrs = _resolve_host_sync(host)
    except (socket.gaierror, socket.herror) as e:
        raise URLBlockedError(f"dns resolution failed for {host}: {e}") from e

    _check_resolved_addrs(addrs, host)
    _cache_put(host, addrs)
    return ValidatedURL(raw)


async def validate_url_async(raw: str) -> ValidatedURL:
    """Async entry point. Use from FastAPI async handlers.

    DNS resolution runs via loop.getaddrinfo() with a 2-second timeout
    (configurable via settings.SSRF_DNS_TIMEOUT_SECONDS). DNS results
    cached for 60s by default to amortize cost.
    """
    host, literal = _validate_url_pre_dns(raw)

    if literal is not None:
        return ValidatedURL(raw)

    cached = _cache_get(host)
    if cached is not None:
        _check_resolved_addrs(cached, host)
        return ValidatedURL(raw)

    try:
        addrs = await _resolve_host_async(host)
    except (socket.gaierror, socket.herror) as e:
        raise URLBlockedError(f"dns resolution failed for {host}: {e}") from e
    except asyncio.TimeoutError as e:
        raise URLBlockedError(f"dns resolution timed out for {host}") from e

    _check_resolved_addrs(addrs, host)
    _cache_put(host, addrs)
    return ValidatedURL(raw)
