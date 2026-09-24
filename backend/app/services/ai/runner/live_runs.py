"""Process-local registry of live RunRecorders, and the shutdown close.

A SIGTERM used to abandon every run in flight: ``shutdown_dbos`` does not
wait for workflows (dbos ``destroy(workflow_completion_timeout_sec=0)``), so
the turn coroutine died with its row still ``running`` and a frozen heartbeat,
and the sweeper closed it two minutes later (prod issue 352662630815921,
closed 2m05s after the deploy). The UI showed a live run that was gone.

``RunRecorder`` registers here once its row exists and unregisters on exit.
``interrupt_inflight_runs`` is called from ``startup.teardown.shutdown_all``
BEFORE ``shutdown_dbos`` and closes each registered run exactly the way the
sweeper does (``heartbeat_lost`` + ``turn_end{interrupted}`` + tree settle),
with ``error_code='worker_shutdown'`` so an orderly stop and a lost heartbeat
stay distinguishable (ruling 8: reuse the status, no migration).

Not a DBOS step, and it must never block exit: the whole close is bounded by
a timeout and every failure is logged, not raised.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:  # pragma: no cover
    from app.services.ai.runner.run_recorder import RunRecorder

# compose gives the worker 30 s (stop_grace_period). Background tasks take up
# to 3 s and ``shutdown_dbos`` 5 s after this, so the close gets 10 s.
SHUTDOWN_INTERRUPT_TIMEOUT_S = 10.0
WORKER_SHUTDOWN_DETAIL = "worker_shutdown"

# Keyed by id(): RunRecorder is a non-frozen dataclass, so it is unhashable.
_LIVE: dict[int, "RunRecorder"] = {}


def register(recorder: "RunRecorder") -> None:
    _LIVE[id(recorder)] = recorder


def unregister(recorder: "RunRecorder") -> None:
    _LIVE.pop(id(recorder), None)


def live_recorders() -> tuple["RunRecorder", ...]:
    return tuple(_LIVE.values())


def clear_for_tests() -> None:
    _LIVE.clear()


async def interrupt_inflight_runs(
    *, timeout_s: float = SHUTDOWN_INTERRUPT_TIMEOUT_S
) -> list[int]:
    """Close every registered run as interrupted by a worker shutdown.

    Two phases share one deadline but not one ``wait_for``. The flip is the
    terminal UPDATE; the closes (turn_end + tree settle) run one by one on
    whatever budget is left. The split matters because the sweeper only scans
    ``running`` rows: once flipped, a run it did not get to close here keeps
    no ``turn_end`` forever — so those ids are logged at ERROR, never dropped.

    Returns the run ids the flip took (a run that finished on its own in the
    meantime is not ``running`` and is left alone). Never raises."""
    recorders = live_recorders()
    if not recorders:
        return []
    logger.warning(
        f"[live_runs] worker shutdown with {len(recorders)} run(s) in flight; "
        f"closing them as {WORKER_SHUTDOWN_DETAIL}"
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    try:
        flipped = await asyncio.wait_for(_flip(recorders), timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.error(
            f"[live_runs] flipping in-flight runs exceeded {timeout_s}s; they "
            f"stay running and the heartbeat sweeper will close them"
        )
        return []
    except Exception as exc:  # noqa: BLE001 — shutdown must proceed
        logger.opt(exception=True).error(
            f"[live_runs] flipping in-flight runs failed: {exc!r}; they stay "
            f"running and the heartbeat sweeper will close them"
        )
        return []
    unclosed = await _close_all(flipped, deadline=deadline)
    if unclosed:
        logger.error(
            f"[live_runs] runs {unclosed} were flipped to heartbeat_lost "
            f"(worker_shutdown) but not closed before the {timeout_s}s budget "
            f"ran out: no turn_end{{interrupted}} and no tree settle. The sweeper "
            f"will NOT revisit them (it only scans running rows)."
        )
    logger.info(
        f"[live_runs] closed {len(flipped) - len(unclosed)}/{len(flipped)} "
        f"run(s) as worker_shutdown"
    )
    return list(flipped)


async def _flip(recorders: tuple["RunRecorder", ...]) -> list[int]:
    for recorder in recorders:
        await recorder.stop_heartbeat()
    run_ids = [int(r.run_id) for r in recorders if r.run_id is not None]
    if not run_ids:
        return []

    from app.repositories import agent_runs_repository as repo_mod

    repo = repo_mod.get_agent_runs_repository()
    return list(await repo.mark_worker_shutdown_ids(run_ids))


async def _close_all(flipped: list[int], *, deadline: float) -> list[int]:
    """Close each flipped run on the remaining budget; return the ids that did
    not get closed (budget exhausted or the close itself hung)."""
    loop = asyncio.get_running_loop()
    for index, run_id in enumerate(flipped):
        remaining = deadline - loop.time()
        if remaining <= 0:
            return list(flipped[index:])
        try:
            await asyncio.wait_for(_close_one(run_id), timeout=remaining)
        except asyncio.TimeoutError:
            return list(flipped[index:])
    return []


async def _close_one(run_id: Any) -> None:
    """Transcript terminal event, then tree settle — each contained, so one
    bad run never starves the rest (same order as the sweeper)."""
    from app.services.ai.billing import tree_charge
    from app.services.ai.runner import interrupted_turn

    try:
        await interrupted_turn.close_interrupted_run(
            int(run_id), detail=WORKER_SHUTDOWN_DETAIL
        )
    except Exception as exc:  # noqa: BLE001 — contained and logged
        logger.warning(f"[live_runs] turn_end for run {run_id} failed: {exc!r}")
    try:
        await tree_charge.settle_tree_if_closed(run_id=str(run_id))
    except Exception as exc:  # noqa: BLE001 — billing never blocks shutdown
        logger.warning(f"[live_runs] tree settle for run {run_id} failed: {exc!r}")


__all__ = [
    "SHUTDOWN_INTERRUPT_TIMEOUT_S",
    "interrupt_inflight_runs",
    "live_recorders",
    "register",
    "unregister",
]
