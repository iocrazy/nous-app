"""publish_readback —— 到点之后，去平台**确认**作品真的上线了 (gap-closure P1-3)。

它补的是什么洞
==============
会话通道发布完，``publish_task_accounts.published_url`` 一直是 NULL —— 不是
忘了写，是**根本拿不到**：抖音发布后的跳转不带作品 id，而"取管理页第一条"
对任何有定时稿或并发发布的账号都会张冠李戴（原话见
``browser/app/platforms/douyin_publish.py::_drive``）。

P1-2 于是让定时批次的待办**按时间**放行：``scheduled_at`` 一过就判 done。
那是把假设当结论。这中间平台可以审核、可以拒稿、可以取消定时，用户也可以
删稿 —— 每一种都会被记成"发完了"。

本模块把"按时间放行"换成"回读确认后放行"：到点后开一次浏览器去创作者中心
找那条作品，看平台现在怎么说它。

三种答案，不是两种
==================
这是整条链最容易写错的地方，所以枚举写在这里而不是散在调用点：

  * ``VERIFIED``  —— 平台上真的看得到 → 回填 URL / item id → 待办可以 done
  * ``NOT_LIVE``  —— **我们看过了**，它没上线（审核中 / 被拒 / 找不到）
                     → 待办转 blocked，带类型化原因
  * ``PENDING``   —— 这一轮**没问出来**（容器宕机 / 会话读不出 / 页面读不动）
                     → 什么都不判，下一轮再来

把后两者合并是很自然的手滑（都不是成功），而代价极不对称：一次容器宕机会
把一整批好好的作品挂成事故待办，同时平台真拒稿时又跟"我们这边挂了"长得
一模一样。判据是 ``VerifyResult.conclusive``，不是 ``success`` 为假。

放弃也必须看得见
================
重试有上限（``MAX_ATTEMPTS``）。用完仍然没有结论 → ``ABANDONED``，而
``ABANDONED`` 和 ``NOT_LIVE`` 一样让待办转 blocked。这条是刻意的：
"我们没能确认它上线了"不允许渲染成 done。静默挂着更不行（CLAUDE.md
「触发路径必须类型化失败回显」）。

节奏（刻意的慢）
================
每次回读 == 起一次真浏览器、带着该账号 cookie 登一次平台。三道闸叠起来，
理由与 ``session_health_check`` 完全同源：

  * ``READBACK_CRON``      —— 10 分钟一 tick
  * ``MIN_RETRY_INTERVAL_S`` —— 单行两次回读至少隔 30 分钟
  * ``READBACK_BATCH``     —— 每 tick 至多 5 行

外加 ``GO_LIVE_GRACE_S``：到点那一刻平台自己还在处理，立刻去查必然看到
"审核中"，白白烧掉一次重试预算。

路线 C 边界
===========
1. **不建 task_tracking 行**（规则 5）—— scheduled housekeeping，对齐
   ``session_health_check`` / ``publish_issue_mirror``。
2. **不碰 phase 列**（规则 2）。写的全是业务列：``verify_*`` +
   ``published_url`` / ``platform_item_id``。
3. **不改 ``publish_task_accounts.status``**。上传那一步是真的成功了，把
   ``not_live`` 折进 ``status='failed'`` 会改写历史，还会让这一批看起来
   可重试 —— 而重试意味着把一条平台已经拒掉的视频再传一遍。
4. **失败 raise，不 return failed dict**（规则 4）。单行失败收敛成 verdict，
   workflow 级意外直接抛。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from dbos import DBOS
from loguru import logger

# 10 分钟一轮。常量而非 env —— cron 写错会在 worker 启动时炸掉整个 scheduled
# bundle（同 SESSION_CHECK_CRON 的理由）。
READBACK_CRON = "*/10 * * * *"


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


# 单行两次回读的最小间隔。
MIN_RETRY_INTERVAL_S = _env_int("PUBLISH_READBACK_MIN_INTERVAL_SECONDS", 30 * 60)

# 到点后先等一会儿再查：平台在 go-live 那一刻自己还在处理，立刻查必然看到
# "审核中"，等于白烧一次重试。
GO_LIVE_GRACE_S = _env_int("PUBLISH_READBACK_GRACE_SECONDS", 10 * 60)

# 重试上限。用完 → ABANDONED（待办转 blocked），**不是**悄悄放过。
# 5 次 × 30 分钟间隔 ≈ 覆盖两个半小时的平台审核窗口。
MAX_ATTEMPTS = _env_int("PUBLISH_READBACK_MAX_ATTEMPTS", 5)


# ── verdict 词表（与 mig 418 的 verify_state 一一对应） ──────────────
VERIFY_PENDING = "pending"  # 没问出来，下一轮再来
VERIFY_VERIFIED = "verified"  # 平台上看得到
VERIFY_NOT_LIVE = "not_live"  # 我们看过了，它没上线
VERIFY_NOT_SUPPORTED = "not_supported"  # 这个平台没有回读实现
VERIFY_ABANDONED = "abandoned"  # 重试用尽仍无结论

#: 到达即不再回读的 verdict。``pending`` 不在其中 —— 那正是"还要再来"的意思。
TERMINAL_VERIFY_STATES = frozenset(
    {VERIFY_VERIFIED, VERIFY_NOT_LIVE, VERIFY_NOT_SUPPORTED, VERIFY_ABANDONED}
)

#: 让镜像把待办转 blocked 的 verdict。``not_supported`` **不在**其中：平台
#: 没有回读能力是我们的覆盖缺口，不是这条作品出了问题，拿它挡住用户的待办
#: 等于因为自己没实现而惩罚用户。它只是让门放行（回到 P1-2 的按时间语义）。
BLOCKING_VERIFY_STATES = frozenset({VERIFY_NOT_LIVE, VERIFY_ABANDONED})

# 每行结论 —— 也是返回给 DBOS 的计数键。
VERDICT_MISSING = "missing"  # 扫到之后行/账号没了
VERDICT_BUSY = "busy"  # 账号正被发布占用，下一轮再说
VERDICT_ERROR = "error"  # 该行意外异常（已记日志，不影响其他行）


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(val))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def go_live_at(row: dict) -> Optional[datetime]:
    """这条作品**最早**可能出现在平台上的时刻。纯函数。

    定时批次是 ``scheduled_at``；立即发布是 ``published_at``（我们发完的那一
    刻）。两者都没有就返回 None，调用方按"可以查了"处理 —— 数据缺失不该让
    一行永远卡在队列里没人管。
    """
    return _parse_ts(row.get("scheduled_at")) or _parse_ts(row.get("published_at"))


def select_due(
    candidates: Iterable[dict],
    *,
    now: datetime,
    min_interval_s: int = MIN_RETRY_INTERVAL_S,
    grace_s: int = GO_LIVE_GRACE_S,
    max_attempts: int = MAX_ATTEMPTS,
    max_rows: int = 5,
) -> list[dict]:
    """候选里这一轮真正该回读的那些。纯函数。

    三道过滤，各自拦一类浪费：

    1. **重试预算已用尽** —— 该行等着被 ``abandon`` 收尾，不是等着再查一次。
       仍然扫得到它是刻意的：收尾也要有人做（见 ``select_abandoned``）。
    2. **还没到 go-live + grace** —— 平台那一刻自己还在处理。
    3. **距上次回读不满 ``min_interval_s``** —— 风控克制。

    不 ``break``（尽管 repo 按 ``verify_checked_at ASC`` 排序）：靠调用方的
    排序提前收尾，等于把 repo 的 ORDER BY 变成本函数正确性的隐式前提。同
    ``session_health_check.select_due``。
    """
    out: list[dict] = []
    for row in candidates:
        if len(out) >= max_rows:
            break
        if int(row.get("verify_attempts") or 0) >= max_attempts:
            continue
        live_at = go_live_at(row)
        if live_at is not None and (now - live_at).total_seconds() < grace_s:
            continue
        last = _parse_ts(row.get("verify_checked_at"))
        if last is not None and (now - last).total_seconds() < min_interval_s:
            continue
        out.append(row)
    return out


def select_abandoned(
    candidates: Iterable[dict], *, max_attempts: int = MAX_ATTEMPTS
) -> list[dict]:
    """预算用尽、仍然停在 ``pending`` 的行。纯函数。

    这些必须被主动收尾成 ``abandoned``，否则它们会**永远停在 pending** ——
    而 pending 让镜像把待办一直挂着不动。那正是「不许静默挂着」禁止的形状：
    没结论、没报错、没人知道。
    """
    return [
        row
        for row in candidates
        if int(row.get("verify_attempts") or 0) >= max_attempts
        and (row.get("verify_state") or VERIFY_PENDING) not in TERMINAL_VERIFY_STATES
    ]


def verdict_for(
    result: dict,
    *,
    attempts_before: int,
    max_attempts: int = MAX_ATTEMPTS,
) -> tuple[str, Optional[str]]:
    """一次回读的 §7.8 信封 → ``(verify_state, verify_detail)``。纯函数。

    ``result`` 是 ``VerifyResult.result.to_dict()``。判断顺序就是设计：

    1. ``published``     → VERIFIED
    2. ``not_published`` → NOT_LIVE（``reason=not_supported`` 单独分流）
    3. 其余一律**没有结论**：这次之后还有预算就 PENDING，没有就 ABANDONED。

    第 3 步用 ``attempts_before + 1``（把**本次**算进去）而不是
    ``attempts_before``：否则最后一次尝试会写 PENDING，然后下一轮才发现超额，
    多烧一轮才收尾 —— 而且中间那段时间待办的状态是"还在查"，与事实不符。
    """
    status = result.get("status")
    detail = result.get("detail") or {}
    reason = detail.get("reason")
    message = result.get("message") or status or ""

    if status == "published":
        return VERIFY_VERIFIED, None
    if status == "not_published":
        if reason == "not_supported":
            return (
                VERIFY_NOT_SUPPORTED,
                "[not_supported] this platform has no publish read-back; "
                "go-live could not be confirmed",
            )
        return VERIFY_NOT_LIVE, f"[{reason or 'not_live'}] {message}"

    spent = attempts_before + 1
    cause = detail.get("error_kind") or reason or status or "unknown"
    if spent >= max_attempts:
        return (
            VERIFY_ABANDONED,
            f"[verification_abandoned] gave up after {spent} attempt(s); "
            f"last failure: [{cause}] {message}",
        )
    return VERIFY_PENDING, f"[{cause}] {message}"


# ── DBOS steps ──────────────────────────────────────────────────────


@DBOS.step()
async def _readback_module_enabled() -> bool:
    """Distribution 模块的 ACCESS 开关（admin 控制、fail-closed）。

    与巡检同理：关掉模块的人的意思是"别再碰这些平台账号了"，而回读是另一条
    不需要用户点任何按钮就会去登平台的路径。
    """
    from app.services.distribution.module_config import is_module_enabled

    return await is_module_enabled()


@DBOS.step()
async def _readback_browser_is_healthy() -> bool:
    """浏览器容器可用吗（``GET /healthz``，探真信号 ``browser_ready``）。

    在开第一个 context 之前问一次。容器宕机时这一整轮无论如何都问不出结论，
    逐行去试只会白等一堆超时 —— **而且每次超时都会烧掉一格重试预算**，几轮
    宕机就能把一批本来好好的作品推到 ABANDONED、挂成事故待办。所以全局失败
    在这里一次性挡掉。
    """
    from app.services.distribution.browser_client import BrowserClient

    health = await BrowserClient().health()
    if not health.ok:
        logger.warning(
            f"[publish.readback] browser service unhealthy "
            f"(error_kind={health.error_kind}) — tick skipped"
        )
    return bool(health.ok)


@DBOS.step()
async def _readback_scan_due() -> dict[str, list[dict[str, str]]]:
    """这一轮要回读的行 + 要收尾的行。

    只返回标量字段（id / account_id / platform / title）。step 的返回值会被
    DBOS 落库（spec §7.6），所以带密文的账号行绝不能原样传出去 —— 真正要用
    的会话由 ``get_with_session`` 在回读那一刻现取。
    """
    from app.db.scope import system_request_scope
    from app.repositories.publish_tasks_repository import (
        READBACK_BATCH,
        PublishTasksRepository,
    )

    async with system_request_scope(
        "publish read-back sweep: scan settled session publishes across tenants"
    ):
        rows = await PublishTasksRepository().list_readback_due(limit=READBACK_BATCH)

    now = datetime.now(timezone.utc)
    due = select_due(rows, now=now, max_rows=READBACK_BATCH)
    abandoned = select_abandoned(rows)
    return {
        "due": [
            {
                "id": str(r["id"]),
                "account_id": str(r["account_id"]),
                "platform": str(r.get("platform") or "douyin"),
                "title": str(r.get("title") or ""),
                "attempts": str(int(r.get("verify_attempts") or 0)),
            }
            for r in due
        ],
        "abandon": [{"id": str(r["id"])} for r in abandoned],
    }


async def _verify_one(
    row_id: str, account_id: str, platform: str, title: str, attempts_before: int
) -> str:
    """回读一行并写回结论。返回 ``verify_state`` 或 ``VERDICT_*``。**从不抛**。

    明文 ``session_state`` 只在这一帧的栈上存在，``finally`` 立刻 pop（§7.6）。
    """
    from app.repositories.publish_tasks_repository import PublishTasksRepository
    from app.repositories.social_accounts_repository import (
        SESSION_STATE_DECRYPT_FAILED,
        SocialAccountsRepository,
    )
    from app.services.distribution.registry import get_session_adapter
    from app.services.distribution.session_lock import account_session_lock

    tasks_repo = PublishTasksRepository()
    accounts_repo = SocialAccountsRepository()
    acct: Optional[dict] = None
    try:
        if not title.strip():
            # 标题是回读唯一的抓手。没有它就永远查不了这一行 —— 说清楚并终结，
            # 别让它在队列里无限循环占着每轮的 5 个名额。
            await tasks_repo.record_verification(
                int(row_id),
                state=VERIFY_ABANDONED,
                detail="[no_title] the publish row has no title to look for; "
                "go-live cannot be confirmed",
            )
            return VERIFY_ABANDONED

        acct = await accounts_repo.get_with_session(int(account_id))
        if not acct:
            await tasks_repo.record_verification(
                int(row_id),
                state=VERIFY_ABANDONED,
                detail="[account_missing] the bound account is gone; go-live "
                "cannot be confirmed",
            )
            return VERDICT_MISSING
        if acct.get(SESSION_STATE_DECRYPT_FAILED):
            # 密文在、但打不开：我们的密钥错了，平台会话八成好得很。基建失败
            # → 不下结论，烧一格预算后下一轮再来。
            result = {
                "status": "failed",
                "message": "session_state could not be decrypted",
                "detail": {"error_kind": "decrypt_failed"},
            }
            state, detail = verdict_for(result, attempts_before=attempts_before)
            await tasks_repo.record_verification(
                int(row_id), state=state, detail=detail
            )
            return state

        # attempts=1：抢不到锁说明该账号正在发布 / 巡检，回读等下一轮就行。
        # 同一账号两个活动 context 会互相踢下线（spec §7.5）。
        async with account_session_lock(account_id, attempts=1) as acquired:
            if not acquired:
                logger.info(f"[publish.readback] account={account_id} busy — skipped")
                return VERDICT_BUSY
            verified = await get_session_adapter(platform).verify_publish(acct, title)
    except Exception as exc:  # noqa: BLE001 —— 单行失败不该炸掉整批
        logger.warning(f"[publish.readback] row={row_id} errored: {exc!r}")
        return VERDICT_ERROR
    finally:
        if acct is not None:
            acct.pop("session_state", None)

    try:
        # 会话滑动续期：回读也是一次真实的已认证页面加载，平台照样下发新
        # cookie。不写回等于让这次额外的浏览器任务**净消耗**会话寿命。
        if verified.updated_storage_state:
            import json

            await accounts_repo.update_session_state(
                int(account_id),
                json.dumps(verified.updated_storage_state, ensure_ascii=False),
            )

        state, detail = verdict_for(
            verified.result.to_dict(), attempts_before=attempts_before
        )
        await tasks_repo.record_verification(
            int(row_id),
            state=state,
            detail=detail,
            published_url=verified.published_url,
            platform_item_id=verified.platform_item_id,
        )
        if state == VERIFY_NOT_LIVE:
            logger.warning(
                f"[publish.readback] row={row_id} NOT live on the platform "
                f"(reason={verified.reason!r}) — the work item will be blocked"
            )
        elif state == VERIFY_ABANDONED:
            logger.warning(
                f"[publish.readback] row={row_id} giving up after "
                f"{attempts_before + 1} attempts — the work item will be blocked"
            )
        return state
    except Exception as exc:  # noqa: BLE001 —— 写回失败同样不该炸掉整批
        logger.warning(f"[publish.readback] row={row_id} write-back failed: {exc!r}")
        return VERDICT_ERROR


@DBOS.step()
async def _readback_verify_one_step(
    row_id: str, account_id: str, platform: str, title: str, attempts_before: str
) -> str:
    """``_verify_one`` 的 DBOS 包装。

    拆两层的理由同 ``session_health_check._check_account_step``：业务逻辑要能
    在没有 DBOS runtime 的情况下被单测直接调用，而不是只能靠集成测试覆盖。
    """
    from app.db.scope import system_request_scope

    async with system_request_scope(
        f"publish read-back: confirm go-live for publish row {row_id}"
    ):
        return await _verify_one(
            row_id, account_id, platform, title, int(attempts_before or 0)
        )


@DBOS.step()
async def _readback_abandon_step(row_id: str) -> None:
    """把预算用尽却还停在 ``pending`` 的行收尾成 ``abandoned``。

    ``bump_attempts=False``：这不是一次尝试，是给一串失败尝试盖棺。
    """
    from app.db.scope import system_request_scope
    from app.repositories.publish_tasks_repository import PublishTasksRepository

    async with system_request_scope(
        f"publish read-back: abandon verification for publish row {row_id}"
    ):
        await PublishTasksRepository().record_verification(
            int(row_id),
            state=VERIFY_ABANDONED,
            detail=(
                f"[verification_abandoned] no conclusive read-back after "
                f"{MAX_ATTEMPTS} attempts; go-live was never confirmed"
            ),
            bump_attempts=False,
        )


@DBOS.scheduled(READBACK_CRON)
@DBOS.workflow()
async def publish_readback_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> dict[str, int]:
    """一轮回读。返回各结论的计数（scheduled housekeeping，不建 task_tracking）。

    这里**不吞异常**：扫描或探针本身抛出去就是 workflow 失败，DBOS 记 ERROR
    （路线 C 规则 4）。行级失败早在 ``_verify_one`` 里收敛成 verdict 了。
    """
    counts: dict[str, int] = {"due": 0, "checked": 0, "abandoned": 0}
    if not await _readback_module_enabled():
        return counts

    scanned = await _readback_scan_due()
    due = scanned["due"]
    counts["due"] = len(due)

    # 收尾先于回读，且**不受浏览器健康度影响** —— 它是一次纯数据库写，而且
    # 正是容器长期不可用时最该发生的事：让"一直没能确认"变成用户看得见的
    # blocked，而不是无限期停在 pending。
    for row in scanned["abandon"]:
        await _readback_abandon_step(row["id"])
        counts["abandoned"] += 1

    if not due:
        if counts["abandoned"]:
            logger.info(f"[publish.readback] tick: {counts}")
        return counts

    if not await _readback_browser_is_healthy():
        # 容器不可用：一行都不碰，也不烧任何重试预算 —— 这一轮等于没发生。
        counts["skipped_unhealthy"] = len(due)
        return counts

    for row in due:
        verdict = await _readback_verify_one_step(
            row["id"],
            row["account_id"],
            row["platform"],
            row["title"],
            row["attempts"],
        )
        counts[verdict] = counts.get(verdict, 0) + 1
        counts["checked"] += 1

    logger.info(f"[publish.readback] tick: {counts}")
    return counts


__all__ = [
    "BLOCKING_VERIFY_STATES",
    "GO_LIVE_GRACE_S",
    "MAX_ATTEMPTS",
    "MIN_RETRY_INTERVAL_S",
    "READBACK_CRON",
    "TERMINAL_VERIFY_STATES",
    "VERIFY_ABANDONED",
    "VERIFY_NOT_LIVE",
    "VERIFY_NOT_SUPPORTED",
    "VERIFY_PENDING",
    "VERIFY_VERIFIED",
    "go_live_at",
    "publish_readback_workflow",
    "select_abandoned",
    "select_due",
    "verdict_for",
]
