"""subprocess_registry — track child PIDs per workflow_id so cancel can
kill them.

The problem: nous spawns yt-dlp / whisper / ffmpeg / etc. via
``asyncio.create_subprocess_exec``. When DBOS workflow cancel fires,
the asyncio task is cancelled but the SUBPROCESS keeps running until it
finishes on its own — holding GPU, disk, network for nothing. Workflow
shows "cancelled" in the UI, child still burning resources.

This registry lets each spawn site register its PID against the active
workflow_id. On cancel, ``cancel_workflow_subprocesses(workflow_id)``
walks the registered PIDs and calls ``kill_process_tree`` on each.

Spawn sites do not call this module directly: they go through
``app.agent_framework.process_runner.run_process``, which registers the pid
(under ``workflow_id``, or ``UNSCOPED`` outside a workflow) before waiting and
unregisters it in ``finally`` (``tests/agent_framework/
test_subprocess_registration_guard.py`` pins that).

Scope: this dict is PER PROCESS. Cancel endpoints run in ``nous-backend``;
workflows and their children run in ``nous-worker``. So the cross-process
cancel does NOT go through here — ``run_process`` polls ``task_tracking``
itself (fh4 ruling 1). The registry serves same-process cancel, teardown
(``cancel_all_subprocesses`` from ``startup/teardown.shutdown_all``) and the
atexit last resort (``process_lifecycle._cleanup_subprocess_registry``).

Dispose order (CLAUDE.md 防御模式): an entry is POPPED before its pids are
killed, so a ``finally: unregister_subprocess`` racing the kill is a silent
no-op, and ``run_process`` can tell "someone detached and killed my child"
(→ ``cancelled=True``) from "my child exited".

Not thread-safe; use only from the asyncio loop (atexit reads a snapshot).
"""

from __future__ import annotations

import asyncio

from loguru import logger

from app.agent_framework.kill_tree import KillOutcome, kill_process_tree

#: Key for children spawned outside any workflow (API routes, scripts) — still
#: registered so teardown and atexit can reach them.
UNSCOPED = "<unscoped>"

# workflow_id (str) → set of registered PIDs
_REGISTRY: dict[str, set[int]] = {}


def register_subprocess(workflow_id: str | None, pid: int) -> None:
    """Add ``pid`` to the registry for ``workflow_id``.

    Silent no-op if either is falsy (callers wanting teardown coverage
    outside a workflow pass ``UNSCOPED``).
    """
    if not workflow_id or not pid:
        return
    _REGISTRY.setdefault(workflow_id, set()).add(pid)


def unregister_subprocess(workflow_id: str | None, pid: int) -> bool:
    """Remove ``pid``. Returns whether it was still registered — ``False``
    means a cancel already detached it (or it never was). Race-safe."""
    if not workflow_id or not pid:
        return False
    pids = _REGISTRY.get(workflow_id)
    if pids is None or pid not in pids:
        return False
    pids.discard(pid)
    if not pids:
        _REGISTRY.pop(workflow_id, None)
    return True


def registered_pids(workflow_id: str) -> list[int]:
    """Snapshot of PIDs registered for ``workflow_id`` (test / debug)."""
    return list(_REGISTRY.get(workflow_id, set()))


def snapshot_all_pids() -> dict[str, frozenset[int]]:
    """Immutable copy of the whole registry (atexit / diagnostics)."""
    return {wf: frozenset(pids) for wf, pids in _REGISTRY.items()}


async def _kill_detached(
    label: str, pids: list[int], grace_seconds: float
) -> list[KillOutcome]:
    """Kill already-detached pids concurrently and wait until each is still.
    One bad pid never stops the rest (logged, not raised)."""
    results = await asyncio.gather(
        *(kill_process_tree(pid, grace_seconds=grace_seconds) for pid in pids),
        return_exceptions=True,
    )
    outcomes: list[KillOutcome] = []
    for pid, res in zip(pids, results):
        if isinstance(res, BaseException):
            logger.warning(
                f"[subprocess_registry] kill pid={pid} for {label} raised "
                f"{type(res).__name__}: {res} — continuing"
            )
            continue
        outcomes.append(res)
    return outcomes


async def cancel_workflow_subprocesses(
    workflow_id: str,
    *,
    grace_seconds: float = 3.0,
) -> list[KillOutcome]:
    """Detach every pid registered for ``workflow_id``, then kill them all
    concurrently and await quiescence. Returns one outcome per pid whose kill
    completed (``len()`` is the count to log)."""
    pids = sorted(_REGISTRY.pop(workflow_id, set()))
    if not pids:
        return []
    return await _kill_detached(f"wf={workflow_id}", pids, grace_seconds)


async def cancel_all_subprocesses(*, grace_s: float = 2.0) -> list[KillOutcome]:
    """Teardown: detach and kill everything this process still has running."""
    snapshot = dict(_REGISTRY)
    _REGISTRY.clear()
    pids = sorted({pid for group in snapshot.values() for pid in group})
    if not pids:
        return []
    outcomes = await _kill_detached("teardown", pids, grace_s)
    logger.info(
        f"[subprocess_registry] teardown killed {len(outcomes)} subprocess(es); "
        f"quiesced={sum(o.quiesced for o in outcomes)}"
    )
    return outcomes


def clear_registry() -> None:
    """Test helper — drop everything."""
    _REGISTRY.clear()
