"""PinnedDNSResolver — per-instance DNS cache with boundary validation.

The foundation primitive for the unified outbound HTTP boundary (B9).
Resolves a hostname once via the boundary's validation chain, caches the
result for a TTL, and returns the validated IP that downstream code
should connect to.

Pinning the resolution kills the DNS rebinding window: once we've
verified the host points to a public IP, all subsequent connections from
this resolver instance go to that exact IP. The attacker cannot flip
DNS between validation and connection.

Used by:
- B9-B SafeAsyncClient (custom httpx transport that swaps the request
  URL host for the pinned IP at connection time)
- B9-D SsrfProxy (resolves CONNECT targets before opening the tunnel)

See docs/architecture/boundary-layer.md.
"""

from __future__ import annotations

import time
from typing import Optional

from app.boundary import url_guard as _ug
from app.core.config import settings


class PinnedDNSResolver:
    """Per-instance DNS pin cache.

    Each cache entry: ``host -> (pinned_ip, expires_at)``. Cache is
    populated on the first ``resolve_and_validate(host)`` call. Within
    TTL, subsequent calls return the same IP without hitting the resolver.

    Caches are per-instance (not shared globally), so each
    SafeAsyncClient / SsrfProxy gets its own pinning context. This
    matches OpenClaw ``createPinnedLookup`` pattern (ssrf.ts:302-359).
    """

    def __init__(self, ttl_seconds: Optional[int] = None) -> None:
        self._cache: dict[str, tuple[str, float]] = {}
        self._ttl: int = (
            ttl_seconds
            if ttl_seconds is not None
            else settings.SSRF_DNS_CACHE_TTL_SECONDS
        )

    async def resolve_and_validate(self, host: str) -> str:
        """Return the validated, pinned IP for ``host``.

        Literal IPv4 / IPv6 hosts skip DNS and are validated directly
        against the boundary IP policy. This makes the resolver safe to
        use from the SsrfProxy CONNECT path where the target may be an
        IP literal that an attacker hopes will bypass DNS validation.

        For DNS hostnames: on cache miss, resolve via
        :func:`app.boundary.url_guard._resolve_host_async` and run every
        returned address through
        :func:`app.boundary.url_guard._check_resolved_addrs`. If any
        address is in a blocked range (RFC1918, loopback, link-local,
        IPv6 ULA, configured extra-blocked networks), raise
        ``URLBlockedError``.

        The first allowed address is pinned. Subsequent calls within TTL
        return the same IP without re-resolving (the load-bearing
        rebinding defence).
        """
        # Literal IP fast path — validate directly, no DNS, no caching
        # needed (the literal IS the connect target).
        import ipaddress

        try:
            literal = ipaddress.ip_address(_ug._strip_ipv6_zone(host))
        except ValueError:
            literal = None

        if literal is not None:
            # _check_ip raises URLBlockedError on blocked range.
            _ug._check_ip(literal, host)
            return host

        now = time.time()
        cached = self._cache.get(host)
        if cached is not None and cached[1] > now:
            return cached[0]

        # Cache miss / expired — re-resolve. Module-level access (rather
        # than 'from ... import ...') so test monkeypatch on url_guard
        # is observed here too.
        # _resolve_host_async may raise socket.gaierror; callers should
        # handle (or wrap with their own URLBlockedError translation, as
        # validate_url_async does).
        addrs = await _ug._resolve_host_async(host)
        # Raises URLBlockedError if any addr is in a blocked range.
        _ug._check_resolved_addrs(addrs, host)

        # IPv4 preference: getaddrinfo may return AAAA records first when
        # the host has IPv6 routing, but the Docker bridge network inside
        # our prod backend container has no IPv6 egress — picking IPv6
        # then makes asyncio.open_connection hang until socket timeout
        # (bilibili download `Read timed out`, 2026-05-12). Stable sort so
        # IPv4 stays before IPv6 without disturbing relative order within
        # each family (DNS round-robin within v4 still rotates).
        if settings.SSRF_PREFER_IPV4:
            addrs = sorted(addrs, key=lambda a: ":" in a)

        pinned = addrs[0]
        self._cache[host] = (pinned, now + self._ttl)
        return pinned

    def invalidate(self, host: str) -> None:
        """Drop the cache entry for ``host``. Useful when downstream
        connection failed and caller suspects the IP is stale."""
        self._cache.pop(host, None)

    def clear(self) -> None:
        """Drop the entire cache (test helper / on-error cleanup)."""
        self._cache.clear()
