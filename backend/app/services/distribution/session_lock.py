"""账号级串行锁 (spec §7.5) —— 同一账号同时只能有一个浏览器会话。

为什么需要
==========
平台的 web 会话对"同一账号两个活动 context"是敌意的：两边会互相把对方踢
下线，轻则本次发布失败，重则 storage_state 被平台作废、账号进
``needs_relogin``，用户白扫一次码。发布 / 校验 / 巡检三条链都会开 context，
它们彼此不知道对方存在，所以串行化必须落在**跨进程可见**的地方 —— DB。

为什么是 advisory lock 而不是状态列 CAS
=======================================
``issue_lifecycle.acquire_turn_lock`` 用的是状态列 CAS（``issues
.execution_locked_at``），那是本仓库已有的范式。它这里用不了：
``social_accounts`` 没有对应的列，而 S3 不含 migration。CAS 还多一个麻烦
—— 持锁进程被 kill 后锁会**永久留在表里**，要另配一套租约/清扫。advisory
lock 没有这个问题：连接断了锁自动没。

为什么是 **xact** 版本而不是 session 版本
=========================================
两个约束叠起来只剩这一个选择：

1. **连接走 Supavisor 事务级池**（见 ``app/db/engine.py``：NullPool +
   ``statement_cache_size=0``，因为"Supavisor 才是池"）。事务级池只在一个
   事务的存续期内把客户端连接钉在同一个 PG backend 上。``pg_advisory_lock``
   是 **session 级**的，跨事务持有时下一条语句可能落到另一个 backend ——
   锁既不在你以为的地方，也没人能解开它。
2. 因此锁必须在**一个事务内**取得并持有。``pg_try_advisory_xact_lock``
   正是这个语义，且事务结束（commit / rollback / 连接死掉）自动释放，
   不存在忘记解锁或进程猝死留下死锁的路径。

**代价要说清楚**：持锁期间那个事务一直开着（idle in transaction），而发布
一次视频是分钟级。这是本项目踩过的坑（"临时脚本泄漏连接把池饿死"），所以
两条自律：锁的作用域**只包住浏览器调用那一段**，不包住素材解析、参数校验
这些能提前做的事；并发上限由浏览器容器本身兜底（一次能起几个 context 就
只会有几把锁），不靠这里放大。

取不到锁 = 有另一个会话正在跑
=============================
不无限等（§7.2「所有轮询必须有上界」）。每轮开一个**独立的短事务**去 try
（同一个事务里重试是无意义的：那个事务的连接已经被钉住，锁状态不会变），
轮次用尽就把结论交回调用方 —— 由它记成该账号本次失败并继续下一个账号，
而不是把整批拖住。
"""

from __future__ import annotations

import asyncio
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger
from sqlalchemy import func, select

# advisory lock 的键空间是全库共享的一对 int4。用固定 namespace 占住高位，
# 避免与将来任何别的 advisory lock 用法撞键 —— 撞了不会报错，只会莫名其妙
# 地互相阻塞，是最难查的一类问题。
LOCK_NAMESPACE = 0x5E55  # "SESS"

DEFAULT_LOCK_ATTEMPTS = 10
DEFAULT_LOCK_RETRY_SECONDS = 3.0


def account_lock_key(account_id: int | str) -> int:
    """账号 id → int4 键（``pg_try_advisory_xact_lock`` 的第二个参数）。

    账号 id 是 Snowflake BIGINT，塞不进 int4，所以取 CRC32 再压到有符号
    32 位。**哈希碰撞是可接受的**：两个不同账号极小概率共用一把锁，后果只是
    多串行一次（正确性不受损）；反过来"两个 context 同时开在一个账号上"才是
    真正会烧掉会话的错误。宁可过度串行。
    """
    digest = zlib.crc32(str(account_id).encode("utf-8")) & 0xFFFFFFFF
    # PG 的 int4 是有符号的；> 2^31-1 要绕回负数区，否则绑参数会溢出报错。
    return digest - 0x100000000 if digest > 0x7FFFFFFF else digest


@asynccontextmanager
async def account_session_lock(
    account_id: int | str,
    *,
    attempts: int = DEFAULT_LOCK_ATTEMPTS,
    retry_seconds: float = DEFAULT_LOCK_RETRY_SECONDS,
) -> AsyncIterator[bool]:
    """持有该账号的浏览器会话独占锁。

    ``yield True`` = 拿到了，块内可以安全开 context；``yield False`` = 轮次
    用尽仍被别人占着，调用方**必须**自己处理（不要当作拿到了继续跑）。锁在
    退出块时随事务结束自动释放，异常路径同样。

    等待上界 = ``attempts × retry_seconds``（默认 30s）。发布本身是分钟级，
    所以这个等待相对代价很小，但它必须有界：无界等待会让一个卡住的会话把
    整批任务连坐。
    """
    from app.db.scope import system_session

    key = account_lock_key(account_id)
    for attempt in range(1, attempts + 1):
        # 每轮一个独立短事务：try 失败就立刻结束事务、把连接还给池，等下一轮。
        async with system_session(
            reason=f"session channel: serialize browser use of account {account_id}"
        ) as session:
            acquired = bool(
                (
                    await session.execute(
                        select(func.pg_try_advisory_xact_lock(LOCK_NAMESPACE, key))
                    )
                ).scalar()
            )
            if acquired:
                logger.info(
                    f"[session.lock] account={account_id} acquired "
                    f"(attempt {attempt}/{attempts})"
                )
                # 事务保持打开直到调用方的块结束 —— 这正是锁的持有期。
                yield True
                return
        if attempt < attempts:
            await asyncio.sleep(retry_seconds)

    logger.warning(
        f"[session.lock] account={account_id} busy — gave up after "
        f"{attempts} attempts ({attempts * retry_seconds:.0f}s)"
    )
    yield False


__all__ = [
    "DEFAULT_LOCK_ATTEMPTS",
    "DEFAULT_LOCK_RETRY_SECONDS",
    "LOCK_NAMESPACE",
    "account_lock_key",
    "account_session_lock",
]
