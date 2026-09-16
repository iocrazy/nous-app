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


def _writer_factory(run_id, *, seq_start):
    from app.services.ai.runner.run_recorder import RunEventWriter

    return RunEventWriter(run_id, seq_start=seq_start)


async def _stamp_turn_end_reason(run_id: int, reason: str) -> None:
    """仅当 ``turn_end_reason`` 还空着时补写。``IS NULL`` 守卫有两个作用：sweeper 重
    放（DBOS 重试）不改写，一个真跑完并自己写了结论的 run 也绝不会被事后改成
    ``interrupted``。"""
    from sqlalchemy import update as sa_update

    from app.db.session import write_scope
    from app.models import AgentRuns

    async with write_scope() as session:
        await session.execute(
            sa_update(AgentRuns)
            .where(AgentRuns.id == int(run_id))
            .where(AgentRuns.turn_end_reason.is_(None))
            .values(turn_end_reason=reason)
        )


async def close_interrupted_run(run_id: int, *, detail: Optional[str] = None) -> bool:
    """Append ``turn_end{reason:interrupted}`` unless one already exists.
    Returns True when an event was written."""
    last_seq, has_end = await _last_seq_and_has_turn_end(run_id)
    if has_end:
        return False
    writer = _writer_factory(run_id, seq_start=last_seq)
    payload = {"reason": TurnEndReason.INTERRUPTED.value}
    if detail:
        payload["detail"] = detail
    await writer.append(TURN_END_EVENT_TYPE, payload, turn=1)
    # 3c §3.2：事件与列一起补，否则崩溃的 run 那一列永远空着。
    #
    # 补列失败**不**回退返回值：事件已经落在 transcript 上了，而幂等守卫读的正是
    # 事件（``_last_seq_and_has_turn_end``），报 False 只会让下一轮 sweeper 再
    # append 一条。容纳并记录 —— 那条 ERROR 是「这一列现在空着」唯一的线索，下一
    # 次 stamp 仍会被 ``IS NULL`` 守卫放行，所以缺口是可自愈的。
    try:
        await _stamp_turn_end_reason(int(run_id), TurnEndReason.INTERRUPTED.value)
    except Exception as err:  # noqa: BLE001 — 见上：容纳并记录，不是静默吞
        logger.error(
            f"[interrupted_turn] turn_end_reason not stamped (run={run_id}): {err}"
        )
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
