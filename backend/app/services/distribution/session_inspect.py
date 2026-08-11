"""只读页面勘探的 backend 侧（T0，规格 §3.2）。

这一层存在的唯一理由：**账号级串行锁在 backend**。
=================================================
浏览器容器不碰数据库，也就拿不到那把锁 —— 它是 PG advisory lock
（``session_lock.account_session_lock``，spec §7.5）。同一账号上同时开两个
context 会互相把对方踢下线，轻则本次失败，重则 storage_state 被平台作废、
用户白扫一次码。发布 / 巡检 / 回读三条链都在这里取锁，勘探必须走同一把，
否则它就是那三条链之外的第四个并发源。

所以调用链是：

    admin router  →  本模块（解密 + 取锁 + 环境组装）
                  →  BrowserClient.inspect_page  →  nous-browser /session/inspect

而不是让谁直接去调浏览器容器。直调不是"少一跳"，是**绕过锁**。

明文纪律（spec §7.6）
====================
``session_state`` 的明文只在本函数这一帧的栈上存在，``finally`` 立刻 pop；
它**不进返回值、不进日志、不落盘**。返回给 HTTP 调用方的只有浏览器侧那份
受控摘要（计数 / 属性 / 截断文本）加一个布尔量说明会话有没有被续期。

``attempts=1``
==============
抢不到锁 = 该账号此刻正在发布或巡检 → 立刻返回类型化的 ``account_busy``。
勘探是人在等结果的手动动作，让它排队 30 秒没有意义；更重要的是**勘探绝不
该让一次真实发布等它**，所以这条链只做"让开"，不做"抢占"。
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Sequence

from loguru import logger

REASON_ACCOUNT_MISSING = "account_missing"
REASON_ACCOUNT_BUSY = "account_busy"
REASON_AUTH_TYPE_MISMATCH = "auth_type_mismatch"
REASON_PLATFORM_UNSUPPORTED = "platform_unsupported"


def _envelope(
    status: str,
    message: str,
    *,
    reason: str,
    **extra: Any,
) -> dict[str, Any]:
    """§7.8 信封 + 勘探自己的两个字段。失败路径永远走这里。"""
    return {
        "success": False,
        "status": status,
        "message": message,
        "detail": {"reason": reason, **extra},
        "observation": {},
        "session_refreshed": False,
    }


async def inspect_account_page(
    account_id: int,
    url: str,
    *,
    seed_files: Optional[Sequence[Mapping[str, Any]]] = None,
    text_probes: Optional[Sequence[str]] = None,
    selector_probes: Optional[Sequence[str]] = None,
    options: Optional[Mapping[str, Any]] = None,
    client: Any = None,
) -> dict[str, Any]:
    """用某个已绑账号的会话打开一个白名单内的页面并读它。返回信封 dict。

    **从不抛**（除了调用方传了非法参数）：每条失败路径都是类型化结论，因为
    调用方要按 reason 分支给出可读的回显 —— silent no-op 与"笼统 500"都不
    可接受（CLAUDE.md：触发路径必须类型化失败回显）。
    """
    from app.repositories.social_accounts_repository import (
        SESSION_STATE_DECRYPT_FAILED,
        SocialAccountsRepository,
    )
    from app.services.distribution.browser_client import BrowserClient, SessionStatus
    from app.services.distribution.session_adapter import (
        AUTH_TYPE_SESSION,
        SESSION_PLATFORM_PROFILES,
        SessionStateError,
        build_environment,
        decrypt_failure_result,
        parse_session_state,
    )
    from app.services.distribution.session_lock import account_session_lock

    accounts_repo = SocialAccountsRepository()
    acct: Optional[dict] = None
    try:
        acct = await accounts_repo.get_with_session(int(account_id))
        if not acct:
            return _envelope(
                SessionStatus.FAILED.value,
                "account not found",
                reason=REASON_ACCOUNT_MISSING,
            )

        platform = acct.get("platform") or ""
        if platform not in SESSION_PLATFORM_PROFILES:
            return _envelope(
                SessionStatus.FAILED.value,
                f"platform {platform!r} has no session profile",
                reason=REASON_PLATFORM_UNSUPPORTED,
                platform=platform,
            )
        if acct.get("auth_type") != AUTH_TYPE_SESSION:
            # OAuth 账号根本没有 storage_state 可用。类型化拒绝，不静默。
            return _envelope(
                SessionStatus.FAILED.value,
                f"account auth_type is {acct.get('auth_type')!r}, not 'session'",
                reason=REASON_AUTH_TYPE_MISMATCH,
            )
        if acct.get(SESSION_STATE_DECRYPT_FAILED):
            # 密文在、打不开：我们的密钥错了，平台会话八成好得很。基建失败，
            # 账号状态一律不动。
            failure = decrypt_failure_result(
                "session_state could not be decrypted", account_id=account_id
            )
            return {**failure, "observation": {}, "session_refreshed": False}

        try:
            storage_state = parse_session_state(acct)
        except SessionStateError as exc:
            return _envelope(
                SessionStatus.SESSION_INVALID.value, str(exc), reason=exc.reason
            )

        environment = build_environment(acct.get("environment"))
        browser = client or BrowserClient()

        async with account_session_lock(account_id, attempts=1) as acquired:
            if not acquired:
                # 刻意**不**配 error_kind。这不是"我们没问出来"的基建故障，
                # 而是一条确定的结论：那个账号此刻有别的浏览器会话在跑，让开
                # 是对的。（勘探本来也不改任何账号状态，所以这条 reason 的
                # 唯一职责就是把话说清楚。）
                return _envelope(
                    SessionStatus.FAILED.value,
                    "another browser session is already running for this account",
                    reason=REASON_ACCOUNT_BUSY,
                )
            result = await browser.inspect_page(
                platform,
                storage_state,
                url,
                environment=environment,
                seed_files=seed_files,
                text_probes=text_probes,
                selector_probes=selector_probes,
                options=options,
            )
    finally:
        # 明文只活在这一帧（spec §7.6）。
        if acct is not None:
            acct.pop("session_state", None)

    refreshed = False
    if result.updated_storage_state:
        # 会话滑动续期：勘探也是一次真实的已认证页面加载，平台照样下发新
        # cookie。不写回等于让这次额外的浏览器任务净消耗会话寿命。写回失败
        # 不该吞掉已经拿到的观测结果 —— 那才是这次调用的产物。
        try:
            await accounts_repo.update_session_state(
                int(account_id),
                json.dumps(result.updated_storage_state, ensure_ascii=False),
            )
            refreshed = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[session.inspect] account={account_id} "
                f"state write-back failed: {type(exc).__name__}"
            )

    logger.info(
        f"[session.inspect] account={account_id} status={result.status} "
        f"refreshed={refreshed}"
    )
    return {
        **result.result.to_dict(),
        # 摘要原样透传。**明文 storage_state 到此为止**：它不在这个 dict 里，
        # 也永远不会在。
        "observation": dict(result.observation),
        "session_refreshed": refreshed,
    }


__all__ = [
    "REASON_ACCOUNT_BUSY",
    "REASON_ACCOUNT_MISSING",
    "REASON_AUTH_TYPE_MISMATCH",
    "REASON_PLATFORM_UNSUPPORTED",
    "inspect_account_page",
]
