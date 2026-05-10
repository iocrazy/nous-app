"""Background task orchestration for FastAPI lifespan.

Why this module exists
----------------------
FastAPI's `lifespan` is a single async context manager whose pre-`yield`
body runs **strictly before** the HTTP server starts accepting connections.
Awaiting a slow operation there (DB seed, schema scan, etc.) silently
multiplies cold-start time and — worse — makes `--reload` cycles unusable
because every code edit replays the full startup chain.

The fix is to split lifespan work into three buckets:

* **CRITICAL**   — must complete before yield. Settings load, signal
  handlers, in-memory registries that request handlers depend on.
* **BACKGROUND** — fire-and-forget tasks that may finish after yield.
  Examples: schema sanity probe, seed loader, deployment-log writer,
  bounds self-registration.
* **LONGRUNNING** — daemon coroutines that live for the process lifetime
  and have their own start/stop protocol (WorkforceScheduler, SsrfProxy,
  PrometheusPusher, bounds heartbeat). These already manage themselves;
  this module doesn't touch them.

`BackgroundTaskRegistry` owns the BACKGROUND bucket: it spawns each task
with a name, exposes a `done()` query for `/readyz`, and on shutdown
cancels what's still running with `asyncio.gather(return_exceptions=True)`
so a hung task can't block the rest of the shutdown chain.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from loguru import logger


@dataclass
class _Entry:
    name: str
    task: asyncio.Task
    started_at: float
    finished_at: Optional[float] = None
    exception: Optional[BaseException] = None


@dataclass
class BackgroundTaskRegistry:
    """Track lifespan-spawned background tasks.

    Usage
    -----
    >>> reg = BackgroundTaskRegistry()
    >>> reg.spawn("seed_loader", seed_loader.load_all())
    >>> # ... after yield ...
    >>> await reg.shutdown(timeout=5.0)

    Thread-safety
    -------------
    Single event loop only. All methods must be called from the same loop
    that owns the spawned tasks.
    """

    _entries: Dict[str, _Entry] = field(default_factory=dict)

    def spawn(self, name: str, coro: Awaitable[Any]) -> asyncio.Task:
        """Wrap `coro` in a tracked Task and start it.

        If `name` already exists the previous entry is replaced — caller
        is responsible for not double-spawning the same logical task.
        Records exceptions on the entry so `/readyz` can surface them
        without scraping logs.
        """
        loop = asyncio.get_event_loop()
        started_at = loop.time()

        async def _runner() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                logger.info(f"[bg-task:{name}] cancelled during shutdown")
                raise
            except Exception as exc:
                logger.exception(f"[bg-task:{name}] failed: {exc!r}")
                entry = self._entries.get(name)
                if entry is not None:
                    entry.exception = exc
                raise
            finally:
                entry = self._entries.get(name)
                if entry is not None:
                    entry.finished_at = loop.time()

        task = loop.create_task(_runner(), name=f"bg:{name}")
        self._entries[name] = _Entry(name=name, task=task, started_at=started_at)
        logger.info(f"[bg-task:{name}] spawned")
        return task

    def all_done(self) -> bool:
        """True if every spawned task has finished (success OR failure)."""
        return all(e.task.done() for e in self._entries.values())

    def status_snapshot(self) -> List[Dict[str, Any]]:
        """Per-task status for `/readyz` payload. Cheap, no I/O."""
        out: List[Dict[str, Any]] = []
        for entry in self._entries.values():
            duration = (
                (entry.finished_at - entry.started_at)
                if entry.finished_at is not None
                else None
            )
            out.append(
                {
                    "name": entry.name,
                    "done": entry.task.done(),
                    "duration_seconds": duration,
                    "error": (
                        f"{type(entry.exception).__name__}: {entry.exception}"
                        if entry.exception is not None
                        else None
                    ),
                }
            )
        return out

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Cancel every still-running task and gather with timeout.

        Exceptions are swallowed (return_exceptions=True) — at shutdown we
        care that things stop, not that they stopped cleanly.
        """
        pending = [e.task for e in self._entries.values() if not e.task.done()]
        if not pending:
            return
        logger.info(
            f"[bg-task] shutdown: cancelling {len(pending)} pending task(s) "
            f"(timeout={timeout}s)"
        )
        for task in pending:
            task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[bg-task] shutdown: {sum(1 for t in pending if not t.done())} "
                "task(s) did not exit within timeout — leaking on process exit"
            )


def safe_critical(name: str) -> Callable:
    """Decorator: log + swallow exceptions on a critical-but-non-fatal step.

    Use for lifespan steps that should run inline (because something later
    depends on the side effect being applied) but whose failure shouldn't
    take the server down. Equivalent to a bare ``try: ... except: log``,
    but reads as a lifespan-step annotation.
    """

    def _decorate(coro_fn: Callable) -> Callable:
        async def _wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                return await coro_fn(*args, **kwargs)
            except Exception as e:
                logger.warning(f"[critical:{name}] non-fatal failure: {e!r}")
                return None

        return _wrapped

    return _decorate
