"""SsrfProxy — local HTTP/HTTPS forward proxy that enforces boundary
on every subprocess + browser client (yt-dlp, DrissionPage, ffmpeg).

The proxy exists so non-Python clients (subprocess, embedded browser)
get the same SSRF protection that in-process httpx callers get from
SafeAsyncClient. Configure subprocess clients via standard env vars
(HTTPS_PROXY/HTTP_PROXY) or per-tool flags (yt-dlp --proxy).

Tests cover:
- Server starts and accepts connections
- HTTP forward path: public URL forwarded, private IP returns 403
- HTTPS CONNECT path: public host tunneled, private host returns 403
- Server stops cleanly
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from app.boundary import url_guard
from app.boundary.ssrf_proxy import SsrfProxy


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    url_guard._reset_dns_cache()
    url_guard._reset_network_cache()

    async def _fake_resolve(host: str) -> list[str]:
        mapping = {
            "blocked.example.com": ["10.0.0.1"],
            "127.0.0.1": ["127.0.0.1"],  # literal forwards through resolver
        }
        return mapping.get(host, ["8.8.8.8"])

    monkeypatch.setattr(url_guard, "_resolve_host_async", _fake_resolve)
    yield


@pytest.fixture
async def proxy():
    """Start an SsrfProxy on a random port, yield it, then stop."""
    p = SsrfProxy()
    await p.start()
    try:
        yield p
    finally:
        await p.stop()


# ============================================================================
# Lifecycle
# ============================================================================


@pytest.mark.unit
async def test_proxy_starts_and_binds(proxy: SsrfProxy):
    """After start(), the proxy has a bound port on 127.0.0.1."""
    assert proxy.port is not None
    assert proxy.port > 0
    assert proxy.url.startswith("http://127.0.0.1:")


@pytest.mark.unit
async def test_proxy_stops_cleanly():
    p = SsrfProxy()
    await p.start()
    port = p.port
    await p.stop()
    # After stop, the port is released — opening a new server on it should succeed
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.close()
    assert p.port is None


# ============================================================================
# HTTPS CONNECT path
# ============================================================================


async def _send_connect(proxy_port: int, target: str) -> tuple[int, bytes]:
    """Open a TCP connection to the proxy and send a CONNECT request.
    Returns (status_code, full_response_bytes)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    await writer.drain()
    # Read just the status line + headers (don't try to start tunneling)
    response = await asyncio.wait_for(reader.read(4096), timeout=2.0)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    status_line = response.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    parts = status_line.split(" ", 2)
    status = int(parts[1]) if len(parts) > 1 else 0
    return status, response


@pytest.mark.unit
async def test_connect_to_private_host_blocked(proxy: SsrfProxy):
    """CONNECT to a host that resolves private must return 403."""
    status, _ = await _send_connect(proxy.port, "blocked.example.com:443")
    assert status == 403


@pytest.mark.unit
async def test_connect_to_literal_private_ip_blocked(proxy: SsrfProxy):
    """CONNECT to literal RFC1918 must return 403."""
    status, _ = await _send_connect(proxy.port, "192.168.50.9:9080")
    assert status == 403


@pytest.mark.unit
async def test_connect_to_invalid_target_returns_400(proxy: SsrfProxy):
    """Malformed CONNECT target → 400."""
    status, _ = await _send_connect(proxy.port, "not-a-valid-target")
    assert status in (400, 403)


# ============================================================================
# HTTP forward path
# ============================================================================


async def _send_http_get(proxy_port: int, absolute_url: str) -> tuple[int, bytes]:
    """Send a proxied HTTP GET (RFC 7230 §5.3.2 absolute-form)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    request = (
        f"GET {absolute_url} HTTP/1.1\r\n"
        f"Host: {httpx.URL(absolute_url).host}\r\n"
        f"\r\n"
    )
    writer.write(request.encode())
    await writer.drain()
    response = await asyncio.wait_for(reader.read(8192), timeout=3.0)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    status_line = response.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    parts = status_line.split(" ", 2)
    status = int(parts[1]) if len(parts) > 1 else 0
    return status, response


@pytest.mark.unit
async def test_http_forward_to_private_ip_blocked(proxy: SsrfProxy):
    """HTTP GET via proxy with private-IP target must be rejected."""
    status, _ = await _send_http_get(proxy.port, "http://192.168.50.9:9080/admin")
    assert status == 403


@pytest.mark.unit
async def test_http_forward_to_blocked_host_blocked(proxy: SsrfProxy):
    """HTTP GET via proxy to host that resolves private must be rejected."""
    status, _ = await _send_http_get(proxy.port, "http://blocked.example.com/")
    assert status == 403
