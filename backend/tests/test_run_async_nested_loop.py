"""Regression tests for run_async cross-context safety.

Background:
After PR #247 made bilibili URLs flow into `_do_ytdlp_download`,
download_workflow (async DBOS workflow) → run_download_step (sync
DBOS step that DBOS did not offload to a worker thread) →
`run_async(YtdlpService.download_video(...))` raised:

    RuntimeError: asyncio.run() cannot be called from a running event loop

The fix: when run_async detects a running loop on the calling thread,
it executes the coroutine in a worker thread (which gets its own fresh
event loop), instead of failing.
"""

from __future__ import annotations

import asyncio

import pytest

from app.tasks.utils import run_async


async def _trivial_coro(value: int) -> int:
    await asyncio.sleep(0)
    return value * 2


def test_run_async_no_running_loop():
    """Top-level sync (Celery/script) — direct asyncio.run path."""
    assert run_async(_trivial_coro(3)) == 6


async def test_run_async_from_inside_running_loop():
    """Sync run_async called from inside a running async test — bounces
    to a worker thread instead of raising. This mirrors the exact
    download_strategies → run_async path that breaks bilibili."""
    # asyncio.get_running_loop() succeeds here — we're inside pytest-asyncio's loop.
    assert run_async(_trivial_coro(5)) == 10


async def test_run_async_propagates_exception_from_running_loop():
    """Worker thread should still surface coroutine exceptions to caller."""

    async def boom() -> None:
        raise ValueError("nested-loop probe")

    with pytest.raises(ValueError, match="nested-loop probe"):
        run_async(boom())
