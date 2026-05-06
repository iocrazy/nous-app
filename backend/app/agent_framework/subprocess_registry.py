"""subprocess_registry — track child PIDs per workflow_id so cancel can
kill them.

The problem: mediahub spawns yt-dlp / whisper / ffmpeg / etc. via
``asyncio.create_subprocess_exec``. When DBOS workflow cancel fires,
the asyncio task is cancelled but the SUBPROCESS keeps running until it
finishes on its own — holding GPU, disk, network for nothing. Workflow
shows "cancelled" in the UI, child still burning resources.

This registry lets each spawn site register its PID against the active
workflow_id. On cancel, ``cancel_workflow_subprocesses(workflow_id)``
walks the registered PIDs and calls ``kill_process_tree`` on each.

Usage at spawn site:

    from app.agent_framework.subprocess_registry import (
        register_subprocess, unregister_subprocess
    )
    from app.agent_framework.process_lifecycle import safe_popen_kwargs

    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", url,
        # safe_popen_kwargs() sets up new process group (so
        # kill_process_tree's killpg can signal yt-dlp's children too,
        # not just the parent) AND binds child to parent SIGKILL on
        # Linux (PR_SET_PDEATHSIG).
        **safe_popen_kwargs(),
    )
    register_subprocess(workflow_id, proc.pid)
    try:
        await proc.wait()
    finally:
        unregister_subprocess(workflow_id, proc.pid)

Usage at cancel site (typically inside DBOS cancel hook or
unified_task_manager.cancel):

    from app.agent_framework.subprocess_registry import (
        cancel_workflow_subprocesses
    )

    await cancel_workflow_subprocesses(workflow_id, grace_seconds=3.0)

Module-level dict (per-process) — all coroutines on the same loop see
the same registry. Not thread-safe; use only from the asyncio loop.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from app.agent_framework.kill_tree import kill_process_tree

# workflow_id (str) → set of registered PIDs
_REGISTRY: dict[str, set[int]] = {}


def register_subprocess(workflow_id: Optional[str], pid: int) -> None:
    """Add ``pid`` to the registry for ``workflow_id``.

    Silent no-op if either is falsy (workflow_id may be None when the
    subprocess is spawned outside a tracked workflow context — common
    for ad-hoc dev / testing).
    """
    if not workflow_id or not pid:
        return
    _REGISTRY.setdefault(workflow_id, set()).add(pid)


def unregister_subprocess(workflow_id: Optional[str], pid: int) -> None:
    """Remove ``pid`` from the registry. Silent on missing entries
    (race-safe — caller's `finally` may run after cancel cleanup
    already pulled the entry)."""
    if not workflow_id or not pid:
        return
    pids = _REGISTRY.get(workflow_id)
    if pids is None:
        return
    pids.discard(pid)
    if not pids:
        _REGISTRY.pop(workflow_id, None)


def registered_pids(workflow_id: str) -> list[int]:
    """Snapshot of PIDs registered for ``workflow_id`` (test / debug)."""
    return list(_REGISTRY.get(workflow_id, set()))


async def cancel_workflow_subprocesses(
    workflow_id: str,
    *,
    grace_seconds: float = 3.0,
) -> int:
    """Kill every subprocess registered for ``workflow_id``.

    Returns the count of PIDs we attempted to kill (may include
    already-dead PIDs — kill_tree handles that silently).

    Best-effort: each kill wrapped in try/except so one bad PID doesn't
    prevent the rest from being cleaned up.
    """
    pids = list(_REGISTRY.get(workflow_id, set()))
    if not pids:
        return 0

    count = 0
    for pid in pids:
        try:
            await kill_process_tree(pid, grace_seconds=grace_seconds)
            count += 1
        except Exception as exc:
            logger.warning(
                f"[subprocess_registry] kill pid={pid} for wf={workflow_id} "
                f"raised {type(exc).__name__}: {exc} — continuing"
            )

    # Drop the entire registry entry — all PIDs killed (or attempted).
    # Caller's `finally: unregister_subprocess` will still be called and
    # become a silent no-op (entry already gone).
    _REGISTRY.pop(workflow_id, None)
    return count


def clear_registry() -> None:
    """Test helper — drop everything."""
    _REGISTRY.clear()
