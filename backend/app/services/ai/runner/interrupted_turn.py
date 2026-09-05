"""Close transcripts of runs the sweeper flipped to ``heartbeat_lost``.

The live runner emits ``turn_end`` on every exit it survives; a worker crash
or a lost heartbeat is the one exit it cannot narrate. The sweeper calls
``close_interrupted_runs`` with the ids it flipped and this module appends a
``turn_end{reason:"interrupted"}`` through the same ``RunEventWriter`` the
runner uses — one write path onto the event log, so ``run.view`` folds the
terminal event exactly as it would for a live run.

Idempotent: a run whose transcript already has a ``turn_end`` is skipped, so
a re-run of the sweeper tick (DBOS retry) does not double-close.
"""

from __future__ import annotations

from typing import Iterable, Optional

from loguru import logger

from app.services.ai.runner.turn_end import TURN_END_EVENT_TYPE, TurnEndReason


async def _last_seq_and_has_turn_end(run_id: int) -> tuple[int, bool]:
    from sqlalchemy import func, select

    from app.db.session import read_scope
    from app.models import AgentRunTranscriptEvents as E

    async with read_scope() as session:
        row = (
            await session.execute(
                select(
                    func.coalesce(func.max(E.seq), 0),
                    func.count().filter(E.event_type == TURN_END_EVENT_TYPE),
                ).where(E.run_id == run_id)
            )
        ).one()
    return int(row[0]), int(row[1]) > 0


async def close_interrupted_run(run_id: int, *, detail: Optional[str] = None) -> bool:
    """Append ``turn_end{reason:interrupted}`` unless one already exists.
    Returns True when an event was written."""
    from app.services.ai.runner.run_recorder import RunEventWriter

    last_seq, has_end = await _last_seq_and_has_turn_end(run_id)
    if has_end:
        return False
    writer = RunEventWriter(run_id, seq_start=last_seq)
    payload = {"reason": TurnEndReason.INTERRUPTED.value}
    if detail:
        payload["detail"] = detail
    await writer.append(TURN_END_EVENT_TYPE, payload, turn=1)
    return True


async def close_interrupted_runs(run_ids: Iterable[int]) -> int:
    """Close each run; one bad run never blocks the rest. Returns how many
    transcripts received a terminal event."""
    closed = 0
    for run_id in run_ids:
        try:
            if await close_interrupted_run(int(run_id), detail="heartbeat_lost"):
                closed += 1
        except (
            Exception
        ) as err:  # noqa: BLE001 — sweeper telemetry never fails the tick
            logger.warning(f"[interrupted_turn] could not close run={run_id}: {err}")
    return closed


__all__ = ["close_interrupted_run", "close_interrupted_runs"]
