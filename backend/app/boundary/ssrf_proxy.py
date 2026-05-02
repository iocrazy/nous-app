"""SsrfProxy — local HTTP/HTTPS forward proxy that enforces the boundary
on every subprocess + browser client (yt-dlp, DrissionPage, ffmpeg).

The unified L3 enforcement point for non-Python clients. Subprocess
clients connect to this proxy via standard env vars (HTTPS_PROXY,
HTTP_PROXY) or per-tool flags (yt-dlp ``--proxy``).

Architecture:
- Listens on 127.0.0.1:<random_port>
- HTTP forward path: parses absolute-form request line, validates target
  via SafeAsyncClient (which handles redirect re-validation, cross-origin
  header strip)
- HTTPS CONNECT path: parses CONNECT host:port, validates host via
  PinnedDNSResolver (gives the IP), opens TCP tunnel to that IP, pipes
  encrypted bytes both ways. Cannot inspect TLS payload but the IP check
  is what blocks SSRF
- Lifecycle: start() / stop() — owned by app.main lifespan

See docs/architecture/boundary-layer.md (Layer 3 variant).
"""
from __future__ import annotations

import asyncio
from typing import Optional

import httpx
from loguru import logger

from app.boundary.errors import URLBlockedError
from app.boundary.pinned_dns import PinnedDNSResolver
from app.boundary.safe_http import safe_async_client


