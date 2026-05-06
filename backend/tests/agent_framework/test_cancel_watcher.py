"""cancel_watcher — bridge cancel signals to AbortController."""
from __future__ import annotations

import asyncio

import pytest

from app.agent_framework import AbortController
from app.agent_framework.cancel_watcher import watch_cancel_loop


class _FakeRecorder:
    def __init__(self, cancel_after_calls: int = 999) -> None:
        self._calls = 0
        self._cancel_after = cancel_after_calls

    async def check_cancelled(self) -> bool:
        self._calls += 1
        return self._calls >= self._cancel_after


@pytest.mark.unit
async def test_watcher_fires_abort_on_cancel():
    """Source returns True → abort fires."""
    rec = _FakeRecorder(cancel_after_calls=2)
    abort = AbortController()
    await asyncio.wait_for(
        watch_cancel_loop(rec, abort, poll_interval_seconds=0.01),
        timeout=1.0,
    )
    assert abort.is_aborted()
    assert "cancel_requested" in (abort.reason or "")


@pytest.mark.unit
async def test_watcher_returns_when_externally_aborted():
    """If someone else fires abort, watcher exits without further polls."""
    rec = _FakeRecorder()  # never returns True
    abort = AbortController()

    async def fire_externally():
        await asyncio.sleep(0.05)
        abort.fire(reason="external cancel")

    fire_task = asyncio.create_task(fire_externally())
    await asyncio.wait_for(
        watch_cancel_loop(rec, abort, poll_interval_seconds=0.01),
        timeout=1.0,
    )
    await fire_task
    assert abort.reason == "external cancel"


@pytest.mark.unit
async def test_watcher_handles_missing_check_cancelled():
    """Source without check_cancelled (or non-async) returns silently."""

    class NoMethod: ...

    abort = AbortController()
    # Should return immediately without raising
    await watch_cancel_loop(NoMethod(), abort, poll_interval_seconds=0.01)
    assert not abort.is_aborted()


@pytest.mark.unit
async def test_watcher_swallows_source_exceptions():
    """Source raising mid-poll doesn't kill the watcher; it keeps trying."""
    abort = AbortController()
    calls = {"n": 0}

    class FlakyRecorder:
        async def check_cancelled(self) -> bool:
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("DB blip")
            return True

    await asyncio.wait_for(
        watch_cancel_loop(FlakyRecorder(), abort, poll_interval_seconds=0.01),
        timeout=1.0,
    )
    assert abort.is_aborted()
    assert calls["n"] >= 3


@pytest.mark.unit
async def test_watcher_can_be_cancelled_by_caller():
    """task.cancel() lets the watcher exit cleanly. Watcher absorbs
    CancelledError so it completes successfully — the caller's typical
    `try/finally: watcher.cancel()` doesn't need to ignore CancelledError."""
    rec = _FakeRecorder()  # never True
    abort = AbortController()
    task = asyncio.create_task(watch_cancel_loop(rec, abort, poll_interval_seconds=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    # Watcher absorbs CancelledError → task completes cleanly with None
    result = await asyncio.wait_for(task, timeout=1.0)
    assert result is None
    assert not abort.is_aborted()  # cancel didn't fire abort
