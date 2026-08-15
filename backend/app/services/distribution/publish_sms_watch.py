"""让"这次发布卡在验证码上"这件事，在发布调用还没返回时就能被用户看见。

## 为什么需要一个并发观察者

发布是**一次阻塞 HTTP 调用**：``run_publish_accounts_step`` 里 ``await
adapter.publish(...)`` 一压就是十几分钟。浏览器停下来等验证码时，那个 await
还在原地 —— **没有任何一段后端代码有机会去写 task_tracking**，于是用户只能看
见一个转圈，看不到"要你输个码"。

所以在发起那次调用的同时，另起一个协程去轮询浏览器（``GET
/session/publish/{cid}/sms``），把"在等码"翻译成 ``task_tracking.metadata``
里一个类型化的块。前端读到它就渲染输入框。

这是「触发路径必须类型化失败回显」的直接落地：用户按下发布，平台要码，如果
没人把这件事说出来，表现就是"什么也没发生"——本仓库反复吃亏的那一类。

## 只写 metadata，绝不碰 phase 列

路线 C 第 2、3 条：``phase / status / progress / started_at / completed_at /
error_msg`` 由 ``mirror_dbos_lifecycle_to_tracking`` trigger 单向同步，业务代码
**一律不许 PATCH**。"在等验证码"是业务装饰信息，不是引擎生命周期状态 —— 这次
发布在 DBOS 眼里确实仍然 in_progress，那是真话。所以它走 ``metadata`` jsonb，
用 ``manager.patch_metadata``，一列 phase 都不动。

## 观察者绝不许把发布搞挂

轮询里任何异常都被吞掉并记日志。它是**装饰通道**：轮询挂了，最坏结果是用户
看不到输入框、发布按老路超时失败 —— 与修复前一模一样。而让一次轮询异常冒泡
去打断一个已经上传完几百 MB 的发布，是拿主路径给辅助功能陪葬。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

# ``task_tracking.metadata`` 里承载这件事的键。前端按它取，后端按它清。
PUBLISH_SMS_KEY = "publish_sms"

# 轮询间隔。与登录 workflow 的节奏同量级：这是人的时间尺度，1s 轮询只会多打
# 浏览器 3 倍的招呼，换不来任何用户能感知的差别。
DEFAULT_POLL_INTERVAL_S = 3.0


def new_correlation_id() -> str:
    """给一次发布尝试取名。

    每次尝试一个新的，不复用 ``publish_task_accounts.id``：重试会重跑同一行，
    而复用会让上一次尝试的残留挑战被新一次寻址到 —— 用户的码进错上下文，是
    这里唯一真正危险的错法。
    """
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _patch(manager: Any, workflow_id: str, block: Optional[dict]) -> None:
    """把 ``publish_sms`` 块写进（或清出）metadata。失败只记日志。"""
    try:
        await manager.patch_metadata(workflow_id, {PUBLISH_SMS_KEY: block})
    except Exception as e:  # noqa: BLE001 — 装饰通道，不许影响发布
        logger.warning(f"[publish.sms] metadata patch failed: {e}")


async def _watch(
    *,
    client: Any,
    manager: Any,
    workflow_id: str,
    correlation_id: str,
    account_id: int,
    platform: str,
    poll_interval_s: float,
) -> None:
    """轮询浏览器，把"在等码"的状态镜像进 task_tracking.metadata。

    ``announced`` 让写入只发生在**状态翻转**时，而不是每一轮。一次发布可能等
    3 分钟，每 3 秒 PATCH 一次 jsonb 就是 60 次没有新信息的写。
    """
    announced = False
    try:
        while True:
            await asyncio.sleep(poll_interval_s)
            try:
                status = await client.get_publish_sms(correlation_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[publish.sms] poll failed: {e}")
                continue

            if status.waiting and not announced:
                await _patch(
                    manager,
                    workflow_id,
                    {
                        "waiting": True,
                        "correlation_id": correlation_id,
                        "account_id": account_id,
                        "platform": platform,
                        "attempts_left": status.attempts_left,
                        "max_attempts": status.max_attempts,
                        "seconds_remaining": status.seconds_remaining,
                        "updated_at": _now_iso(),
                    },
                )
                logger.info(
                    f"[publish.sms] publish is waiting for a code platform={platform}"
                )
                announced = True
            elif announced and not status.waiting:
                # 挑战结束（收了码 / 超时 / 用光次数）。立刻收掉输入框，别让用户
                # 对着一个没人在等的框继续输 —— 那是"绿灯但什么也没发生"的另一
                # 种写法，只是方向反过来。
                await _patch(
                    manager,
                    workflow_id,
                    {
                        "waiting": False,
                        "correlation_id": correlation_id,
                        "outcome": status.outcome,
                        "message": status.message,
                        "updated_at": _now_iso(),
                    },
                )
                logger.info(f"[publish.sms] challenge ended outcome={status.outcome}")
                announced = False
    except asyncio.CancelledError:
        # 发布结束时的正常收场，不是错误。
        raise


async def publish_with_sms_channel(
    *,
    adapter: Any,
    account: dict,
    intent: Any,
    workflow_id: Optional[str],
    account_id: int,
    platform: str = "douyin",
    manager: Any = None,
    client: Any = None,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> Any:
    """发起一次发布，并在它卡在验证码上时让用户看得见。

    返回值与 ``adapter.publish`` 完全一致 —— 调用方的结算逻辑一行都不用改。

    没有 ``workflow_id`` 时（单测、无 DBOS runtime）退化成裸调用：**仍然带
    correlation_id**，所以浏览器侧的通道照常建立，只是没人把状态镜像出来。
    这个降级是刻意的 —— 让"能不能供码"和"能不能显示"各自独立地失败，比让前者
    依赖后者好。
    """
    correlation_id = new_correlation_id()

    if not workflow_id:
        return await adapter.publish(account, intent, correlation_id=correlation_id)

    if manager is None:
        from app.services.infra.unified_task_manager import get_task_manager

        manager = get_task_manager()
    if client is None:
        from app.services.distribution.browser_client import BrowserClient

        client = BrowserClient()

    watcher = asyncio.create_task(
        _watch(
            client=client,
            manager=manager,
            workflow_id=workflow_id,
            correlation_id=correlation_id,
            account_id=account_id,
            platform=platform,
            poll_interval_s=poll_interval_s,
        )
    )
    try:
        return await adapter.publish(account, intent, correlation_id=correlation_id)
    finally:
        watcher.cancel()
        # 等它真的收干净，否则会留下一个悬空 task 在发布早已返回之后还去写
        # metadata —— 那正好会把一个不存在的输入框重新画到用户屏幕上。
        try:
            await watcher
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        # 无论怎么结束的，最后一定把块清掉。发布已经返回 == 没有任何上下文还在
        # 等码，此时屏幕上残留一个输入框，用户输进去的每一个码都会石沉大海。
        await _patch(manager, workflow_id, None)


__all__ = [
    "PUBLISH_SMS_KEY",
    "DEFAULT_POLL_INTERVAL_S",
    "new_correlation_id",
    "publish_with_sms_channel",
]
