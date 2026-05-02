"""AbortController — per-run cancel signal that interrupts in-flight
LLM calls (not just hooks between turns).

Today's mediahub agent_runner has cooperative cancel:
``recorder.check_cancelled()`` is polled between iterations of the
skill loop. This works for between-turns cancel but NOT during an
in-flight LLM call. If an LLM call takes 30 seconds, the user pressing
"cancel" doesn't interrupt — they wait the full 30 seconds.

This module adds the missing piece: an asyncio.Event-backed signal
that can short-circuit out of any awaited coroutine via
``race_until_abort``.

Mirrors OpenClaw ``gateway/chat-abort.ts``.

Usage from agent_runner (sketch — actual wire-up in a follow-up commit):

    abort = AbortController()

    # External watcher fires the signal:
    async def watch_db_cancel():
        while True:
            if await db.is_cancel_requested(session_id):
                abort.fire(reason="user cancel via cancel_requested")
                return
            await asyncio.sleep(0.5)

    asyncio.create_task(watch_db_cancel())

    # Adapter call wrapped (use ``runner.run_turn`` or
    # ``RunRecorder.run`` in practice — the LLM call inside the
    # adapter is what gets cancelled when ``abort.fire()`` happens):
    try:
        response = await race_until_abort(
            runner.run_turn(composed, user_messages, recorder=recorder),
            abort,
        )
    except RunAborted as e:
        # Surface to caller — already-cancelled hook chain will follow
        return {"content": "", "aborted": True, "abort_reason": str(e)}
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Optional


class RunAborted(Exception):
    """The abort signal fired before the wrapped task completed."""


class AbortController:
    """A one-shot async signal that callers race against in-flight work.

    Backed by ``asyncio.Event`` — ``wait_until_aborted()`` returns when
    ``fire()`` is called. Once fired, the controller stays fired forever
    (it's one-shot — create a new controller per run).
    """

    def __init__(self) -> None:
        self._event: asyncio.Event = asyncio.Event()
        self._reason: Optional[str] = None

    def fire(self, *, reason: Optional[str] = None) -> None:
        """Set the abort signal. Idempotent — calling again is a no-op
        (first reason wins). Safe to call from any coroutine on the
        same event loop."""
        if not self._event.is_set():
            self._reason = reason
            self._event.set()

    def is_aborted(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> Optional[str]:
        return self._reason

    async def wait_until_aborted(self) -> None:
        """Block until ``fire()`` is called. Returns silently when fired."""
        await self._event.wait()


async def race_until_abort(
    awaitable: Awaitable[Any],
    abort: AbortController,
) -> Any:
    """Run ``awaitable`` and return its result, OR raise ``RunAborted``
    if ``abort`` fires first.

    When abort wins, the underlying task is properly cancelled (not
    orphaned). When the task wins, the abort-watcher task is cancelled.

    If ``abort`` is already fired when this is called, raises ``RunAborted``
    immediately without scheduling the awaitable.
    """
    if abort.is_aborted():
        raise RunAborted(abort.reason or "aborted before start")

    # Wrap awaitable in a Task so we can cancel it cleanly
    task = asyncio.ensure_future(awaitable)
    abort_task = asyncio.ensure_future(abort.wait_until_aborted())

    done, pending = await asyncio.wait(
        {task, abort_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel whichever didn't finish (abort_task on success, task on abort)
    for p in pending:
        p.cancel()
        try:
            await p
        except (asyncio.CancelledError, BaseException):
            pass

    if task in done and not task.cancelled():
        # Normal completion — propagate the result (or exception).
        return task.result()

    # Abort won. Re-raise as RunAborted with the controller's reason.
    raise RunAborted(abort.reason or "aborted")
