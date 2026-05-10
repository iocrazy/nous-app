"""workflow_heartbeat — heartbeat infrastructure for DBOS workflows.

Why this exists
---------------
The workflow health classifier needs three independent signals to
distinguish "running long" from "wedged" from "worker died". Two of
them (progress, elapsed time) are already implicit in task_tracking.
The third — *heartbeat* — has to be written by the workflow body itself.

Without a heartbeat, the only way to detect a dead worker is to wait
for the wall-clock timeout, which forces the timeout to be tight (kills
legitimate long jobs) or loose (silent for hours after death). With a
heartbeat written every ~30s, we can mark the workflow LOST 5 minutes
after the worker dies regardless of what the wall-clock timeout says,
AND let the user run a 6-hour job in peace.

How callers use it
------------------
Wrap the body of a long-running step (or the whole workflow) in
``heartbeat_loop``::

    @DBOS.step()
    def transcribe_step(...):
        with heartbeat_loop(workflow_id=DBOS.workflow_id, interval_seconds=30):
            do_the_long_thing()

The context manager spawns a background thread that bumps
``task_tracking.heartbeat_at`` every ``interval_seconds``. On exit the
thread is signalled to stop and joined within 1s.

Why a thread, not asyncio
-------------------------
Most of our long steps are sync (ffmpeg subprocess, whisper inference,
yt-dlp). Asyncio heartbeats wouldn't get scheduled while a sync call
holds the GIL or blocks on subprocess I/O. A daemon thread bumps the
heartbeat reliably even when the main event loop is starved.
"""

from __future__ import annotations

import contextlib
import threading
from datetime import datetime, timezone
from typing import Iterator, Optional

from loguru import logger

# Tunable so tests can ratchet down. Production: 30s.
DEFAULT_INTERVAL_SECONDS = 30


def _write_heartbeat(workflow_id: str) -> None:
    """Write `heartbeat_at = now()` for the task_tracking row keyed on
    `dbos_workflow_id = workflow_id`. Errors are swallowed — a missed
    heartbeat just means the next sweep sees an older timestamp; no
    point taking the workflow down because of a transient PG hiccup.

    Sync function on purpose so the daemon thread doesn't need an
    asyncio loop.
    """
    try:
        from app.db import get_supabase_admin

        sb = get_supabase_admin()
        sb.table("task_tracking").update(
            {"heartbeat_at": datetime.now(timezone.utc).isoformat()}
        ).eq("dbos_workflow_id", workflow_id).execute()
    except Exception as e:
        logger.opt(exception=True).debug(
            f"[heartbeat] write failed for workflow_id={workflow_id}: {e}"
        )


@contextlib.contextmanager
def heartbeat_loop(
    *,
    workflow_id: str,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
) -> Iterator[None]:
    """Bump task_tracking.heartbeat_at every ``interval_seconds`` until
    the context exits.

    Safe to nest (each call spawns its own thread; outer thread keeps
    bumping if inner exits early). Thread is daemon, so a crashed
    workflow doesn't block process shutdown.

    Writes one heartbeat immediately on entry so the classifier sees a
    fresh timestamp even if the body finishes before the first interval.
    """
    if not workflow_id:
        # Defensive — caller passed empty string. Just no-op the heartbeat
        # so misconfigured wiring doesn't crash the workflow.
        yield
        return

    stop_event = threading.Event()

    def _runner() -> None:
        # Initial heartbeat so even sub-second steps register at least once.
        _write_heartbeat(workflow_id)
        while not stop_event.wait(interval_seconds):
            _write_heartbeat(workflow_id)

    thread = threading.Thread(
        target=_runner,
        name=f"heartbeat:{workflow_id[:8]}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        # 1s join — if the thread is wedged on a PG write, just leak
        # (it's a daemon, will die with the process).
        thread.join(timeout=1.0)
        # Final heartbeat on clean exit so the classifier sees the
        # "still alive at completion" timestamp before the row's phase
        # transitions to completed.
        _write_heartbeat(workflow_id)


@contextlib.asynccontextmanager
async def async_heartbeat_loop(
    *,
    workflow_id: str,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
) -> "Iterator[None]":
    """Async variant for purely-async workflow bodies. Same semantics as
    ``heartbeat_loop``; uses asyncio.Event + asyncio.create_task so it
    cooperates with the event loop instead of running a thread.

    Prefer the sync variant when the body contains ANY blocking call
    (subprocess.run, time.sleep, sync DB queries) — those will starve
    the asyncio loop and the heartbeat will skip beats.
    """
    import asyncio

    if not workflow_id:
        yield
        return

    stop_event: Optional[asyncio.Event] = asyncio.Event()

    async def _runner() -> None:
        _write_heartbeat(workflow_id)
        while True:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                return  # stop signalled
            except asyncio.TimeoutError:
                _write_heartbeat(workflow_id)

    task = asyncio.create_task(_runner(), name=f"heartbeat:{workflow_id[:8]}")
    try:
        yield
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()
        _write_heartbeat(workflow_id)


__all__ = [
    "heartbeat_loop",
    "async_heartbeat_loop",
    "DEFAULT_INTERVAL_SECONDS",
]
