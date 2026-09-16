"""Publish issue-chat events to Redis `issue:{id}` for the WS forwarder.
Best-effort: a publish failure must never break the agent turn."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional
from uuid import UUID

from loguru import logger

from app.services.billing.agent_run_reference import AGENT_RUN_REFERENCE_TYPE


async def _get_redis():
    from app.core.redis import get_async_redis

    return await get_async_redis()


def _channel(issue_id: int) -> str:
    return f"issue:{issue_id}"


async def _publish(issue_id: int, payload: dict[str, Any]) -> None:
    try:
        r = await _get_redis()
        await r.publish(_channel(issue_id), json.dumps(payload, default=str))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[issue_chat_stream] publish failed (issue={issue_id}): {e}")


async def publish_chunk(issue_id: int, delta: str) -> None:
    await _publish(issue_id, {"type": "chunk", "delta": delta})


def _as_run_int(run_id: Any) -> Optional[int]:
    """run id 在调用方手里是 str / int / None，两个读方要的是 int，而帧上一律以
    字符串出口（Snowflake 精度）。转不动就当「没有 run」——这两次读都只是装饰，
    一个坏 id 不该让状态帧本身发不出去（裸 ``int()`` 会抛，这里不许抛）。"""
    try:
        return int(run_id)
    except (TypeError, ValueError):
        return None


async def _last_transcript_seq(run_id: Any) -> Optional[int]:
    """best-effort：水位读不到就是 None，绝不让一次遥测失败拖垮回合。"""
    try:
        from app.repositories.agent_runs_repository import get_agent_runs_repository

        return await get_agent_runs_repository().last_transcript_seq(int(run_id))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[issue_chat_stream] last seq read failed (run={run_id}): {e}")
        return None


async def _run_output_keys(run_id: Any) -> list[dict[str, Any]]:
    """这条 run 登记过的产出坐标——前端 done 时按 (kind, ref_id) 精确失效血缘缓存。
    读失败回 []（只是少一次精确失效，TTL 兜底），绝不让状态帧发不出去。"""
    try:
        from app.repositories.run_deliverables_repository import (
            get_run_deliverables_repository,
        )

        return await get_run_deliverables_repository().output_keys_for_run(int(run_id))
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[issue_chat_stream] output keys read failed (run={run_id}): {e}"
        )
        return []


async def _cost_cents_for(rid: int) -> Optional[float]:
    """这条 run 记的花费。读失败回 None —— 降级只影响这一个字段。"""
    try:
        from app.repositories.agent_runs_repository import get_agent_runs_repository

        rows = await get_agent_runs_repository().cost_rows_for_ids([rid])
        row = next((r for r in rows if int(r["id"]) == rid), None) or {}
        return row.get("cost_cents")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[issue_chat_stream] cost read failed (run={rid}): {e}")
        return None


async def _charged_points_for(rid: int) -> Optional[float]:
    """这条 run 真扣掉的积分。没扣过的 id 不出现在返回里 —— ``.get`` 的 None 正是
    「没人收费」，与读失败的 None 在这一层合并（帧上两者都只说「不知道」）。"""
    try:
        from app.repositories.points_repository import get_points_repository

        charged = await get_points_repository().charged_points_for_references(
            reference_type=AGENT_RUN_REFERENCE_TYPE, reference_ids=[str(rid)]
        )
        return charged.get(str(rid))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[issue_chat_stream] points read failed (run={rid}): {e}")
        return None


async def run_cost_for_frame(run_id: Any) -> dict[str, Optional[float]]:
    """这次回合的花费与已扣积分，做成状态帧的两个键。

    两个键**恒定存在**，``null`` 说的是「不知道」—— 缺席会被读成 0，而 0 在钱上是
    另一个答案（「这次免费」）。

    **两个读各自 catch，绝不共用一个 try**：它们打的是两张表、答的是两个正交的问题
    （花了多少 / 收了多少）。共用一个 ``try`` 时，积分读抛出会把**已经读到的**
    ``cost_cents`` 一起丢掉，于是一次积分故障把一个真花了钱的 run 画成完全没有账。
    口径同 ``issue_rollup._charged``：谁失败只空掉谁。

    与 ``/ai-library/runs/costs`` 用同两个取数方法，不另开一条读路径：气泡上那个数字
    和端点回的那个数字必须是同一个来源，否则两处迟早说出不同的钱。
    可见性不在这里判——这一帧发往 ``issue:{id}`` 频道，订阅者已经过了那道闸。"""
    rid = _as_run_int(run_id)
    if rid is None:
        return {"cost_cents": None, "charged_points": None}
    cost_cents, charged_points = await asyncio.gather(
        _cost_cents_for(rid), _charged_points_for(rid)
    )
    return {"cost_cents": cost_cents, "charged_points": charged_points}


async def publish_status(issue_id: int, phase: str, *, run_id: Any = None) -> None:
    """回合状态帧。``done`` 帧带 run、它的 transcript 水位与登记过的产出坐标——前端
    按 seq 丢重复帧（轮询边沿与它说的是同一件事）与乱序帧，按 outputs 精确失效
    血缘缓存（3b §4）。三个键**恒定存在**、未知时为 null / []：有时缺席的字段会被
    读成「seq 0」，那会把最新一帧当最旧的丢掉。

    ``cost_cents`` / ``charged_points`` 同样恒定存在，未知为 null（3c §4.2）——每个
    phase 都带，消费方不必分两种形状去读。"""
    rid = _as_run_int(run_id)
    seq = await _last_transcript_seq(rid) if rid is not None else None
    outputs = await _run_output_keys(rid) if rid is not None else []
    # 与上面两次读同一道守卫：id 转不动就三次读都不做。``run_cost_for_frame``
    # 自己也挡（它还有 chat 那个调用方，run_id 可能是 None），但守卫写在调用点
    # 才拦得住「坏 id 不许碰仓库」这条——内部 try 只是不抛，不等于没去读。
    cost = (
        await run_cost_for_frame(rid)
        if rid is not None
        else {"cost_cents": None, "charged_points": None}
    )
    await _publish(
        issue_id,
        {
            "type": "status",
            "phase": phase,
            "run_id": str(run_id) if run_id is not None else None,
            "seq": seq,
            "outputs": outputs,
            **cost,
        },
    )


async def publish_message(
    issue_id: int, ai_message_row: dict[str, Any], *, session_user_id: Optional[UUID]
) -> None:
    if not ai_message_row or not ai_message_row.get("id"):
        logger.warning(
            f"[issue_chat_stream] skip publish_message — empty assistant row (issue={issue_id})"
        )
        return

    from app.services.issues.issue_message_mapper import (
        map_ai_message_to_issue_message,
    )

    msg = map_ai_message_to_issue_message(
        ai_message_row, issue_id=issue_id, session_user_id=session_user_id
    )
    await _publish(
        issue_id, {"type": "message", "message": msg.model_dump(mode="json")}
    )
