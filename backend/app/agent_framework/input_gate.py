"""needs_input 挂起/唤醒原语 —— approval_gate 的同族兄弟。

approval_gate 用同款 DBOS.recv/send 验证过 pause-resume 可行；本模块把它
接到 issue dispatch 的 needs_input 上：workflow 原地挂起等用户回复，回复
经 DBOS.send 精确唤醒。topic 按 issue 区分，避免同 workflow 多 gate 串扰。

所有对外函数失败都"软"处理（返 None/False），因为调用方永远有旧路径
（respond_to_issue_reply）兜底 —— 本模块任何故障都不得比现状更糟。

等待标记写 task_tracking.metadata.awaiting_input（路线 C 业务装饰字段，
不碰 trigger 独管的 phase/status 列）；inbox 通知走 notify() 唯一写路径。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

TOPIC_PREFIX = "needs_input:"

# 提问原文进 metadata / inbox 前的截断（spec: 500）
_PROMPT_MAX = 500


def _topic_for(issue_id: int) -> str:
    return f"{TOPIC_PREFIX}{issue_id}"


async def _recv_async(topic: str, *, timeout_seconds: int) -> Any:
    """薄壳，供测试 patch。必须在 @DBOS.workflow 体内被调用。"""
    from dbos import DBOS

    return await DBOS.recv_async(topic, timeout_seconds=timeout_seconds)


async def _send_async(workflow_id: str, payload: dict, *, topic: str) -> None:
    from dbos import DBOS

    await DBOS.send_async(workflow_id, payload, topic=topic)


async def await_user_input(issue_id: int, *, ttl_seconds: int) -> Optional[dict]:
    """挂起当前 workflow 等 issue 的用户回复。超时/畸形 payload 均返 None。"""
    payload = await _recv_async(_topic_for(issue_id), timeout_seconds=ttl_seconds)
    if not isinstance(payload, dict) or not payload.get("reply_text"):
        if payload is not None:
            logger.warning(f"[input_gate] malformed payload for issue {issue_id}: {payload!r}")
        return None
    return {
        "reply_text": str(payload["reply_text"]),
        "user_id": str(payload.get("user_id") or ""),
        "attachments": payload.get("attachments"),
    }


async def signal_user_reply(
    *,
    workflow_id: str,
    issue_id: int,
    reply_text: str,
    user_id: str,
    attachments: Optional[list] = None,
) -> bool:
    """向挂起的 workflow 投递回复。失败返 False（调用方走旧路径）。"""
    try:
        await _send_async(
            workflow_id,
            {"reply_text": reply_text, "user_id": user_id, "attachments": attachments},
            topic=_topic_for(issue_id),
        )
        return True
    except Exception as exc:  # noqa: BLE001 — 软失败是本模块契约
        logger.warning(f"[input_gate] send to wf={workflow_id} failed: {exc}")
        return False


async def mark_awaiting_input(
    *, workflow_id: str, issue_id: int, user_id: str, prompt: str
) -> None:
    """写等待标记（task_tracking.metadata.awaiting_input，路线 C 业务装饰字段）
    并投 inbox 通知。任一失败只记日志 —— 标记失败不阻断挂起，UI 少个高亮而已。"""
    from app.db import engine as db_engine
    from app.services.notifications import notify

    clipped = (prompt or "")[:_PROMPT_MAX]
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db_engine.execute_as_service_role(
            """UPDATE public.task_tracking
               SET metadata = COALESCE(metadata, '{}'::jsonb)
                   || jsonb_build_object('awaiting_input', (:marker)::jsonb)
               WHERE dbos_workflow_id = :wf""",
            {
                "marker": json.dumps({"prompt": clipped, "since": now, "issue_id": issue_id}),
                "wf": workflow_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[input_gate] mark metadata failed wf={workflow_id}: {exc}")
    # notify() 自身永不 raise（唯一 inbox 写路径，带 10 分钟去重）
    await notify(
        user_id,
        "agent_question",
        "Agent needs your input",
        body=clipped,
        severity="info",
        link_kind="issue",
        link_id=str(issue_id),
    )


async def clear_awaiting_input(*, workflow_id: str) -> None:
    """移除等待标记。inbox 行有意保留（用户稍后仍可从收件箱进入）。"""
    from app.db import engine as db_engine

    try:
        await db_engine.execute_as_service_role(
            """UPDATE public.task_tracking
               SET metadata = metadata - 'awaiting_input'
               WHERE dbos_workflow_id = :wf""",
            {"wf": workflow_id},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[input_gate] clear metadata failed wf={workflow_id}: {exc}")


__all__ = [
    "TOPIC_PREFIX",
    "await_user_input",
    "signal_user_reply",
    "mark_awaiting_input",
    "clear_awaiting_input",
]
