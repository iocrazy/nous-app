"""Bounded reader — cap byte volume on external streams.

Sprint 7 (P2 safety bundle). Whenever we accept content from outside
the trust boundary (HTTP response body, uploaded file, websocket
frame), the upstream may send arbitrarily large payloads. Without a
cap, a single attacker request can exhaust memory / disk / CPU.

This module provides:

  - ``read_with_cap(stream, max_bytes)``    — read sync stream up to N bytes
  - ``aread_with_cap(astream, max_bytes)``  — same for async
  - ``cap_aiter(aiter, max_bytes)``         — wrap an async iterator,
    yields chunks until budget exhausted

Rationale for separate sync + async variants: ``httpx.Response.aread()``
returns bytes natively while ``httpx.Response.aiter_bytes()`` returns an
async iterator. Both shapes show up in the codebase, and forcing one
into the other adds latency / memory pressure.

On overrun, ``MaxBytesExceededError`` is raised. Caller should treat
the partial buffer as untrusted and discard — partial reads are a
common path for parsers (JSON / image headers) to mis-decode.

Defense-in-depth note: this is independent of httpx ``follow_redirects``
or content-length validation. Some upstreams lie about content-length
or stream forever via chunked transfer; this layer catches both.
"""
from __future__ import annotations

from typing import AsyncIterator, BinaryIO, Iterable, Protocol

from app.boundary.errors import MaxBytesExceededError


# Default ceiling — generous for most use cases. Callers should pass a
# tighter cap when they know the expected size (e.g. avatar upload =
# 1 MiB, transcript = 10 MiB).
DEFAULT_MAX_BYTES = 25 * 1024 * 1024  # 25 MiB


def read_with_cap(stream: BinaryIO, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
    """Read up to ``max_bytes`` from ``stream``. Raise if the stream has
    more than that available.

    Reads ``max_bytes + 1`` to detect overrun deterministically — a
    plain ``stream.read(max_bytes)`` would return exactly N bytes even
    when the upstream had N+10 to give, hiding the overrun.
    """
    if max_bytes < 0:
        raise ValueError(f"max_bytes must be >= 0 (got {max_bytes})")
    buf = stream.read(max_bytes + 1)
    if len(buf) > max_bytes:
        raise MaxBytesExceededError(
            f"stream exceeded byte cap ({len(buf)} > {max_bytes})"
        )
    return buf


class _AsyncReadable(Protocol):
    async def read(self, n: int = -1) -> bytes: ...


async def aread_with_cap(
    stream: _AsyncReadable, max_bytes: int = DEFAULT_MAX_BYTES
) -> bytes:
    """Async variant of ``read_with_cap``. Same overrun semantics."""
    if max_bytes < 0:
        raise ValueError(f"max_bytes must be >= 0 (got {max_bytes})")
    buf = await stream.read(max_bytes + 1)
    if len(buf) > max_bytes:
        raise MaxBytesExceededError(
            f"stream exceeded byte cap ({len(buf)} > {max_bytes})"
        )
    return buf


async def cap_aiter(
    aiter: AsyncIterator[bytes], max_bytes: int = DEFAULT_MAX_BYTES
) -> AsyncIterator[bytes]:
    """Wrap an async iterator of byte chunks. Yields chunks until the
    cumulative byte count would exceed ``max_bytes``; raises immediately
    on overrun without yielding the offending chunk.

    Use when you want to STREAM a bounded body to disk / a parser
    rather than buffer the whole thing in memory.
    """
    if max_bytes < 0:
        raise ValueError(f"max_bytes must be >= 0 (got {max_bytes})")
    seen = 0
    async for chunk in aiter:
        seen += len(chunk)
        if seen > max_bytes:
            raise MaxBytesExceededError(
                f"stream exceeded byte cap ({seen} > {max_bytes})"
            )
        yield chunk


def cap_iter(
    src: Iterable[bytes], max_bytes: int = DEFAULT_MAX_BYTES
) -> Iterable[bytes]:
    """Sync iterator variant. Same overrun semantics."""
    if max_bytes < 0:
        raise ValueError(f"max_bytes must be >= 0 (got {max_bytes})")
    seen = 0
    for chunk in src:
        seen += len(chunk)
        if seen > max_bytes:
            raise MaxBytesExceededError(
                f"stream exceeded byte cap ({seen} > {max_bytes})"
            )
        yield chunk


__all__ = [
    "DEFAULT_MAX_BYTES",
    "aread_with_cap",
    "cap_aiter",
    "cap_iter",
    "read_with_cap",
]
