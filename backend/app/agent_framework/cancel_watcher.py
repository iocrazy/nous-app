"""cancel_watcher — bridge cancel signals to AbortController.

Today RunRecorder.check_cancelled polls a DB row (ai_sessions.cancel_
requested) every iteration of the agent loop. That works for between-
turns cancel but the in-flight LLM call doesn't see it.

This module fires the AbortController whenever the source signal flips,
so AgentRunner.run_turn (with abort=...) interrupts mid-call.

Usage in chat service / workflow caller:

    from app.agent_framework import AbortController
    from app.agent_framework.cancel_watcher import watch_cancel_loop

    abort = AbortController()
    # Spawn watcher coroutine; cancel it when run completes
    watcher = asyncio.create_task(watch_cancel_loop(recorder, abort))
    try:
        result = await runner.run_turn(
            composed, user_messages, recorder=recorder, abort=abort
        )
    finally:
        watcher.cancel()
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from loguru import logger

from app.agent_framework.abort_controller import AbortController

# A "cancel source" is any async callable that returns True when the run
# should be aborted. RunRecorder.check_cancelled fits this shape.
CancelSource = Callable[[], Awaitable[bool]]


async def watch_cancel_loop(
    cancel_source: Any,
    abort: AbortController,
    *,
    poll_interval_seconds: float = 0.5,
) -> None:
    """Poll ``cancel_source.check_cancelled()`` and fire ``abort`` when True.

    Designed to run as a background task alongside ``runner.run_turn``.
    Returns silently when:
      - abort is fired (by anyone — us or external)
      - the watcher task is cancelled by the caller
      - source raises an exception (logged at DEBUG)

    Defensive: missing or non-async ``check_cancelled`` is treated as
    "no cancel source", returns silently.
    """
    check = getattr(cancel_source, "check_cancelled", None)
    if check is None or not asyncio.iscoroutinefunction(check):
        return

    try:
        while not abort.is_aborted():
            try:
                if await check():
                    abort.fire(reason="user cancel via cancel_requested")
                    return
            except Exception as exc:
                logger.debug(
                    f"[cancel_watcher] source raised "
                    f"{type(exc).__name__}: {exc} — continuing poll"
                )
            await asyncio.sleep(poll_interval_seconds)
    except asyncio.CancelledError:
        return  # caller stopped us; don't propagate
