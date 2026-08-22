"""music_charts_sweep —— 榜单缓存的定时刷新。

它存在的理由，一句话
====================
用户要的是「点开就看到榜单」。而榜单**只能由抖音自己的页面取到**：那两个接口
（``media/music/category`` / ``media/music/list``）的签名绑定整串 query，实测
（2026-08-19 重放阶梯）**改任何一个参数都返回 status_code=8**，连只去掉
``cursor`` 都死；而承载那个页面的图文编辑器**必须先上传素材才存在**（不传素材
的那一轮 URL 停在 ``/upload``、「选择音乐」0 匹配）。

于是"取一次榜单"= 开一个浏览器 + 传一个文件 ≈ 2 分钟，并在账号上留一条草稿。
那个代价不能挂在"用户点开面板"那一下 —— 所以由这里在后台付掉，面板只读缓存。
**这个模块不是一个功能，它是让"点开就看到"成立的那件事。**

⚠️ 订阅制，不是全量扫
=====================
只刷新**已经有榜单行**的账号（``list_stale_accounts`` 的语义）。一个从没被人
打开过配乐面板的账号，永远不会被这里碰到 —— 零草稿。第一次手动「Read charts」
就是那个账号的订阅动作。

这条不是省资源，是**默认值的道德**：一个每天在你从不用来配乐的账号上留一条草稿
的后台任务，是在替你做一个你没同意过的决定。

节奏（刻意的慢）
================
* ``MUSIC_CHARTS_CRON`` —— 1 小时一 tick
* ``MAX_PER_TICK`` —— 每 tick **1 个账号**。一次采集约 2 分钟且要独占该账号的
  浏览器会话；三个订阅账号在它们过期后三小时内轮完，完全够用
* ``TTL_HOURS`` —— 单账号 24 小时内不重复采。榜单是天级变化的，更频繁只是多
  付草稿

三条边界
========
1. **模块开关同源。** 关掉 distribution 的人的意思是"别再碰这些平台账号了"，
   而这里恰恰是不需要用户点任何按钮就会去登平台的路径之一，必须服从同一个
   fail-closed 开关。
2. **不建 task_tracking 行**（路线 C 规则 5），与 ``session_health_check`` /
   ``publish_issue_mirror`` 同口径：这是 scheduled housekeeping。
   ⚠️ 已知缺口：它每天会在订阅账号上留一条草稿，而任务中心看不到这件事。面板
   的文案说了手动刷新的代价，自动那条目前只在这里说。
3. **失败 raise，不 return failed dict**（路线 C 规则 4）。账号级失败在
   ``harvest_account_charts`` 里就已经收敛成类型化信封了，到不了这一层；
   扫描/探针自己抛出去就该让 DBOS 记 ERROR。

凭证边界（spec §7.6）
=====================
step 的入参/返回值只有 account id、platform、结论字符串与计数。会话明文由
``harvest_account_charts`` 在它自己那一帧里取用并 pop —— DBOS 会把 step 的
input/output 落库，把凭证放进去等于给它做一份不受 secret_box 管的副本。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from dbos import DBOS
from loguru import logger

#: 1 小时一 tick。是常量而非 env —— cron 写错会在 worker 启动时炸掉整个
#: scheduled bundle，不值得为可调性冒这个险（与 session_health_check 同理）。
MUSIC_CHARTS_CRON = "17 * * * *"


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _ttl_hours() -> int:
    from app.services.distribution.music_charts import DEFAULT_TTL_HOURS

    return _env_int("MUSIC_CHARTS_TTL_HOURS", DEFAULT_TTL_HOURS)


def _max_per_tick() -> int:
    return _env_int("MUSIC_CHARTS_MAX_PER_TICK", 1)


VERDICT_STORED = "stored"  # 采到并落库了
VERDICT_NOTHING = "nothing"  # 跑完了但一个榜都没读到（缓存原样保留）
VERDICT_BUSY = "busy"  # 账号正被发布占用，下一轮再说
VERDICT_ERROR = "error"  # 该账号意外异常（已记日志，不影响其他账号）


@DBOS.step()
async def _scan_stale_accounts() -> dict[str, Any]:
    """这一轮该采的账号，**外加真实的过期总数**。

    ``{"picked": [{account_id, platform}, ...], "due_total": N}``

    ⚠️ 两个数缺一不可。每 tick 只取 1 个是刻意的（一次采集约 2 分钟、独占该
    账号的浏览器会话、并留一条草稿），所以 ``picked`` 永远是 0 或 1 —— **它
    无法回答"队列到底在不在往前走"**：两个账号在等和两百个在等，它给出的数字
    一模一样，而每一行日志都很健康。

    这正是「上限静默截断覆盖面」那一类：每小时 1 个 = 全系统上限 24 个/天，
    超过就永远排不完，而且不会有任何报错。多问一次总数，是让这件事可被发现的
    唯一办法。
    """
    from app.db.scope import system_request_scope
    from app.repositories.music_charts_repository import MusicChartsRepository

    limit = _max_per_tick()
    repo = MusicChartsRepository()
    async with system_request_scope(
        "music chart sweep: find accounts whose cached charts went stale"
    ):
        rows = await repo.list_stale_accounts(ttl_hours=_ttl_hours(), limit=limit)
        due_total = await repo.count_stale_accounts(ttl_hours=_ttl_hours())
    return {
        "picked": [
            {"account_id": r["account_id"], "platform": r["platform"]} for r in rows
        ],
        "due_total": int(due_total),
    }


def describe_backlog(due_total: int, picked: int, *, cron_per_day: int = 24) -> str:
    """积压说明，或空串。纯函数。

    空串 = 这一轮把该采的都采了。**不是"没查"** —— 调用方在两种情况下都会调
    到它，所以空串是一个结论，不是缺省值。

    带上"排空要多少天"而不是只报一个积压数：24 个账号积压意味着一天，240 个
    意味着十天 —— 后者等于这个功能对大多数账号已经不成立了，而两者的积压数
    长得一样。
    """
    if due_total <= picked:
        return ""
    waiting = due_total - picked
    days = (due_total + cron_per_day - 1) // max(1, cron_per_day)
    return (
        f"{waiting} account(s) still due after this tick "
        f"(due={due_total}, taken={picked}, ceiling={cron_per_day}/day, "
        f"~{days}d to drain)"
    )


@DBOS.step()
async def _music_module_enabled() -> bool:
    # ⚠️ 导入的是 `is_module_enabled` —— 这里曾经写成 `is_music_module_enabled`，
    # 而那个函数不存在。成因：给 step 加 `_music_` 前缀那次用了全量字符串替换，
    # 而 `is_module_enabled` 恰好含有 `_module_enabled` 这个子串，导入名被一起
    # 改掉了。生产上 22 次 ERROR，这个定时任务从上线起一次都没成功过。
    #
    # 测试没抓住，是因为它们全部 monkeypatch 掉了这个函数 —— **函数体一次都没
    # 被执行过**。所以现在有一条测试真的调它，见
    # `test_the_module_gate_actually_resolves_its_import`。
    from app.services.distribution.module_config import is_module_enabled

    return await is_module_enabled()


@DBOS.step()
async def _music_browser_is_healthy() -> bool:
    """浏览器容器是否可用，在开第一个 context 之前问一次。

    容器宕机时这一整轮无论如何都采不到，逐个试只是白等一堆超时 —— 而每一次
    超时都可能在账号上留下一条半成品草稿。
    """
    from app.services.distribution.browser_client import BrowserClient

    health = await BrowserClient().health()
    if not health.ok:
        logger.warning(
            f"[music.sweep] browser service unhealthy "
            f"(error_kind={health.error_kind}) — tick skipped"
        )
    return bool(health.ok)


@DBOS.step()
async def _harvest_one(account_id: str) -> str:
    """采一个账号。返回 ``VERDICT_*``，**从不抛**。

    ``harvest_account_charts`` 本身是全类型化的（账号不存在 / 会话不可用 /
    账号忙 / 一个榜都没读到），所以这里只把它的信封翻译成计数键。
    """
    from app.core.config import settings
    from app.services.distribution.music_charts import (
        REASON_ACCOUNT_BUSY,
        harvest_account_charts,
    )

    base_url = getattr(settings, "INTERNAL_BASE_URL", "") or "http://nous-backend:8080"
    try:
        out: dict[str, Any] = await harvest_account_charts(
            int(account_id), base_url=base_url
        )
    except Exception as exc:  # noqa: BLE001 - 一个账号的意外不该中止整轮
        logger.warning(
            f"[music.sweep] account={account_id} raised: {type(exc).__name__}"
        )
        return VERDICT_ERROR

    if (out.get("detail") or {}).get("reason") == REASON_ACCOUNT_BUSY:
        # 不是错误：账号正在发布或巡检。下一轮再来，而且**不推进任何时间戳**，
        # 所以它仍然排在队首。
        return VERDICT_BUSY
    stored = int((out.get("stored") or {}).get("stored") or 0)
    if stored > 0:
        logger.info(f"[music.sweep] account={account_id} stored={stored} charts")
        return VERDICT_STORED
    # 跑完了却一个榜都没读到。缓存**原样保留**（repository 的 keep 规则），
    # 所以这既不是数据丢失也不是成功。
    logger.warning(
        f"[music.sweep] account={account_id} read no chart: "
        f"{str(out.get('message'))[:160]}"
    )
    return VERDICT_NOTHING


@DBOS.scheduled(MUSIC_CHARTS_CRON)
@DBOS.workflow()
async def music_charts_sweep_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> dict[str, int]:
    """一轮刷新。返回各结论的计数（scheduled housekeeping，不建 task_tracking）。

    **不吞异常**：扫描或探针本身抛出去就是 workflow 失败，DBOS 记 ERROR
    （返回 failed dict 会被当成 SUCCESS）。
    """
    counts: dict[str, int] = {"due": 0, "harvested": 0}
    if not await _music_module_enabled():
        return counts

    scan = await _scan_stale_accounts()
    due = list(scan.get("picked") or [])
    # `due` 是**真实过期总数**，不是这一轮取了几个。两者在只取 1 个的设计下
    # 几乎总是不同，而报小的那个会让"队列排不完"永远看不见。
    counts["due"] = int(scan.get("due_total") or 0)
    counts["taken"] = len(due)
    backlog = describe_backlog(counts["due"], counts["taken"])
    if backlog:
        # 上限截断了覆盖面就必须说出来。这条 WARNING 进 application_logs，
        # 是"这个功能对多少账号已经不成立了"唯一可查的地方。
        logger.warning(f"[music.sweep] backlog: {backlog}")
    if not due:
        return counts

    if not await _music_browser_is_healthy():
        # 一个账号都不碰，也不刷新任何时间戳 —— 这一轮等于没发生。
        counts["skipped_unhealthy"] = len(due)
        return counts

    for account in due:
        verdict = await _harvest_one(account["account_id"])
        counts[verdict] = counts.get(verdict, 0) + 1
        counts["harvested"] += 1

    logger.info(f"[music.sweep] tick: {counts}")
    return counts


__all__ = [
    "MUSIC_CHARTS_CRON",
    "describe_backlog",
    "VERDICT_BUSY",
    "VERDICT_ERROR",
    "VERDICT_NOTHING",
    "VERDICT_STORED",
    "music_charts_sweep_workflow",
]
