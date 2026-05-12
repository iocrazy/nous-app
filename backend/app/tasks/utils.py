"""Shared utilities for running async code from sync contexts."""

import asyncio
import concurrent.futures


def run_async(coro):
    """Run an async coroutine from a synchronous context.

    Two contexts to support:

    1. **No running loop** (Celery worker, top-level script):
       `asyncio.run(coro)` directly. Creates a fresh loop per call.

    2. **Running loop on the calling thread** (DBOS sync `@DBOS.step`
       invoked from an async `@DBOS.workflow` — DBOS does not always
       offload sync steps to a worker thread, so the step body inherits
       the workflow's event loop):
       `asyncio.run` would raise `RuntimeError: asyncio.run() cannot be
       called from a running event loop`. Bounce to a worker thread that
       gets its own fresh loop, then block on the result.

    Symptom that motivated the dual path: after PR #247 made bilibili
    URLs reach `_do_ytdlp_download` (which calls run_async), every
    download failed 3x with the nested-loop RuntimeError and DBOS
    surfaced `DBOSMaxStepRetriesExceeded`.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Running loop on this thread — isolate via a worker.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
