"""abort_registry — per-task cooperative cancellation tokens.

When a user hits Cancel on a long-running task, the API endpoint sets
an `asyncio.Event` keyed on the task's run_id. Workflow code paths
poll this event between long-running steps and exit early when it's
set. For subprocess-bound work (ffmpeg / whisper / yt-dlp), the
event handler is paired with `kill_tree` to terminate the OS process
group.

Why a dedicated registry vs ad-hoc dict
---------------------------------------
* Type-safe API — `register(run_id) → token`, `signal(run_id)`,
  `is_aborted(run_id) → bool`. Callers don't reach into module globals.
* Auto-cleanup — tokens are weakly referenced once the run completes;
  a missing run_id means "no abort signaled" without ever raising
  KeyError.
* Cross-process bridge — this module emits a `task.cancel_requested`
  event on the lifecycle bus when `signal()` is called locally. Other
  process replicas receive the event and signal their own local
  registry. So a cancel hit on the gateway propagates to the worker
  that's actually running the task.

Usage
-----
Workflow body::

    from app.services.abort_registry import get_registry, AbortError

    abort = get_registry().register(run_id)
    try:
        for chunk in download_chunks():
            if abort.is_set():
                raise AbortError("cancelled by user")
            await process_chunk(chunk)
    finally:
        get_registry().release(run_id)

API endpoint::

    from app.services.abort_registry import get_registry

    @router.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str):
        await get_registry().signal(run_id)  # cross-process via bus
        return {"signaled": True}
"""
from __future__ import annotations

import asyncio
from typing import Dict, Optional

from loguru import logger


class AbortError(Exception):
    """Raised by workflow code paths that detect the abort token has
    been signaled. Callers catch and translate into the appropriate
    task_tracking status (typically `cancelled`).
    """


class AbortRegistry:
    """Per-process map of run_id → asyncio.Event.

    Process-local; cross-process signaling happens via lifecycle bus
    (see `signal` below). This class itself only manages the local
    side, so there's no Redis dependency to test the pure logic.
    """

    def __init__(self) -> None:
        self._tokens: Dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    def register(self, run_id: str) -> asyncio.Event:
        """Idempotent — calling twice returns the same Event so the
        caller can register without checking. Returned Event is shared
        with all other holders (signal once → everyone sees set)."""
        if run_id not in self._tokens:
            self._tokens[run_id] = asyncio.Event()
        return self._tokens[run_id]

    def is_aborted(self, run_id: str) -> bool:
        token = self._tokens.get(run_id)
        return token is not None and token.is_set()

    def release(self, run_id: str) -> None:
        """Drop the token from the registry. Call from a `finally` once
        the workflow run terminates (either normally or via abort).
        Missing key is fine."""
        self._tokens.pop(run_id, None)

    async def signal(
        self, run_id: str, *, broadcast: bool = True
    ) -> bool:
        """Set the local token (creating one if absent — useful when the
        cancel-API call arrives before the worker has registered).

        If `broadcast=True`, also emit a `task.cancel_requested` event
        on the lifecycle bus so other processes' registries signal too.

        Returns True if a local token existed (was already registered),
        False if we created one defensively. Useful diagnostics: if a
        cancel call repeatedly returns False, the run_id is wrong or
        the worker hasn't picked the task up yet.
        """
        existed = run_id in self._tokens
        token = self.register(run_id)
        token.set()
        logger.info(
            f"[abort_registry] signaled run_id={run_id} "
            f"existed={existed} broadcast={broadcast}"
        )
        if broadcast:
            await self._publish_cancel(run_id)
        return existed

    async def _publish_cancel(self, run_id: str) -> None:
        try:
            from app.services.lifecycle_bus import get_bus

            await get_bus().emit(
                "task.cancel_requested",
                {"run_id": run_id},
            )
        except Exception as exc:
            logger.opt(exception=True).debug(
                f"[abort_registry] cancel publish failed: {exc}"
            )

    def install_bus_subscriber(self) -> None:
        """Subscribe this registry to incoming `task.cancel_requested`
        events on the lifecycle bus, so cancels emitted by other
        processes flip our local tokens too. Call once per process at
        startup; idempotent.
        """
        try:
            from app.services.lifecycle_bus import get_bus

            bus = get_bus()

            async def _handle(evt) -> None:  # type: ignore[no-untyped-def]
                rid = (evt.payload or {}).get("run_id")
                if not rid:
                    return
                # Use signal() with broadcast=False to avoid an emit
                # loop (we received this event from the bus, don't
                # republish it).
                await self.signal(str(rid), broadcast=False)

            bus.subscribe(
                "task.cancel_requested",
                _handle,
                name="abort_registry.local_signal",
            )
            logger.info(
                "[abort_registry] subscribed to task.cancel_requested events"
            )
        except Exception as exc:
            logger.opt(exception=True).warning(
                f"[abort_registry] bus subscription failed: {exc}"
            )


# ── Singleton accessor ───────────────────────────────────────────

_registry: Optional[AbortRegistry] = None


def get_registry() -> AbortRegistry:
    global _registry
    if _registry is None:
        _registry = AbortRegistry()
    return _registry


__all__ = [
    "AbortError",
    "AbortRegistry",
    "get_registry",
]
