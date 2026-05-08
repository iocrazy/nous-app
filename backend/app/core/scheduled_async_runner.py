"""Process-wide event loop for DBOS scheduled-workflow steps.

Why this exists
───────────────
DBOS dispatches sync-decorated workflows on its `_executor` (a worker
ThreadPoolExecutor). Inside that worker thread there is no event loop.
Our scheduled workflows want to run async step bodies (`@DBOS.step`
async functions, which call async supabase / async repository code),
so they need *some* loop.

Three patterns we tried for "create a loop, run, throw it away" all
broke prod (#185, #186, #187):

  - ``asyncio.run(coro)`` — its cleanup calls
    ``loop.run_until_complete(loop.shutdown_default_executor())``. Under
    Python 3.13 + DBOS's ``_configure_asyncio_thread_pool`` hook (which
    mutates the current loop's ``_default_executor`` to point at DBOS's
    process-shared ``_executor``), this ends up shutting down the
    DBOS pool the entire process depends on. Result: ``cannot schedule
    new futures after shutdown`` raised by every subsequent
    ``verify_jwt`` and DBOS queue dispatch.

  - ``new_event_loop()`` + ``loop.close()`` — same shutdown path inside
    ``close()``. Same cascade.

  - ``new_event_loop()`` + ``set_default_executor(None)`` + ``close()``
    — should detach DBOS's pool before close, but empirically still
    cascaded (likely because step bodies are dispatched to the *main*
    uvicorn loop via ``BackgroundEventLoop.submit_coroutine`` and the
    cleanup path we control isn't the loop where DBOS sets the
    executor). Reverted in #188.

The pattern that actually works: **never close the loop**. Spin up
one daemon thread + one event loop at process start, hand it
coroutines via ``run_coroutine_threadsafe``, block for results. The
loop runs forever; nothing ever calls ``shutdown_default_executor``;
DBOS's pool is never shut down.

Public API: ``run_in_scheduled_loop(coro)``.
"""

import asyncio
import threading
from typing import Any, Coroutine, Optional, TypeVar

from loguru import logger

T = TypeVar("T")

_loop: Optional[asyncio.AbstractEventLoop] = None
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Start the daemon thread + loop on first call. Idempotent."""
    global _loop, _thread

    # Fast path: already running.
    if _loop is not None and _loop.is_running():
        return _loop

    with _lock:
        if _loop is not None and _loop.is_running():
            return _loop

        ready = threading.Event()

        def _runner() -> None:
            global _loop
            _loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_loop)
            ready.set()
            try:
                _loop.run_forever()
            except Exception:
                logger.exception("[scheduled-async] loop crashed unexpectedly")
            finally:
                # In normal operation we never reach here — the daemon
                # thread dies with the process. Don't call close(); it
                # would shutdown _default_executor, defeating the whole
                # point of this module. Just let the process tear down.
                pass

        _thread = threading.Thread(
            target=_runner,
            daemon=True,
            name="scheduled-async-loop",
        )
        _thread.start()
        ready.wait(timeout=5.0)

        if _loop is None or not _loop.is_running():
            raise RuntimeError(
                "scheduled_async_runner failed to start within 5s"
            )

        return _loop


def run_in_scheduled_loop(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on the process-wide scheduled-async loop.

    Blocks the caller (must be a sync function — typically a DBOS
    scheduled-workflow body running on a DBOS executor thread) until
    the coroutine completes. Exceptions propagate.

    Do NOT call this from inside the scheduled-async loop itself — it
    would deadlock waiting on the same loop. It is safe to call from
    any other thread.
    """
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()