class SsrfProxy:
    """Local HTTP/HTTPS forward proxy.

    Started once per process via ``await proxy.start()``. The proxy
    binds to 127.0.0.1 on a random port (or a fixed port if ``port``
    given). Stop with ``await proxy.stop()``.

    Public attributes after start():
    - ``port``: the actual bound port
    - ``url``: the full proxy URL (http://127.0.0.1:port)
    """

    READ_BUFFER = 8192

    def __init__(self, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._requested_port = port
        self._server: Optional[asyncio.AbstractServer] = None
        self._port: Optional[int] = None
        self._pinner = PinnedDNSResolver()

    @property
    def port(self) -> Optional[int]:
        return self._port

    @property
    def url(self) -> str:
        if self._port is None:
            return ""
        return f"http://{self._host}:{self._port}"

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._requested_port
        )
        sockets = self._server.sockets or []
        if not sockets:
            await self._server.wait_closed()
            self._server = None
            raise RuntimeError("SsrfProxy failed to bind a socket")
        self._port = sockets[0].getsockname()[1]
        logger.info(f"[boundary] SsrfProxy listening on {self.url}")

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self._port = None

    # ------------------------------------------------------------------
    # Connection handler
    # ------------------------------------------------------------------

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        except asyncio.TimeoutError:
            await self._reply(writer, 408, "Request Timeout")
            return

        if not request_line:
            await self._close(writer)
            return

        try:
            method, target, version = request_line.decode("ascii").rstrip().split(" ", 2)
        except (ValueError, UnicodeDecodeError):
            await self._reply(writer, 400, "Bad Request")
            return

        # Read remaining request headers (until empty line)
        headers: dict[str, str] = {}
        while True:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            except asyncio.TimeoutError:
                await self._reply(writer, 408, "Request Timeout")
                return
            if line in (b"\r\n", b"\n", b""):
                break
            try:
                k, _, v = line.decode("ascii", errors="replace").partition(":")
                headers[k.strip()] = v.strip()
            except Exception:
                pass

        method_upper = method.upper()
        try:
            if method_upper == "CONNECT":
                await self._handle_connect(target, reader, writer)
            else:
                await self._handle_http(method_upper, target, headers, reader, writer)
        except Exception as exc:
            logger.warning(
                f"[boundary] SsrfProxy handler error ({method_upper}): "
                f"{type(exc).__name__}: {exc}"
            )
            try:
                await self._reply(writer, 500, "Internal Proxy Error")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # CONNECT (HTTPS tunnel)
    # ------------------------------------------------------------------

    async def _handle_connect(
        self,
        target: str,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        if ":" not in target:
            await self._reply(client_writer, 400, "Bad Request")
            return

        host, _, port_str = target.rpartition(":")
        try:
            port = int(port_str)
        except ValueError:
            await self._reply(client_writer, 400, "Bad Request")
            return

        if not host or port <= 0 or port > 65535:
            await self._reply(client_writer, 400, "Bad Request")
            return

        try:
            ip = await self._pinner.resolve_and_validate(host)
        except URLBlockedError as e:
            logger.info(f"[boundary] SsrfProxy CONNECT blocked: {target} ({e})")
            await self._reply(client_writer, 403, "Forbidden")
            return
        except Exception as e:
            logger.warning(f"[boundary] SsrfProxy CONNECT resolve error: {e}")
            await self._reply(client_writer, 502, "Bad Gateway")
            return

        try:
            target_reader, target_writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=10.0
            )
        except (OSError, asyncio.TimeoutError) as e:
            logger.warning(f"[boundary] SsrfProxy CONNECT upstream failed: {e}")
            await self._reply(client_writer, 502, "Bad Gateway")
            return

        # Tunnel established
        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        try:
            await client_writer.drain()
        except Exception:
            return

        await asyncio.gather(
            self._pipe(client_reader, target_writer),
            self._pipe(target_reader, client_writer),
            return_exceptions=True,
        )
        await self._close(target_writer)
        await self._close(client_writer)

    # ------------------------------------------------------------------
    # HTTP forward
    # ------------------------------------------------------------------

    async def _handle_http(
        self,
        method: str,
        target: str,
        headers: dict[str, str],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        # Absolute-form per RFC 7230 §5.3.2 — proxies receive full URLs.
        # Reject relative-form (typically only used by origin servers).
        if not target.startswith(("http://", "https://")):
            await self._reply(writer, 400, "Bad Request — absolute URL required")
            return

        content_length = 0
        try:
            content_length = int(headers.get("Content-Length", 0))
        except ValueError:
            content_length = 0

        body: bytes | None = None
        if content_length > 0:
            try:
                body = await asyncio.wait_for(
                    reader.readexactly(content_length), timeout=30.0
                )
            except (asyncio.IncompleteReadError, asyncio.TimeoutError):
                await self._reply(writer, 400, "Bad Request — body truncated")
                return

        # Strip hop-by-hop headers per RFC 7230 §6.1
        hop_by_hop = {
            "connection", "keep-alive", "proxy-authenticate",
            "proxy-authorization", "te", "trailers", "transfer-encoding",
            "upgrade", "host", "proxy-connection",
        }
        forward_headers = {
            k: v for k, v in headers.items() if k.lower() not in hop_by_hop
        }

        try:
            async with safe_async_client() as client:
                response = await client.request(
                    method=method,
                    url=target,
                    headers=forward_headers,
                    content=body,
                )
        except URLBlockedError as e:
            logger.info(f"[boundary] SsrfProxy HTTP blocked: {target} ({e})")
            await self._reply(writer, 403, "Forbidden")
            return
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            logger.warning(f"[boundary] SsrfProxy upstream error: {e}")
            await self._reply(writer, 502, "Bad Gateway")
            return

        # Build proxied response — strip hop-by-hop response headers too
        status_line = (
            f"HTTP/1.1 {response.status_code} "
            f"{response.reason_phrase or 'OK'}\r\n"
        ).encode("ascii")
        writer.write(status_line)
        for k, v in response.headers.items():
            if k.lower() in hop_by_hop:
                continue
            writer.write(f"{k}: {v}\r\n".encode("ascii", errors="replace"))
        # Force Content-Length so client knows where the response ends
        writer.write(f"Content-Length: {len(response.content)}\r\n".encode())
        writer.write(b"Connection: close\r\n\r\n")
        writer.write(response.content)
        try:
            await writer.drain()
        except Exception:
            pass
        await self._close(writer)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _reply(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        reason: str,
    ) -> None:
        body = reason.encode("ascii", errors="replace")
        try:
            writer.write(
                (
                    f"HTTP/1.1 {status} {reason}\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                ).encode("ascii")
                + body
            )
            await writer.drain()
        except Exception:
            pass
        await self._close(writer)

    async def _pipe(
        self,
        src: asyncio.StreamReader,
        dst: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                data = await src.read(self.READ_BUFFER)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except (ConnectionError, asyncio.CancelledError, BrokenPipeError):
            pass
        except Exception as exc:
            logger.debug(
                f"[boundary] SsrfProxy pipe error: {type(exc).__name__}: {exc}"
            )
        finally:
            try:
                if dst.can_write_eof():
                    dst.write_eof()
            except Exception:
                pass

    async def _close(self, writer: asyncio.StreamWriter) -> None:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
