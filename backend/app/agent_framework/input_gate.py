"""needs_input 挂起/唤醒原语 —— approval_gate 的同族兄弟。

approval_gate 用同款 DBOS.recv/send 验证过 pause-resume 可行；本模块把它
接到 issue dispatch 的 needs_input 上：workflow 原地挂起等用户回复，回复
经 DBOS.send 精确唤醒。topic 按 issue 区分，避免同 workflow 多 gate 串扰。

所有对外函数失败都"软"处理（返 None/False），因为调用方永远有旧路径
（respond_to_issue_reply）兜底 —— 本模块任何故障都不得比现状更糟。

等待标记权威位在 issues.execution_state.awaiting_input（issue dispatch
没有 task_tracking 行，见 mark_awaiting_input）；task_tracking.metadata
仅作 best-effort 装饰写（路线 C 业务装饰字段，不碰 trigger 独管的
phase/status 列）；inbox 通知走 notify() 唯一写路径。
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
    """Send via the gateway DBOSClient when present, else the DBOS singleton.

    approval_gate 的同款分支，但对本模块是生死攸关而非 dormant：回复端点跑
    在 GATEWAY 进程（enqueue-only DBOSClient，**没有** DBOS 单例——
    ``DBOS.send_async`` 恒抛 "No DBOS was created yet"，2026-08-03 E2E 实测
    唤醒因此永远降级旧路径、撞挂起 dispatch 自己持有的执行锁）。worker /
    combined 角色没有 client，走单例分支。"""
    from app.services.infra.dbos_orchestrator import get_dbos_client

    client = get_dbos_client()
    if client is not None:
        await client.send_async(workflow_id, payload, topic=topic)
        return
    from dbos import DBOS

    await DBOS.send_async(workflow_id, payload, topic=topic)


async def await_user_input(issue_id: int, *, ttl_seconds: int) -> Optional[dict]:
    """挂起当前 workflow 等 issue 的用户回复。超时/畸形 payload 均返 None。"""
    payload = await _recv_async(_topic_for(issue_id), timeout_seconds=ttl_seconds)
    if not isinstance(payload, dict) or not payload.get("reply_text"):
        if payload is not None:
            logger.warning(
                f"[input_gate] malformed payload for issue {issue_id}: {payload!r}"
            )
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


_QUESTION_MARKER_KEYS = ("question_id", "kind", "options", "allow_free_text", "run_id")


def build_awaiting_marker(
    *,
    prompt: str,
    issue_id: int,
    now: str,
    question: Optional[dict] = None,
) -> dict:
    """The ``issues.execution_state.awaiting_input`` value. Without a typed
    question it is exactly the pre-2a shape; with one it adds the fields the
    reply endpoint needs to validate an answer and the UI needs to render
    buttons (``NeedsInputCard`` keeps reading ``prompt``)."""
    marker: dict = {"prompt": prompt, "since": now, "issue_id": issue_id}
    if question:
        for key in _QUESTION_MARKER_KEYS:
            if key in question:
                marker[key] = question[key]
    return marker


async def mark_awaiting_input(
    *,
    workflow_id: str,
    issue_id: int,
    user_id: str,
    prompt: str,
    question: Optional[dict] = None,
) -> None:
    """写等待标记并投 inbox 通知。任一失败只记日志 —— 标记失败不阻断挂起，
    回复会走旧路径兜底。

    权威落点是 ``issues.execution_state.awaiting_input``（jsonb merge，不碰
    ``set_status`` 写的 agent_outcome/outcome_reason 键）——2026-08-03 E2E
    实测 issue dispatch **没有** task_tracking 行（spec 的落点假设错了），
    标记写在那里等于没写，回复分流永远降级旧路径并撞上 dispatch 自己持有
    的 execution_locked_at。

    这里曾经有一段"写时序与 set_status 兼容"的说明（挂起入口先
    route_finish_outcome 覆盖式写、本函数后 merge；唤醒时 clear 必须排在
    set_status(in_progress) 之前）。那是在绕开 ``set_status`` 的整列覆盖写：
    顺序错了标记就没了。``set_status`` 现在恒为 jsonb merge 且只接管
    ``error_code``/``error_message`` 两个键，不再碰 ``awaiting_input``，
    所以两处顺序都不再是正确性前提。

    task_tracking.metadata 同步 best-effort 装饰写保留（今天恒 0 行，若
    未来 dispatch 建了 task 行，Task Center 行高亮即自动点亮）。"""
    from sqlalchemy import cast, func, literal, select, text, update
    from sqlalchemy.dialects.postgresql import JSONB

    from app.db.session import read_scope, write_scope
    from app.models import Issues, TaskTracking
    from app.services.notifications import notify

    clipped = (prompt or "")[:_PROMPT_MAX]
    now = datetime.now(timezone.utc).isoformat()
    marker = json.dumps(
        build_awaiting_marker(
            prompt=clipped, issue_id=issue_id, now=now, question=question
        )
    )
    empty_jsonb = cast(literal("{}"), JSONB)
    marker_jsonb = cast(literal(marker), JSONB)
    try:
        async with write_scope() as session:
            await session.execute(text("SET LOCAL ROLE service_role"))
            await session.execute(
                update(Issues)
                .where(Issues.id == issue_id)
                .values(
                    execution_state=func.coalesce(
                        Issues.execution_state, empty_jsonb
                    ).op("||", return_type=JSONB)(
                        func.jsonb_build_object("awaiting_input", marker_jsonb)
                    )
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[input_gate] mark issue marker failed issue={issue_id}: {exc}")
    try:
        async with write_scope() as session:
            await session.execute(text("SET LOCAL ROLE service_role"))
            await session.execute(
                update(TaskTracking)
                .where(TaskTracking.dbos_workflow_id == workflow_id)
                .values(
                    metadata_=func.coalesce(TaskTracking.metadata_, empty_jsonb).op(
                        "||", return_type=JSONB
                    )(func.jsonb_build_object("awaiting_input", marker_jsonb))
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[input_gate] mark metadata failed wf={workflow_id}: {exc}")
    # 深链 link_id 口径：issue 链接携带人类标识符（MH-N），issues 路由按它
    # 解析（scheduled_master 的 autopilot producer 同款,含 str(id) 兜底）。
    link_id = str(issue_id)
    try:
        async with read_scope() as session:
            ident = (
                await session.execute(
                    select(Issues.identifier).where(Issues.id == issue_id)
                )
            ).scalar_one_or_none()
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


async def mark_question_answered(*, workflow_id: str, question_id: str) -> None:
    """Stamp ``awaiting_input.answered_at`` on the issue marker once an answer
    was DELIVERED (woken or dispatched). Until the workflow clears the marker
    itself this is what makes a second POST of the same answer a 409 instead
    of a second wake (phase 2a answer-channel idempotency). Best-effort."""
    from sqlalchemy import cast, func, literal, text, update
    from sqlalchemy.dialects.postgresql import JSONB

    from app.db.session import write_scope
    from app.models import Issues

    stamp = json.dumps(
        {
            "answered_at": datetime.now(timezone.utc).isoformat(),
            "answered_question_id": question_id,
        }
    )
    try:
        async with write_scope() as session:
            await session.execute(text("SET LOCAL ROLE service_role"))
            await session.execute(
                update(Issues)
                .where(
                    Issues.dbos_workflow_id == workflow_id,
                    Issues.execution_state.has_key("awaiting_input"),
                )
                .values(
                    execution_state=Issues.execution_state.op("||", return_type=JSONB)(
                        func.jsonb_build_object(
                            "awaiting_input",
                            Issues.execution_state.op("->", return_type=JSONB)(
                                "awaiting_input"
                            ).op("||", return_type=JSONB)(cast(literal(stamp), JSONB)),
                        )
                    )
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[input_gate] mark answered failed wf={workflow_id} q={question_id}: {exc}"
        )


async def clear_awaiting_input(*, workflow_id: str) -> None:
    """移除等待标记（issues 权威位 + task_tracking 装饰位）。
    inbox 行有意保留（用户稍后仍可从收件箱进入）。"""
    from sqlalchemy import text, update
    from sqlalchemy.dialects.postgresql import JSONB

    from app.db.session import write_scope
    from app.models import Issues, TaskTracking

    try:
        async with write_scope() as session:
            await session.execute(text("SET LOCAL ROLE service_role"))
            await session.execute(
                update(Issues)
                .where(
                    Issues.dbos_workflow_id == workflow_id,
                    Issues.execution_state.has_key("awaiting_input"),
                )
                .values(
                    execution_state=Issues.execution_state.op("-", return_type=JSONB)(
                        "awaiting_input"
                    )
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[input_gate] clear issue marker failed wf={workflow_id}: {exc}"
        )
    try:
        async with write_scope() as session:
            await session.execute(text("SET LOCAL ROLE service_role"))
            await session.execute(
                update(TaskTracking)
                .where(TaskTracking.dbos_workflow_id == workflow_id)
                .values(
                    metadata_=TaskTracking.metadata_.op("-", return_type=JSONB)(
                        "awaiting_input"
                    )
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[input_gate] clear metadata failed wf={workflow_id}: {exc}")


async def _fetch_awaiting_rows() -> list[dict]:
    """所有带 awaiting_input 标记的 issue 行 + 其 DBOS app_version。

    权威标记位在 issues.execution_state（issue dispatch 没有 task_tracking
    行，见 mark_awaiting_input）。JOIN dbos.workflow_status 是引擎侧读
    （非 UI 数据源，不违路线 C——路线 C 禁的是前端/列表 endpoint 直查
    引擎表；reaper 恰恰是引擎孤儿的清道夫，与 _bg_reap_internal_queue
    同族）。"""
    from app.db import engine as db_engine

    return await db_engine.fetch_all(
        """SELECT i.dbos_workflow_id, i.id AS issue_id, w.application_version
           FROM public.issues i
           JOIN dbos.workflow_status w ON w.workflow_uuid = i.dbos_workflow_id
           WHERE i.execution_state ? 'awaiting_input'
             AND w.status IN ('PENDING', 'ENQUEUED')""",
    )


async def _cancel_workflow(workflow_id: str) -> None:
    from dbos import DBOS

    await DBOS.cancel_workflow_async(workflow_id)


async def _clear_issue_lock(workflow_id: str) -> None:
    """按 dbos_workflow_id 定位（atomic_checkout 写过它,比 task_tracking.issue_id
    回填更可靠）,释放被 reap 的 dispatch 持有的 issue 执行锁。"""
    from sqlalchemy import text, update

    from app.db.session import write_scope
    from app.models import Issues

    async with write_scope() as session:
        await session.execute(text("SET LOCAL ROLE service_role"))
        await session.execute(
            update(Issues)
            .where(Issues.dbos_workflow_id == workflow_id)
            .values(execution_locked_at=None)
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
