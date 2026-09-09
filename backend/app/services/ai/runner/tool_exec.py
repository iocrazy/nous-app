"""The ONE way a runner awaits a tool handler (phase 2b-1 §3).

``run_tool_with_timeout`` runs the handler as a task and waits at most the
tool's wall-clock limit (``asyncio.wait`` — the task is never cancelled by
the wait itself, so the outcome is decided by OUR clock, not by any
``TimeoutError`` the handler raises internally):

* finished in time → its return value, or its own exception re-raised
  UNCHANGED (each call site keeps its typed ``except``; an inner
  ``TimeoutError`` from e.g. ffmpeg is the handler's error, not ours);
* expired → the handler is cancelled and awaited to quiescence (bounded by
  ``CLEANUP_GRACE_S`` so a stuck cleanup cannot hang the turn), then the
  typed result below is returned — even if the handler swallowed the
  cancel and produced a late value (logged, discarded):

    {"error": "timeout", "timed_out": true, "timeout_s": 60, "elapsed_s": 60.0,
     "tool": "ResourceFetch", "message": "Tool ResourceFetch timed out after
     60s. Retry once with a narrower request, or choose another way."}

* the run itself cancelled while waiting → the handler is cancelled and
  given the same bounded grace before the cancellation propagates.

``limit=None`` (AskUser / FinishIssue) waits without a clock. Source guard:
tests/runner/test_tool_await_source_guard.py pins that every handler await in
agent_runner goes through here.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Optional

from loguru import logger

from app.services.ai.runner.tool_timeouts import resolve_timeout

# How long a cancelled handler gets to finish its cleanup (kill_tree etc.).
CLEANUP_GRACE_S = 5.0
_UNSET = object()


def timeout_message(tool_name: str, limit: float) -> str:
    return (
        f"Tool {tool_name} timed out after {limit:g}s. Retry once with a "
        f"narrower request, or choose another way."
    )


async def _settle(task: "asyncio.Task[Any]", tool_name: str) -> None:
    """Cancel and wait (bounded) for the handler to reach quiescence."""
    if not task.done():
        task.cancel()
    await asyncio.wait({task}, timeout=CLEANUP_GRACE_S)
    if not task.done():
        logger.error(
            f"[tool_exec] {tool_name} did not finish cleanup within "
            f"{CLEANUP_GRACE_S}s after cancel; it may leave an orphan"
        )
    elif not task.cancelled() and task.exception() is None:
        logger.warning(
            f"[tool_exec] {tool_name} swallowed the cancel and returned late; "
            f"the value is discarded"
        )


async def run_tool_with_timeout(
    tool_name: str,
    coro: Awaitable[Any],
    *,
    timeout_s: Any = _UNSET,
) -> Any:
    limit: Optional[float] = (
        resolve_timeout(tool_name) if timeout_s is _UNSET else timeout_s
    )
    task = asyncio.ensure_future(coro)
    t0 = time.monotonic()
    try:
        done, _ = await asyncio.wait({task}, timeout=limit)
    except asyncio.CancelledError:
        await _settle(task, tool_name)
        raise
    if task in done:
        return task.result()  # value, or the handler's own exception unchanged
    await _settle(task, tool_name)
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
        "message": timeout_message(tool_name, float(limit)),  # type: ignore[arg-type]
    }


__all__ = ["CLEANUP_GRACE_S", "run_tool_with_timeout", "timeout_message"]
