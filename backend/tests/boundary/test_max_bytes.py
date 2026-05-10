"""Sprint 7 — bounded-byte readers."""

from __future__ import annotations

import io

import pytest

from app.boundary.errors import MaxBytesExceededError
from app.boundary.max_bytes import (
    aread_with_cap,
    cap_aiter,
    cap_iter,
    read_with_cap,
)

# ─── Sync read_with_cap ───────────────────────────────────────────────


@pytest.mark.unit
def test_under_cap_returns_full_buffer():
    payload = b"hello"
    result = read_with_cap(io.BytesIO(payload), max_bytes=10)
    assert result == payload


@pytest.mark.unit
def test_at_cap_exact_returns():
    payload = b"hello"
    result = read_with_cap(io.BytesIO(payload), max_bytes=5)
    assert result == payload


@pytest.mark.unit
def test_over_cap_raises():
    payload = b"hello world"
    with pytest.raises(MaxBytesExceededError, match="cap"):
        read_with_cap(io.BytesIO(payload), max_bytes=5)


@pytest.mark.unit
def test_negative_cap_rejected():
    with pytest.raises(ValueError, match=">= 0"):
        read_with_cap(io.BytesIO(b""), max_bytes=-1)


@pytest.mark.unit
def test_zero_cap_only_allows_empty():
    assert read_with_cap(io.BytesIO(b""), max_bytes=0) == b""
    with pytest.raises(MaxBytesExceededError):
        read_with_cap(io.BytesIO(b"x"), max_bytes=0)


# ─── Async aread_with_cap ─────────────────────────────────────────────


class _AsyncBytesIO:
    """Minimal async-readable wrapper over BytesIO."""

    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    async def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)


@pytest.mark.asyncio
async def test_async_under_cap():
    result = await aread_with_cap(_AsyncBytesIO(b"hi"), max_bytes=10)
    assert result == b"hi"


@pytest.mark.asyncio
async def test_async_over_cap_raises():
    with pytest.raises(MaxBytesExceededError):
        await aread_with_cap(_AsyncBytesIO(b"hello world"), max_bytes=5)


# ─── cap_iter / cap_aiter ─────────────────────────────────────────────


@pytest.mark.unit
def test_cap_iter_yields_until_overrun():
    chunks = [b"abc", b"def", b"ghi"]
    out = list(cap_iter(iter(chunks), max_bytes=10))
    assert out == chunks  # 9 bytes total, under cap


@pytest.mark.unit
def test_cap_iter_raises_on_overrun_chunk():
    """Overrunning chunk raises BEFORE being yielded — the caller never
    sees the offending bytes (important for streaming-to-disk)."""
    chunks = [b"abc", b"defghij"]
    gen = cap_iter(iter(chunks), max_bytes=5)
    assert next(gen) == b"abc"  # 3 bytes, under
    with pytest.raises(MaxBytesExceededError):
        next(gen)  # 3+7=10 > 5


async def _aiter(items):
    for x in items:
        yield x


@pytest.mark.asyncio
async def test_cap_aiter_yields_until_overrun():
    out = []
    async for chunk in cap_aiter(_aiter([b"a", b"b", b"c"]), max_bytes=10):
        out.append(chunk)
    assert out == [b"a", b"b", b"c"]


@pytest.mark.asyncio
async def test_cap_aiter_raises_on_overrun():
    received: list[bytes] = []
    with pytest.raises(MaxBytesExceededError):
        async for chunk in cap_aiter(_aiter([b"abc", b"defghij"]), max_bytes=5):
            received.append(chunk)
    # Caller saw the safe chunk but NOT the overrun chunk.
    assert received == [b"abc"]


@pytest.mark.unit
def test_cap_iter_negative_cap_rejected():
    with pytest.raises(ValueError):
        list(cap_iter(iter([b"x"]), max_bytes=-1))
