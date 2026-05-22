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

    Connection-leak guard (2026-05-22 prod incident)
    -------------------------------------------------
    Every call here spins up a *fresh* event loop, and
    ``AsyncSupabaseClient`` caches one httpx-backed client per loop. A
    throwaway loop's client was never ``aclose()``d — Python GC reclaims
    the ``WeakKeyDictionary`` entry but does **not** close the underlying
    sockets — so each call leaked its open connections to Supabase/Kong.
    Over ~2 days the gateway exhausted the ephemeral port range
    (32768-60999) and every new Supabase REST call failed with
    ``[Errno 99] Cannot assign requested address`` (surfaced as httpx
    "All connection attempts failed"; the Engine status badge flipped to
    Offline). Wrapping the coroutine so its loop drains the per-loop
    Supabase clients in a ``finally`` closes the leak at the source —
    every transient loop now releases its sockets before being torn down.

    All callers MUST route through this helper rather than calling
    ``asyncio.run()`` directly, otherwise the leak reappears.
    """

    async def _runner():
        try:
            return await coro
        finally:
            # Drain THIS loop's Supabase clients before it is torn down.
            # Imported lazily so this module stays dependency-free (it is
            # imported very early by sync call sites). ``close()`` is
            # already exception-safe internally.
            from app.db.supabase_client import AsyncSupabaseClient

            await AsyncSupabaseClient.close()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_runner())

    # Running loop on this thread — isolate via a worker that gets its own
    # fresh loop. ``_runner`` builds the coroutine here; it is awaited (and
    # drained) inside the worker's loop.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _runner()).result()
