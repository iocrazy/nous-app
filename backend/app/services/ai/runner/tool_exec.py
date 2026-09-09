"""The ONE way a runner awaits a tool handler (phase 2b-1 §3).

``run_tool_with_timeout`` wraps the handler coroutine in the tool's
wall-clock limit. On timeout the handler task is cancelled AND awaited to
quiescence (a subprocess-backed tool runs its CancelledError cleanup —
kill_tree — before we return), then a typed result is returned:

    {"error": "timeout", "timed_out": true, "timeout_s": 60, "elapsed_s": 60.0,
     "tool": "ResourceFetch"}

A handler's own exception propagates unchanged (each call site keeps its
typed ``except``); an outer cancellation (the run being cancelled) propagates.
Source guard: tests/runner/test_tool_await_source_guard.py pins that every
handler await in agent_runner goes through here.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Optional

from loguru import logger

from app.services.ai.runner.tool_timeouts import resolve_timeout


async def run_tool_with_timeout(
    tool_name: str, coro: Awaitable[Any], *, timeout_s: Optional[float] = None
) -> Any:
    limit = float(timeout_s if timeout_s is not None else resolve_timeout(tool_name))
    task = asyncio.ensure_future(coro)
    t0 = time.monotonic()
    try:
        return await asyncio.wait_for(task, timeout=limit)
    except asyncio.TimeoutError:
        # wait_for cancels the task on timeout but (py<3.12) may return before
        # the task has actually finished its cleanup; drive it to quiescence.
        if not task.done():
            task.cancel()
        try:
            await task
        except BaseException:  # noqa: BLE001 — cancel or cleanup error
            pass
        elapsed = round(time.monotonic() - t0, 3)
        logger.warning(
            f"[tool_exec] {tool_name} timed out after {elapsed}s (limit {limit}s)"
        )
        return {
            "error": "timeout",
            "timed_out": True,
            "timeout_s": limit,
            "elapsed_s": elapsed,
            "tool": tool_name,
        }
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        raise
    # A handler's own exception propagates unchanged: each call site keeps
    # its typed ``except`` (ResourceFetch / MCP transport / ...) — this wrapper
    # only owns the wall clock.


__all__ = ["run_tool_with_timeout"]
