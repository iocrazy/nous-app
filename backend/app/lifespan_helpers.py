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
* **LONGRUNNING** — daemon coroutines that live for the process lifetime.
  Two sub-kinds: components with their own start/stop protocol
  (SsrfProxy, PrometheusPusher, bounds heartbeat)
  manage themselves and stay outside this module; bare daemon loops
  (reap sweep, stall detector) are registry-owned via
  `spawn(..., long_running=True)` so they get snapshot visibility and
  shutdown cancellation without gating `/readyz`.

`BackgroundTaskRegistry` owns the BACKGROUND bucket plus the registry-owned
daemons: it spawns each task with a name, exposes the `/readyz` gate
(`all_done()` — finite tasks finished AND daemons still alive), and on
shutdown cancels what's still running with
`asyncio.gather(return_exceptions=True)` so a hung task can't block the
rest of the shutdown chain.
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
    long_running: bool = False
    gates_readiness: bool = True
    # A gate whose FAILURE must block readiness (degraded/503), not merely be
    # recorded. For probes that exist to refuse a bad state — the media work
    # dir being an overlay shadow dir (2026-09-07).
    fatal: bool = False


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

    def spawn(
        self,
        name: str,
        coro: Awaitable[Any],
        *,
        long_running: bool = False,
        gates_readiness: bool = True,
        fatal: bool = False,
    ) -> asyncio.Task:
        """Wrap `coro` in a tracked Task and start it.

        If `name` already exists the previous entry is replaced — caller
        is responsible for not double-spawning the same logical task.
        Records exceptions on the entry so `/readyz` can surface them
        without scraping logs.

        Three kinds of task, two flags:

        - **startup gate** (default): finite work readiness waits for.
          `/readyz` stays 503 "starting" until it finishes.
        - **daemon** (`long_running=True`): a loop that lives for the process
          lifetime (internal-queue reaper, stall detector). Never finishes by
          design, so `all_done()` gates on it being ALIVE — a pending daemon
          doesn't pin `/readyz` at "starting", and a **finished** one means it
          crashed and flips readiness to "degraded".
        - **detached housekeeping** (`gates_readiness=False`): finite work that
          readiness must NOT wait for (e.g. a sweep deliberately delayed 30s).
          It is allowed to finish, and finishing is not a crash.

        Plus one modifier: `fatal=True` on a startup gate means its FAILURE
        blocks readiness ("degraded"/503) instead of merely being recorded.
        Default gates are warn-only (a failed schema probe must not take the
        process down); a probe that exists to refuse a state — the media work
        dir being an overlay shadow dir — must be fatal or it guards nothing.

        ⚠️ Do NOT express "keep it off the readiness gate" as
        `long_running=True` — that lies about the task's shape, and the moment
        it returns, `dead_daemons()` reads it as a crashed daemon: `/readyz`
        goes **permanently** "degraded"/503, both containers sit `(unhealthy)`,
        and the deploy smoke gate starts failing (auto-rollback). That is
        exactly what happened to `reap_stale_input_waits` (#1662, caught
        2026-08-03). Use `gates_readiness=False` for that intent.
        """
        loop = asyncio.get_event_loop()
        started_at = loop.time()

        previous = self._entries.get(name)
        if previous is not None and not previous.task.done():
            # Re-spawn: cancel the evicted task, or it keeps running
            # untracked — it would skip shutdown() cancellation and (before
            # entries were closure-bound) stamp its exception/finished_at
            # onto the NEW entry via a by-name lookup.
            previous.task.cancel()
            logger.warning(f"[bg-task:{name}] re-spawn cancelled previous task")

        async def _runner() -> None:
            # `entry` is bound below before the loop first runs this
            # coroutine, and stays pinned to THIS spawn — a later re-spawn
            # under the same name can't have its status corrupted by us.
            try:
                await coro
            except asyncio.CancelledError:
                logger.info(f"[bg-task:{name}] cancelled during shutdown")
                raise
            except Exception as exc:
                logger.exception(f"[bg-task:{name}] failed: {exc!r}")
                entry.exception = exc
                raise
            finally:
                entry.finished_at = loop.time()

        task = loop.create_task(_runner(), name=f"bg:{name}")
        entry = _Entry(
            name=name,
            task=task,
            started_at=started_at,
            long_running=long_running,
            gates_readiness=gates_readiness,
            fatal=fatal,
        )
        self._entries[name] = entry
        logger.info(f"[bg-task:{name}] spawned")
        return task

    def all_done(self) -> bool:
        """Readiness gate: every finite task finished AND no daemon died.

        Finite tasks must be done (success OR failure). `long_running`
        daemons must be NOT done — a daemon loop never returns by design,
        so a finished daemon task means it crashed (or exited unexpectedly)
        and the process is degraded, which must not read as \"ready\".
        """
        finite_done = all(
            e.task.done()
            for e in self._entries.values()
            if not e.long_running and e.gates_readiness
        )
        return finite_done and not self.dead_daemons() and not self.failed_fatal_gates()

    def failed_fatal_gates(self) -> List[str]:
        """Names of `fatal` gates that finished with an exception — each one
        is a state the process refused to run in; `/readyz` says "degraded"."""
        return [
            e.name
            for e in self._entries.values()
            if e.fatal and e.task.done() and e.exception is not None
        ]

    def dead_daemons(self) -> List[str]:
        """Names of `long_running` daemons whose task finished (= crashed).

        A live daemon never completes, so `task.done()` on one is always a
        failure signal — surfaced by `/readyz` as status \"degraded\"."""
        return [
            e.name for e in self._entries.values() if e.long_running and e.task.done()
        ]

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
                    "long_running": entry.long_running,
                    "gates_readiness": entry.gates_readiness,
                    "fatal": entry.fatal,
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
