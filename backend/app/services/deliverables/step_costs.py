"""每个 ``(run, turn, step)`` 的**每件产出**花费份额（3b spec §3.1）。

分子分母同源、一次查询：``step_end.cost_cents`` 是那一步的 LLM 花费（出生点
``agent_runner._step_ended``），同键下的 ``deliverable`` 事件条数是那一步产出了
几件。花费是对事件流的折叠，不存第二份——``run_deliverables.cost_cents`` 对文本
类永远是 NULL，读时才折。

⚠️ ``turn`` 今天硬编码 1（``agent_runner``），键实际是 step-only；仍写三元组，
将来 turn 真正推进时零改动。
⚠️ 分母是**事件**数不是 ``run_deliverables`` 行数：没落成事件的登记既不占分母
也拿不到份额——好过一个查表一个查流然后对不上。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import select

from app.db.session import read_scope

StepKey = Tuple[int, int, int]


def _events_stmt(run_ids: List[int]):
    from app.models import AgentRunTranscriptEvents as TE

    return (
        select(TE.run_id, TE.event_type, TE.turn, TE.step, TE.payload)
        .where(TE.run_id.in_(run_ids))
        .where(TE.event_type.in_(["step_end", "deliverable"]))
    )


def _key(row: Dict[str, Any]) -> Optional[StepKey]:
    turn, step = row.get("turn"), row.get("step")
    if turn is None or step is None:
        return None
    return (int(row["run_id"]), int(turn), int(step))


async def load_step_shares(run_ids: List[int]) -> Dict[StepKey, float]:
    """每件产出应分摊到的分数。算不出的步不进表（缺席 = 不知道）。"""
    runs = sorted({int(r) for r in run_ids if r is not None})
    if not runs:
        return {}
    try:
        async with read_scope() as session:
            rows = (await session.execute(_events_stmt(runs))).mappings().all()
    except Exception as exc:  # noqa: BLE001 — 花费是装饰，不连坐血缘
        logger.warning(f"[step_costs] transcript read failed for {runs}: {exc!r}")
        return {}
    costs: Dict[StepKey, float] = {}
    counts: Dict[StepKey, int] = {}
    for row in rows:
        key = _key(row)
        if key is None:
            continue
        if row["event_type"] == "deliverable":
            counts[key] = counts.get(key, 0) + 1
            continue
        cents = (row.get("payload") or {}).get("cost_cents")
        if isinstance(cents, (int, float)) and not isinstance(cents, bool):
            costs[key] = float(cents)
    return {
        key: round(cost / counts[key], 4)
        for key, cost in costs.items()
        if counts.get(key)
    }


__all__ = ["StepKey", "load_step_shares"]
