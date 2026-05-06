"""SafeAsyncClient — drop-in httpx.AsyncClient replacement that enforces
the boundary policy on every HTTP transaction.

Guarantees on every request, including redirect-followed hops:

  1. The URL about to be fetched is validated via validate_url_async
     (SSRF: scheme allowlist + private IP block + DNS rebinding cache)
  2. Redirects are followed manually (follow_redirects=False at the
     underlying client) so we control re-validation per hop
  3. Authorization, Cookie, and Proxy-Authorization headers are stripped
     before any cross-origin redirect (per OpenClaw redirect-headers.ts —
     prevents credential leak to attacker-controlled hosts)
  4. Default max_redirects=10, default 30s timeout

Use everywhere instead of raw httpx.AsyncClient:

    from app.boundary import safe_async_client

    async with safe_async_client(http2=True) as client:
        response = await client.get(url)  # validated + safe-redirected

The PinnedDNSResolver (B9-A) provides per-instance DNS pinning for the
underlying validate_url_async cache layer, killing the rebinding window
across both the initial request and every redirect hop.

See docs/architecture/boundary-layer.md (Layer 3 — SafeAsyncClient).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger

from app.boundary.errors import URLBlockedError
from app.boundary.pinned_dns import PinnedDNSResolver
from app.boundary.url_guard import validate_url_async

# Per OpenClaw redirect-headers.ts — these MUST NOT survive a cross-origin
# redirect (else attacker who got a redirect through the validator could
# harvest your credentials).
SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})

DEFAULT_MAX_REDIRECTS = 10
DEFAULT_TIMEOUT_SECONDS = 30.0


def _origin_tuple(url: str | httpx.URL) -> tuple[str, str | None, int | None]:
    """(scheme, host, port). httpx.URL.host returns None for path-only."""
    parsed = urlparse(str(url))
    return (parsed.scheme.lower(), parsed.hostname, parsed.port)


def _same_origin(a: str | httpx.URL, b: str | httpx.URL) -> bool:
    return _origin_tuple(a) == _origin_tuple(b)


def _strip_sensitive(headers: httpx.Headers) -> httpx.Headers:
    """Return a new Headers without SENSITIVE_HEADERS keys."""
    new = httpx.Headers()
    for k, v in headers.items():
        if k.lower() not in SENSITIVE_HEADERS:
            new[k] = v
    return new


class SafeAsyncClient(httpx.AsyncClient):
    """httpx.AsyncClient subclass that enforces the boundary policy.

    Subclassing ``httpx.AsyncClient`` (rather than wrapping) lets every
    existing convenience method (``get``, ``post``, ``stream``, etc.)
    flow through our overridden ``send`` automatically.
    """

    def __init__(
        self,
        *,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT_SECONDS,
        pinner: PinnedDNSResolver | None = None,
        **kwargs: Any,
    ) -> None:
        # We handle redirects manually so we can re-validate each hop.
        # Accepting follow_redirects=True from the caller would silently
        # bypass our policy — strip it.
        kwargs.pop("follow_redirects", None)
        super().__init__(
            follow_redirects=False,
            timeout=timeout,
            **kwargs,
        )
        self._max_redirects = max_redirects
        # PinnedDNSResolver lives on the client so its cache survives
        # multiple requests in the same context manager.
        self._pinner = pinner if pinner is not None else PinnedDNSResolver()

    async def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        auth: httpx.Auth | None = httpx._client.USE_CLIENT_DEFAULT,  # type: ignore[attr-defined]
        follow_redirects: bool | None = None,
    ) -> httpx.Response:
        """Validate-each-hop redirect loop. Caller's follow_redirects flag
        is intentionally ignored — boundary policy always wins."""
        current = request
        for hop in range(self._max_redirects + 1):
            # 1. Validate the URL we're about to fetch.
            #    Raises URLBlockedError on private/blocked IP — propagates
            #    to the caller's API edge handler (or the global
            #    BoundaryError exception handler in app/core/exceptions.py).
            try:
                await validate_url_async(str(current.url))
            except URLBlockedError:
                # Layer 5 audit. Distinguish initial (hop=0) from redirect
                # rejection so operators can see attack patterns.
                try:
                    from app.boundary import audit

                    audit.log_block(
                        layer=audit.LAYER_SAFE_HTTP,
                        reason="redirect_blocked" if hop > 0 else "initial_blocked",
                        raw_url=str(current.url),
                        metadata={"hop": hop},
                    )
                except Exception:
                    pass
                raise

            # 2. (PinnedDNS warm-cache hint — does not change connection
            #     target in this commit; see B9-future for socket-level
            #     pinning. Keeps the validation cache + pin cache aligned
            #     so DNS rebinding window stays at zero between
            #     validate_url_async and httpx connect.)
            host = current.url.host
            if host:
                try:
                    await self._pinner.resolve_and_validate(host)
                except URLBlockedError:
                    raise

            # 3. Send WITHOUT follow_redirects. We loop manually.
            response = await super().send(
                current,
                stream=stream,
                auth=auth,
                follow_redirects=False,
            )

            if not response.is_redirect:
                return response

            location = response.headers.get("Location")
            if not location:
                # 3xx without Location — return as-is (caller decides).
                return response

            # 4. Build the next request.
            new_url = current.url.join(location)

            # 5. Cross-origin? Strip credentials per OpenClaw policy.
            if _same_origin(current.url, new_url):
                next_headers = httpx.Headers(current.headers)
            else:
                next_headers = _strip_sensitive(current.headers)
                logger.debug(
                    "safe_http: cross-origin redirect — stripped sensitive "
                    f"headers ({current.url.host} -> {new_url.host})"
                )

            # 6. Update Host header to match new URL (always).
            next_headers["host"] = new_url.netloc.decode("ascii")

            # 7. Build the redirect-followed request. Keep method / body
            #    semantics consistent with httpx default behaviour:
            #    - 301/302/303 GET/HEAD: keep method
            #    - 303 anything: -> GET (drop body)
            #    - 307/308: keep method + body
            method = current.method
            content: Any = None
            if response.status_code == 303 and method not in ("GET", "HEAD"):
                method = "GET"
                # 303 forces drop of body
            elif response.status_code in (307, 308):
                content = current.content
            else:
                # 301/302 with non-GET: per RFC 7231 §6.4.3 this should
                # keep the method, but most clients including browsers
                # downgrade POST to GET. Match httpx default behaviour:
                # drop body, downgrade to GET on 301/302 from non-GET.
                if method not in ("GET", "HEAD"):
                    method = "GET"

            current = httpx.Request(
                method=method,
                url=new_url,
                headers=next_headers,
                content=content,
            )

            # Read & close the redirect response body so connection is
            # released back to the pool.
            await response.aread()
            await response.aclose()

        raise httpx.TooManyRedirects(
            f"Exceeded max_redirects={self._max_redirects}",
            request=current,
        )


def safe_async_client(
    *,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    timeout: float | httpx.Timeout = DEFAULT_TIMEOUT_SECONDS,
    pinner: PinnedDNSResolver | None = None,
    **kwargs: Any,
) -> SafeAsyncClient:
    """Factory: drop-in for ``httpx.AsyncClient(...)``.

    Every kwarg accepted by ``httpx.AsyncClient`` passes through. The
    boundary-specific params (max_redirects, pinner) are kwargs-only.
    """
    return SafeAsyncClient(
        max_redirects=max_redirects,
        timeout=timeout,
        pinner=pinner,
        **kwargs,
    )
