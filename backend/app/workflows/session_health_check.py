"""session_health_check — 会话健康巡检的**调度**（gap-closure P0-4）。

背景：spec §4.4 的巡检早就写好了 repository 侧
(``SocialAccountsRepository.list_session_accounts_for_check`` + ``SESSION_CHECK_BATCH``)
和两个单测，**唯独没有任何生产调用方**。后果是账号掉线完全静默：UI 一直显示
``active``，直到某次发布失败才被发现。这个模块就是缺的那一半 —— 有测试 ≠ 接线了。

它做什么
========
每 tick：取一批「最久没校验过」的 session 账号 → 逐个开浏览器
``/session/validate`` → 把结论写回 ``status`` / ``session_checked_at``
（顺带修 username/avatar，与 ``/accounts/{id}/refresh`` 同源）。

节奏为什么这么慢（刻意的）
==========================
每校验一个账号 == 起一次真浏览器、带着该账号的 cookie 登一次平台。太频繁
**本身就是风控信号**，会把巡检变成掉线的原因而不是发现手段。三道闸叠起来：

  * ``SESSION_CHECK_CRON`` —— 30 分钟一 tick（不是分钟级）
  * ``MIN_RECHECK_INTERVAL_S`` —— 单账号 6 小时内不重复校验，于是**每个账号
    每天最多被登 4 次**；repo 的 ``ORDER BY session_checked_at ASC NULLS FIRST``
    保证"最久没查的"和"刚绑还没查过的"排在最前
  * ``MAX_PER_TICK`` —— 每 tick 至多 ``SESSION_CHECK_BATCH`` 个（repo 侧默认 20），
    上界与"一次不要起一百个 context"是同一条约束

repo 只提供了排序 + 条数上限，**最小复检间隔不在 SQL 里**（那是"哪些行值得
看"与"这一轮要不要动它"两件事），所以由本模块的 ``select_due`` 补上。

三条不能违反的边界
==================
1. **基建失败 ≠ 账号掉线**（spec §7.8）。容器宕机 / 代理死 / 密钥解不开时
   我们*没有拿到结论*，此时**既不能标 needs_relogin，也不能刷新
   session_checked_at** —— 后者会把一次宕机记成"刚查过，一切正常"。判据是
   ``browser_client.is_infra_failure``，不是 ``success`` 为假。
2. **不建 task_tracking 行**（路线 C 规则 5，对齐 ``stranded_issue_monitor`` /
   ``publish_issue_mirror``）。这是 scheduled housekeeping，不是用户任务。
3. **失败 raise，不 return failed dict**（路线 C 规则 4）。单账号失败是业务
   状态、要继续跑完这一批；workflow 级别的意外（扫不出候选、健康探针本身抛）
   直接往外抛，让 DBOS 记成 ERROR。

与并发发布的互斥
================
发布 / 校验 / 巡检三条链都会开 context，同一账号两个活动 context 会互相踢下线
（spec §7.5）。所以每个账号都走 ``account_session_lock``，且 ``attempts=1``：
巡检是可以等下一轮的，为了它去排队 30 秒毫无意义，抢不到就跳过。

凭证边界（spec §7.6）
=====================
``session_state`` 明文只在 ``_check_one_account`` 的栈上存在，且 ``finally``
里立刻 pop。**任何 step 的入参/返回值都只有 account id、platform、结论字符串**
—— DBOS 会把 step 的 input/output 落库，把凭证（哪怕密文）放进去等于给它做了
一份不受 secret_box 管的副本。同理 ``list_session_accounts_for_check`` 那行
带着密文 ``proxy_url``，所以扫描 step 只把 id/platform 传出来。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from dbos import DBOS
from loguru import logger

# 30 分钟一轮。是常量而非 env —— cron 字符串写错会在 worker 启动时炸掉整个
# scheduled bundle，不值得为了可调性冒这个险；真要调就改这里走一次 review。
SESSION_CHECK_CRON = "*/30 * * * *"


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


# 单账号最小复检间隔。6h → 每账号每天最多 4 次真实登录。
MIN_RECHECK_INTERVAL_S = _env_int("SESSION_CHECK_MIN_INTERVAL_SECONDS", 6 * 3600)


def _max_per_tick() -> int:
    """每 tick 的账号上界，默认取 repo 的 ``SESSION_CHECK_BATCH``。

    延迟到调用时才读，是为了让 ``SESSION_CHECK_BATCH`` 保持单一来源（import
    在模块顶层会把它烤成一个副本，repo 改了这边不跟）。
    """
    from app.repositories.social_accounts_repository import SESSION_CHECK_BATCH

    return _env_int("SESSION_CHECK_MAX_PER_TICK", SESSION_CHECK_BATCH)


# 每个账号的结论 —— 也是返回给 DBOS 的计数键。
VERDICT_HEALTHY = "healthy"  # 会话活着：status=active + session_checked_at 前进
VERDICT_INVALID = "invalid"  # 会话真的死了：status=needs_relogin
VERDICT_INFRA = "infra"  # 没问出结论：**不动任何列**
VERDICT_BUSY = "busy"  # 账号正被发布占用，下一轮再说
VERDICT_MISSING = "missing"  # 扫到之后被删了
VERDICT_ERROR = "error"  # 该账号意外异常（已记日志，不影响其他账号）


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(val))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def select_due(
    candidates: Iterable[dict],
    *,
    now: datetime,
    min_interval_s: int = MIN_RECHECK_INTERVAL_S,
    max_accounts: int = 20,
) -> list[dict]:
    """纯函数：候选里这一轮真正该校验的那些。

    从没查过（``session_checked_at`` 为 NULL）永远算 due —— 刚绑的账号是最该
    确认一次的。查过的要满够 ``min_interval_s``。

    不 ``break``（尽管 repo 是按 ``session_checked_at ASC`` 排的）：靠调用方的
    排序来提前收尾，等于把"repo 的 ORDER BY"变成本函数正确性的隐式前提；万一
    以后排序变了，``break`` 会静默漏掉后面所有该查的账号，而 ``continue`` 顶多
    多遍历几行。
    """
    out: list[dict] = []
    for c in candidates:
        if len(out) >= max_accounts:
            break
        ts = _parse_ts(c.get("session_checked_at"))
        if ts is not None and (now - ts).total_seconds() < min_interval_s:
            continue
        out.append(c)
    return out


def classify_validation(result: dict) -> str:
    """纯函数：``validate_session`` 的 §7.8 信封 → 三种结论之一。

    **顺序是有意义的**：先判基建失败再判 success。``is_infra_failure`` 为真时
    ``success`` 也是 False，若先看 success 就会把"容器宕机"判成"账号掉线"，
    一次宕机让全部账号被要求重扫码 —— 正是 §7.8 存在的理由。
    """
    from app.services.distribution.browser_client import is_infra_failure

    if is_infra_failure(result):
        return VERDICT_INFRA
    return VERDICT_HEALTHY if result.get("success") else VERDICT_INVALID


@DBOS.step()
async def _scan_due_accounts() -> list[dict[str, str]]:
    """这一轮要校验的账号 —— 只返回 ``{id, platform}``。

    刻意不把 repo 那行原样传出去：它带着密文 ``proxy_url``（mig 402），而 step
    的返回值会被 DBOS 落库（§7.6）。真正要用的环境由 ``get_with_session`` 在
    校验那一刻现取。
    """
    from app.db.scope import system_request_scope
    from app.repositories.social_accounts_repository import SocialAccountsRepository

    limit = _max_per_tick()
    async with system_request_scope(
        "session health sweep: scan session accounts across tenants"
    ):
        rows = await SocialAccountsRepository().list_session_accounts_for_check(
            limit=limit
        )
    due = select_due(
        rows,
        now=datetime.now(timezone.utc),
        min_interval_s=MIN_RECHECK_INTERVAL_S,
        max_accounts=limit,
    )
    return [
        {"id": str(r["id"]), "platform": str(r.get("platform") or "douyin")}
        for r in due
    ]


@DBOS.step()
async def _browser_is_healthy() -> bool:
    """浏览器容器是否可用（``GET /healthz``，探真信号：``browser_ready``）。

    在**开第一个 context 之前**问一次，而不是靠逐账号的 infra 失败去发现。两个
    原因：容器宕机时这一整轮无论如何都问不出结论，逐个试只是白等一堆超时；更
    要紧的是排序会把失败的那个账号永远留在队首（它的 ``session_checked_at``
    不会前进），若改成"遇到 infra 就中止本轮"，一个账号就能把其余账号饿死。
    所以：**全局失败在这里一次性挡掉，账号级 infra 失败只跳过它自己。**
    """
    from app.services.distribution.browser_client import BrowserClient

    health = await BrowserClient().health()
    if not health.ok:
        logger.warning(
            f"[session.sweep] browser service unhealthy "
            f"(error_kind={health.error_kind} message={health.message}) — tick skipped"
        )
    return bool(health.ok)


@DBOS.step()
async def _module_enabled() -> bool:
    """Distribution 模块的 ACCESS 开关（admin 控制、fail-closed）。

    关掉模块的人的意思是"别再碰这些平台账号了"，而巡检恰恰是唯一一条不需要
    用户点任何按钮就会去登平台的路径 —— 它必须服从同一个开关，否则模块"关了"
    还在每 30 分钟登一次抖音。
    """
    from app.services.distribution.module_config import is_module_enabled

    return await is_module_enabled()


async def _check_one_account(account_id: str, platform: str) -> str:
    """校验一个账号并写回结论。返回 ``VERDICT_*``，**从不抛**。

    写回严格对齐 ``distribution_router._refresh_session_account``（同一套校验的
    两个调用方：那边把结论映射成 HTTP 状态码，这边映射成计数器）：

      * 基建失败 → 什么都不写（§7.8）
      * 会话失效 → ``mark_needs_relogin``（``session_checked_at`` 不动：那一列
        的含义是"最后一次成功确认",拿它记失败会让下一轮以为刚查过）
      * 会话健康 → 回写 profile + ``update_session_state(None, status="active")``
        （``None`` = 只推进 ``session_checked_at``，不会把活会话抹成空）
    """
    from app.repositories.social_accounts_repository import (
        SESSION_STATE_DECRYPT_FAILED,
        SocialAccountsRepository,
    )
    from app.services.distribution.registry import get_session_adapter
    from app.services.distribution.session_adapter import decrypt_failure_result
    from app.services.distribution.session_lock import account_session_lock

    repo = SocialAccountsRepository()
    acct: Optional[dict] = None
    try:
        acct = await repo.get_with_session(int(account_id))
        if not acct:
            return VERDICT_MISSING
        if acct.get(SESSION_STATE_DECRYPT_FAILED):
            # 密文在、但打不开：我们的密钥错了，平台会话八成好得很。按基建失败
            # 处理（decrypt_failure_result 会记 ERROR 日志）——**跳过它继续下
            # 一个**，而不是中止本轮，否则一行坏数据能饿死其余所有账号。
            decrypt_failure_result(
                "session_state could not be decrypted", account_id=account_id
            )
            return VERDICT_INFRA

        # attempts=1：抢不到锁说明该账号正在发布/校验，巡检等下一轮就行，
        # 没有理由为它挂住一个事务 30 秒（session_lock 的 idle-in-transaction）。
        async with account_session_lock(account_id, attempts=1) as acquired:
            if not acquired:
                logger.info(f"[session.sweep] account={account_id} busy — skipped")
                return VERDICT_BUSY
            result = await get_session_adapter(platform).validate_session(acct)
    except Exception as exc:  # noqa: BLE001 —— 单账号失败不该炸掉整批
        logger.warning(f"[session.sweep] account={account_id} check errored: {exc!r}")
        return VERDICT_ERROR
    finally:
        # 明文凭证只在这一帧里活着（§7.6）。
        if acct is not None:
            acct.pop("session_state", None)

    verdict = classify_validation(result)
    try:
        if verdict == VERDICT_INFRA:
            logger.warning(
                f"[session.sweep] account={account_id} inconclusive "
                f"(error_kind={(result.get('detail') or {}).get('error_kind')}) "
                f"— status untouched"
            )
            return verdict
        if verdict == VERDICT_INVALID:
            logger.warning(
                f"[session.sweep] account={account_id} session dead "
                f"(reason={(result.get('detail') or {}).get('reason')}) "
                f"— marking needs_relogin"
            )
            await repo.mark_needs_relogin(int(account_id))
            return verdict

        profile = (result.get("detail") or {}).get("profile") or {}
        await repo.update_profile(
            int(account_id),
            username=profile.get("username"),
            avatar_url=profile.get("avatar_url"),
        )
        # session_state=None：会话活着但校验没产出新 cookie，只推进时间戳。
        await repo.update_session_state(int(account_id), None, status="active")
        return verdict
    except Exception as exc:  # noqa: BLE001 —— 写回失败同样不该炸掉整批
        logger.warning(
            f"[session.sweep] account={account_id} write-back failed: {exc!r}"
        )
        return VERDICT_ERROR


@DBOS.step()
async def _check_account_step(account_id: str, platform: str) -> str:
    """``_check_one_account`` 的 DBOS 包装。

    分成两个函数是为了让业务逻辑能在没有 DBOS runtime 的情况下被单测直接调用
    （``@DBOS.step()`` 装饰过的函数在测试里要么需要起 runtime、要么要绕过装饰
    器），而不是把逻辑写进装饰器下面让它只能靠集成测试覆盖。
    """
    from app.db.scope import system_request_scope

    async with system_request_scope(
        f"session health sweep: validate account {account_id}"
    ):
        return await _check_one_account(account_id, platform)


@DBOS.scheduled(SESSION_CHECK_CRON)
@DBOS.workflow()
async def session_health_check_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> dict[str, int]:
    """一轮巡检。返回各结论的计数（scheduled housekeeping，不建 task_tracking）。

    这里**不吞异常**：扫描或探针本身抛出去就是 workflow 失败，DBOS 记 ERROR
    （路线 C 规则 4 —— 返回 failed dict 会被当成 SUCCESS）。账号级失败早在
    ``_check_one_account`` 里被收敛成 verdict 了。
    """
    counts: dict[str, int] = {"due": 0, "checked": 0}
    if not await _module_enabled():
        return counts

    candidates = await _scan_due_accounts()
    counts["due"] = len(candidates)
    if not candidates:
        return counts

    if not await _browser_is_healthy():
        # 容器不可用：一个账号都不碰，也不刷新任何时间戳 —— 这一轮等于没发生。
        counts["skipped_unhealthy"] = len(candidates)
        return counts

    for c in candidates:
        verdict = await _check_account_step(c["id"], c["platform"])
        counts[verdict] = counts.get(verdict, 0) + 1
        counts["checked"] += 1

    logger.info(f"[session.sweep] tick: {counts}")
    return counts


__all__ = [
    "MIN_RECHECK_INTERVAL_S",
    "SESSION_CHECK_CRON",
    "classify_validation",
    "select_due",
    "session_health_check_workflow",
]
