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
    # 深链 link_id 口径：issue 链接携带人类标识符（MH-N），issues 路由按它
    # 解析（scheduled_master 的 autopilot producer 同款,含 str(id) 兜底）。
    link_id = str(issue_id)
    try:
        ident = await db_engine.fetch_val(
            "SELECT identifier FROM public.issues WHERE id = :id", {"id": issue_id}
        )
        if ident:
            link_id = str(ident)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[input_gate] identifier lookup failed issue={issue_id}: {exc}")
    # notify() 自身永不 raise（唯一 inbox 写路径，带 10 分钟去重）
    await notify(
        user_id,
        "agent_question",
        "Agent needs your input",
        body=clipped,
        severity="info",
        link_kind="issue",
        link_id=link_id,
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


async def _fetch_awaiting_rows() -> list[dict]:
    """所有带 awaiting_input 标记的 task 行 + 其 DBOS app_version。

    JOIN dbos.workflow_status 是引擎侧读（非 UI 数据源，不违路线 C——路线 C
    禁的是前端/列表 endpoint 直查引擎表；reaper 恰恰是引擎孤儿的清道夫，
    与 _bg_reap_internal_queue 同族）。"""
    from app.db import engine as db_engine

    return await db_engine.fetch_all(
        """SELECT t.dbos_workflow_id, t.issue_id, w.application_version
           FROM public.task_tracking t
           JOIN dbos.workflow_status w ON w.workflow_uuid = t.dbos_workflow_id
           WHERE t.metadata ? 'awaiting_input'
             AND w.status IN ('PENDING', 'ENQUEUED')""",
    )


async def _cancel_workflow(workflow_id: str) -> None:
    from dbos import DBOS

    await DBOS.cancel_workflow_async(workflow_id)


async def _clear_issue_lock(workflow_id: str) -> None:
    """按 dbos_workflow_id 定位（atomic_checkout 写过它,比 task_tracking.issue_id
    回填更可靠）,释放被 reap 的 dispatch 持有的 issue 执行锁。"""
    from app.db import engine as db_engine

    await db_engine.execute_as_service_role(
        "UPDATE public.issues SET execution_locked_at = NULL "
        "WHERE dbos_workflow_id = :wf",
        {"wf": workflow_id},
    )


async def reap_stale_input_waits(*, current_version: str) -> int:
    """部署换版本后,旧版本挂起的 workflow 无 worker 认领 —— 假活。
    清标记 + cancel + 释放 issue 执行锁,使回复自动走旧路径;issue 停在
    needs_followup 用户无感。锁必须在这里清：被 reap 的 workflow 永远跑不到
    execute_issue 的 ``finally: clear_lock``,而 ``acquire_turn_lock`` 用的是
    同一列 —— 不清的话旧路径回复会对死锁自旋 10 分钟后 defer。
    单行失败只记日志继续 —— 一个坏行不能挡住整个 sweep。"""
    cleaned = 0
    for row in await _fetch_awaiting_rows():
        if row.get("application_version") == current_version:
            continue
        wf = row["dbos_workflow_id"]
        try:
            await clear_awaiting_input(workflow_id=wf)
            await _cancel_workflow(wf)
            await _clear_issue_lock(wf)
            cleaned += 1
            logger.info(f"[input_gate] reaped stale wait wf={wf}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[input_gate] reap failed wf={wf}: {exc}")
    return cleaned


__all__ = [
    "TOPIC_PREFIX",
    "await_user_input",
    "signal_user_reply",
    "mark_awaiting_input",
    "clear_awaiting_input",
    "reap_stale_input_waits",
]
