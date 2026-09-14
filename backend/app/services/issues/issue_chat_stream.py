"""Publish issue-chat events to Redis `issue:{id}` for the WS forwarder.
Best-effort: a publish failure must never break the agent turn."""

from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

from loguru import logger


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


async def publish_status(issue_id: int, phase: str, *, run_id: Any = None) -> None:
    """回合状态帧。``done`` 帧带 run、它的 transcript 水位与登记过的产出坐标——前端
    按 seq 丢重复帧（轮询边沿与它说的是同一件事）与乱序帧，按 outputs 精确失效
    血缘缓存（3b §4）。三个键**恒定存在**、未知时为 null / []：有时缺席的字段会被
    读成「seq 0」，那会把最新一帧当最旧的丢掉。"""
    rid = _as_run_int(run_id)
    seq = await _last_transcript_seq(rid) if rid is not None else None
    outputs = await _run_output_keys(rid) if rid is not None else []
    await _publish(
        issue_id,
        {
            "type": "status",
            "phase": phase,
            "run_id": str(run_id) if run_id is not None else None,
            "seq": seq,
            "outputs": outputs,
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
