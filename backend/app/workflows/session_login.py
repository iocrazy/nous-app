"""session_login DBOS workflow —— 扫码绑定一个平台账号（会话通道 S2）。

spec: ``docs/superpowers/specs/2026-08-04-distribution-session-channel-design.md``
§4.1（流程）/ §7.2（轮询上界 + heartbeat）/ §7.6（凭证不落盘）/ §7.8（类型化结果）

一次 workflow run = 一次扫码。编排：

    start   → POST   /session/login/start        起保活 context，抓二维码
    poll    → GET    /session/login/{id}/status  有上界地轮询，状态进 metadata
    finish  → GET    /session/login/{id}/state   取 storage_state → 加密入库
    always  → POST   /session/login/{id}/close   释放 context

为什么二维码不能照搬 issues 的 needs_input 模式
===============================================
二维码属于浏览器容器里那个**活着的** context —— workflow 一结束 context 就
销毁，码立即作废。所以这里不挂起、不终止，而是 workflow 全程在线轮询。

路线 C 纪律（CLAUDE.md「任务系统架构纪律」）
==========================================
1. **全程 ``in_progress``，绝不 PATCH ``phase``**。``task_tracking`` 上根本
   没有 ``needs_input`` 这个状态（那是 issues 侧的机制），spec §4.1 初稿写错
   了，其「⚠️ 更正」块已订正。扫码进度只写 ``metadata`` jsonb —— 业务装饰
   字段，纪律 3 明确允许业务代码写。
2. 生命周期只经 ``UnifiedTaskManager``（``start`` / ``fail`` / ``complete``）。
3. **失败一律 raise**，绝不 ``return {"status": "failed"}``（纪律 4）——
   返回 dict 会被 DBOS 判成 SUCCESS，trigger 把任务标 completed，用户看到的
   是"完成了但没有账号"。
4. ``update_progress`` 对同一 task 有 1 write/sec 节流，会**整条丢弃**
   （含 metadata_patch）。二维码丢一次就是白等一轮，所以状态一律走
   ``patch_metadata``（不节流），subtitle 才用 ``update_progress``。

context 释放（唯一必须做对的资源纪律）
====================================
``start_login`` 返回后，浏览器容器里留着一个活的 context。成功、失败、超时、
用户取消四条路径都必须释放，因此 ``close`` 在 ``finally`` 里，且
``close_login`` 自身永不抛异常 —— 清理失败不得盖掉正在传播的真实错误。
浏览器侧的 TTL 自毁是**兜底**（worker 进程被 kill 时才轮到它），不是常规路径。

凭证边界（§7.6）
===============
明文 ``storage_state`` 只在 ``finalize_login_step`` 的函数作用域里存在一瞬：
取出 → ``secret_box.encrypt``（在 repository 里）→ 入库。它**绝不**出现在
step 的返回值里 —— DBOS 会把 step 的输入输出持久化进引擎表，那等于把平台
会话明文写进数据库的另一张表，还是不可改的那种。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from dbos import DBOS
from loguru import logger

from app.services.distribution.browser_client import (
    LOGIN_FAILURE_STATUSES,
    LOGIN_PENDING_STATUSES,
    LoginSnapshot,
    SessionStatus,
)

# ── 轮询参数（§7.2：必须有上界） ────────────────────────────────

# 浏览器侧 login session 的 TTL 是 5 分钟（spec §4.1）。两侧取同一个数：
# 我们先于它放弃，才能保证释放走的是 close 而不是对端的自毁。
LOGIN_TTL_SECONDS = 300.0
# 2s 是 update_progress 节流（1 write/sec）之上的安全区，也是"用户感觉即时"
# 与"别把浏览器容器打成筛子"之间的折中。压到亚秒级没有收益。
POLL_INTERVAL_SECONDS = 3.0
# 双重上界：时间到 **或** 轮次用尽。只有时间上界的话，一个恒返回极快失败的
# 对端会把这个循环变成忙等；只有轮次上界的话，慢响应会让实际时长远超 TTL。
MAX_POLLS = int(LOGIN_TTL_SECONDS / POLL_INTERVAL_SECONDS) + 20
# 容器重启 / 网络抖动期间轮询会连续拿到基建失败。此时用户手机上那次扫码
# 可能**已经成功**，立刻放弃等于白扫，所以容忍几轮；连续超过阈值才认输。
MAX_CONSECUTIVE_INFRA_FAILURES = 3

TASK_TYPE = "session_login"  # ≤20 chars — task_tracking.task_type 是 VARCHAR(20)

# 各状态的 UI 文案（英文，CLAUDE.md UI 语言规范）。浏览器侧给了 message 就用
# 它的，这里只是兜底 —— 但兜底必须存在：silent 空字符串等于没有回显。
_STATUS_SUBTITLE: dict[str, str] = {
    SessionStatus.WAITING_SCAN.value: "Scan the QR code",
    SessionStatus.SCANNED.value: "Confirm on your phone",
    SessionStatus.QRCODE_EXPIRED.value: "QR code refreshed",
    SessionStatus.SMS_REQUIRED.value: "Enter the SMS code",
    SessionStatus.SUCCESS.value: "Signed in",
    SessionStatus.TIMEOUT.value: "Login timed out",
    SessionStatus.PROXY_FAILED.value: "Proxy unreachable",
    SessionStatus.FAILED.value: "Login failed",
}


class SessionLoginError(RuntimeError):
    """登录编排失败 —— 带上通道级 status，供上层写进 metadata / 日志。

    ``status`` 有默认值不是随手写的：DBOS 会 pickle step 抛出的异常，而
    ``Exception`` 的默认 ``__reduce__`` 只还原 ``args`` 并按 ``cls(*args)``
    重建。必填的 keyword-only 参数会让**反序列化**炸在 DBOS 内部，把一个清晰
    的登录失败变成一个看不懂的引擎错误。
    """

    def __init__(self, message: str, *, status: str = SessionStatus.FAILED.value):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class LoginLoopOutcome:
    """轮询循环的出口。``outcome`` 是编排结论，``status`` 是通道级枚举值。"""

    outcome: str  # "success" | "cancelled" | "timeout" | "failed"
    status: str
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == "success"


# ── metadata 写入 ─────────────────────────────────────────────


class LoginMetadataWriter:
    """把登录快照写进 ``task_tracking.metadata.login``（前端契约，spec §4.1）。

    有状态，因为**二维码必须粘住**：``/status`` 只在刷新时带回新码，其余轮次
    是 null。而 ``patch_metadata`` 的合并是顶层浅合并 —— 写 ``login`` 会整体
    替换。不记住上一张码，用户就会在第二轮轮询时看着二维码凭空消失。
    """

    def __init__(self, manager: Any, workflow_id: str, platform: str) -> None:
        self._manager = manager
        self._workflow_id = workflow_id
        self._platform = platform
        self._qrcode: Optional[str] = None
        self._expires_at: Optional[str] = None

    def build(self, snapshot: LoginSnapshot) -> dict[str, Any]:
        if snapshot.qrcode_data_url:
            self._qrcode = snapshot.qrcode_data_url
            self._expires_at = snapshot.expires_at
        elif snapshot.expires_at:
            self._expires_at = snapshot.expires_at
        return {
            "platform": self._platform,
            "status": snapshot.status,
            "qrcode_data_url": self._qrcode,
            "expires_at": self._expires_at,
            "message": snapshot.message
            or _STATUS_SUBTITLE.get(snapshot.status, snapshot.status),
        }

    async def publish(self, snapshot: LoginSnapshot) -> None:
        """写 metadata（权威、不节流）+ 尽力更新 subtitle（可被节流丢弃）。

        两者分开正是因为节流：subtitle 只是任务列表里的一行字，丢了无所谓；
        二维码丢了用户就卡住了。
        """
        login = self.build(snapshot)
        await self._manager.patch_metadata(self._workflow_id, {"login": login})
        try:
            await self._manager.update_progress(
                self._workflow_id,
                _progress_for(snapshot.status),
                subtitle=_STATUS_SUBTITLE.get(snapshot.status, "Signing in"),
            )
        except Exception as exc:  # noqa: BLE001 — subtitle 是装饰，不值得中断登录
            logger.warning(f"[session_login] subtitle update failed: {exc}")


def _progress_for(status: str) -> int:
    """进度条只是给用户的"事情在动"的感觉，不是精确度量。"""
    return {
        SessionStatus.WAITING_SCAN.value: 20,
        SessionStatus.QRCODE_EXPIRED.value: 20,
        SessionStatus.SCANNED.value: 60,
        SessionStatus.SMS_REQUIRED.value: 70,
        SessionStatus.SUCCESS.value: 90,
    }.get(status, 10)


# ── 取消检测 ─────────────────────────────────────────────────


async def is_task_terminal(workflow_id: str) -> bool:
    """``task_tracking`` 已经进终态？（用户取消 / 被 sweeper 判死）

    取消端点走的是 ``manager.cancel()``（写 phase=cancelled），而这个 workflow
    正在一个长循环里 —— 不主动看一眼，它会一直轮询到 TTL 用完，浏览器 context
    也就多占 5 分钟。读的是 ``task_tracking``（路线 C 纪律 1：UI 唯一数据源），
    不是 ``dbos.workflow_status``。

    读失败返 False：宁可多轮询一轮，也不要因为一次 PG 抖动就中断用户的扫码。
    """
    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import TaskTracking

        async with read_scope() as session:
            phase = (
                await session.execute(
                    select(TaskTracking.phase)
                    .where(TaskTracking.dbos_workflow_id == workflow_id)
                    .limit(1)
                )
            ).scalar()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[session_login] phase read failed for {workflow_id}: {exc}")
        return False
    return phase in ("cancelled", "failed", "completed", "lost")


# ── 轮询循环（纯编排，可单测） ────────────────────────────────


async def drive_login_loop(
    *,
    client: Any,
    login_session_id: str,
    publish: Callable[[LoginSnapshot], Awaitable[None]],
    is_cancelled: Callable[[], Awaitable[bool]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    ttl_seconds: float = LOGIN_TTL_SECONDS,
    max_polls: int = MAX_POLLS,
    max_infra_failures: int = MAX_CONSECUTIVE_INFRA_FAILURES,
) -> LoginLoopOutcome:
    """轮询登录状态直到出结论。**永不抛异常** —— 结论用 outcome 表达，raise
    的决定留给 workflow 体（那里才知道该 fail 哪个 task）。

    从 @DBOS.step 里抽出来，所有外部依赖（浏览器、时钟、DB）都是入参，
    因此各状态分支 / 二维码刷新 / 超时 / 取消 / 基建抖动全部可单测。
    """
    deadline = monotonic() + ttl_seconds
    consecutive_infra = 0
    last_key: Optional[tuple[str, Optional[str]]] = None

    for _ in range(max_polls):
        if await is_cancelled():
            return LoginLoopOutcome(
                "cancelled", SessionStatus.FAILED.value, "cancelled"
            )

        snapshot = await client.get_login_status(login_session_id)

        if snapshot.is_infra_failure:
            # 「没能问出结论」≠「登录失败」。浏览器容器重启时手机端那次扫码
            # 可能已经成功，直接判死等于让用户白扫（browser_client 顶部
            # is_infra_failure 的整条说明）。忍几轮再认输，且**不写 metadata**
            # —— 抖动期在用户眼前闪一串错误比沉默更糟。
            consecutive_infra += 1
            logger.warning(
                f"[session_login] infra failure {consecutive_infra}/"
                f"{max_infra_failures}: {snapshot.message}"
            )
            if consecutive_infra >= max_infra_failures:
                return LoginLoopOutcome(
                    "failed", snapshot.status, snapshot.message or "browser unreachable"
                )
            if monotonic() >= deadline:
                return LoginLoopOutcome(
                    "timeout", SessionStatus.TIMEOUT.value, "QR login timed out"
                )
            await sleep(poll_interval)
            continue

        consecutive_infra = 0
        status = snapshot.status
        # 只在"有变化"时写库：状态变了，或者二维码换了（qrcode_expired 后
        # 浏览器侧自动刷新，同响应带回新码 —— 状态可能连续两轮都是它）。
        key = (status, snapshot.qrcode_data_url)
        if key != last_key:
            await publish(snapshot)
            last_key = key

        if status == SessionStatus.SUCCESS.value:
            return LoginLoopOutcome("success", status, snapshot.message or "signed in")
        if status in LOGIN_FAILURE_STATUSES:
            return LoginLoopOutcome(
                "failed", status, snapshot.message or _STATUS_SUBTITLE.get(status, "")
            )
        if status not in LOGIN_PENDING_STATUSES:
            # 走到这里说明 §7.8 的枚举扩了新值而本循环没跟上。默认"继续等"
            # 会把它变成一次静默的 5 分钟空转，所以显式认输。
            return LoginLoopOutcome(
                "failed", SessionStatus.FAILED.value, f"unhandled login status {status}"
            )
        # 四个进行中状态（含 sms_required —— 验证码由 REST 端点直接送进浏览器，
        # 我们只等状态翻转；见 distribution_router 的 /sms 端点）。
        if monotonic() >= deadline:
            return LoginLoopOutcome(
                "timeout", SessionStatus.TIMEOUT.value, "QR login timed out"
            )
        await sleep(poll_interval)

    # 轮次用尽也是超时（§7.2：超时 raise，由 workflow 体执行）。
    return LoginLoopOutcome(
        "timeout", SessionStatus.TIMEOUT.value, "QR login timed out"
    )


# ── DBOS steps ───────────────────────────────────────────────


@DBOS.step()
async def mark_session_login_processing_step(
    workflow_id: str, user_id: str | None = None
) -> None:
    """queued → processing。与 publish_distribution 同款：trigger 只写
    ``status``，``phase`` 得由 manager 推。``user_id`` 必须透传，否则 start()
    的自愈建行会撞 ``task_tracking.user_id`` UUID NOT NULL。"""
    from app.services.infra.unified_task_manager import get_task_manager

    try:
        await get_task_manager().start(
            workflow_id, user_id=user_id, task_type=TASK_TYPE
        )
    except Exception as e:  # noqa: BLE001 — best-effort，同 publish 链
        logger.warning(f"[session_login.mark_processing] {workflow_id}: {e}")


@DBOS.step()
async def start_login_step(workflow_id: str, platform: str) -> dict[str, Any]:
    """起浏览器侧的保活 login session，把第一张二维码送进 metadata。

    返回值只有 ``login_session_id`` 与状态 —— **二维码不进 step 返回值**：
    它会被 DBOS 持久化进引擎表（几十 KB base64 × 每次重放），而它的唯一去处
    是 metadata，本 step 里已经写好了。
    """
    from app.services.distribution.browser_client import BrowserClient
    from app.services.infra.unified_task_manager import get_task_manager

    client = BrowserClient()
    snapshot = await client.start_login(platform)
    if not snapshot.success or not snapshot.login_session_id:
        # 起不来就没有 context 要释放（login_session_id 为空），直接失败。
        raise SessionLoginError(
            snapshot.message or "failed to start QR login", status=snapshot.status
        )
    writer = LoginMetadataWriter(get_task_manager(), workflow_id, platform)
    await writer.publish(snapshot)
    return {
        "login_session_id": snapshot.login_session_id,
        "status": snapshot.status,
    }


@DBOS.step()
async def poll_login_step(
    workflow_id: str, platform: str, login_session_id: str
) -> dict[str, Any]:
    """有上界地轮询登录状态，全程写 ``heartbeat_at``（§7.2）。

    heartbeat 是这里的必需品而不是装饰：一次扫码等待长达 5 分钟，没有心跳时
    ``workflow_health_sweeper`` 只能靠 wall-clock 判断，要么把正常等待的用户
    杀掉，要么在 worker 真死后沉默很久。
    """
    from app.services.distribution.browser_client import BrowserClient
    from app.services.infra.unified_task_manager import get_task_manager
    from app.services.workflow_heartbeat import async_heartbeat_loop

    manager = get_task_manager()
    writer = LoginMetadataWriter(manager, workflow_id, platform)
    client = BrowserClient()

    async with async_heartbeat_loop(workflow_id=workflow_id):
        outcome = await drive_login_loop(
            client=client,
            login_session_id=login_session_id,
            publish=writer.publish,
            is_cancelled=lambda: is_task_terminal(workflow_id),
        )
    logger.info(
        f"[session_login] platform={platform} outcome={outcome.outcome} "
        f"status={outcome.status}"
    )
    return {
        "outcome": outcome.outcome,
        "status": outcome.status,
        "message": outcome.message,
    }


@DBOS.step()
async def finalize_login_step(
    login_session_id: str,
    platform: str,
    scope_type: str,
    scope_id: str,
    user_id: str,
) -> dict[str, Any]:
    """取 storage_state → 加密入库 → 返回**公开**账号信息。

    明文凭证的整个生命周期都在这个函数体内（§7.6）：
    ``get_login_state`` 取出 → ``json.dumps`` → repository 的
    ``upsert_session_account`` 做 Fernet 加密 → 落 ``social_accounts.session_state``。
    返回值里只有 account_id / username / platform_user_id —— DBOS 会持久化
    step 输出，任何凭证进了返回值就等于写进了引擎表且不可撤回。
    """
    from app.repositories.social_accounts_repository import SocialAccountsRepository
    from app.services.distribution.browser_client import BrowserClient

    state = await BrowserClient().get_login_state(login_session_id)
    if not state.success or not state.storage_state:
        raise SessionLoginError(
            state.result.message or "browser returned no session state",
            status=state.status,
        )
    account = await SocialAccountsRepository().upsert_session_account(
        scope_type=scope_type,
        scope_id=scope_id,
        platform=platform,
        platform_user_id=state.platform_user_id,
        # 平台没给昵称时用平台 id 兜底 —— username 是 NOT NULL，且一个空名字
        # 的账号卡片在矩阵场景里等于不可辨认。
        username=state.username or str(state.platform_user_id),
        avatar_url=state.avatar_url,
        session_state=json.dumps(state.storage_state, ensure_ascii=False),
        created_by=user_id,
    )
    logger.info(
        f"[session_login] bound account={account.get('id')} platform={platform} "
        f"scope={scope_type}:{scope_id}"
    )
    return {
        "account_id": str(account.get("id")),
        "username": account.get("username"),
        "platform_user_id": str(state.platform_user_id),
    }


@DBOS.step()
async def close_login_step(login_session_id: str) -> None:
    """释放浏览器 context。永不抛异常 —— 它跑在 ``finally`` 里，清理失败不得
    盖掉正在传播的真实错误。"""
    from app.services.distribution.browser_client import BrowserClient

    try:
        await BrowserClient().close_login(login_session_id)
    except Exception as e:  # noqa: BLE001 — close_login 本身已吞异常，这是双保险
        logger.warning(f"[session_login.close] {login_session_id[:8]}: {e}")


# ── workflow ─────────────────────────────────────────────────


@DBOS.workflow()
async def session_login_workflow(
    platform: str,
    scope_type: str,
    scope_id: str,
    user_id: str,
) -> dict[str, Any]:
    """扫码绑定一个会话通道账号。

    终态：

    - **成功** → ``manager.complete()``，账号已加密入库。
    - **用户取消** → 正常 return（**不是** raise）。取消不是失败：此时
      ``task_tracking`` 已经是 ``cancelled``（取消端点写的），而 raise 会让
      DBOS 落 ERROR，trigger 随即把 ``status`` 从 cancelled 改写成 failed，
      用户按了"取消"却看到一条红色失败记录。返回正常值时 trigger 收到
      SUCCESS，而 mig 184 的守卫明确不让 SUCCESS 把 cancelled 改回 completed
      —— 这正是那条守卫存在的意义。
    - **超时 / 失败** → ``manager.fail()`` + **raise**（路线 C 纪律 4 与
      §7.2）。

    三条路径都会走 ``finally`` 释放浏览器 context。
    """
    from app.services.infra.unified_task_manager import get_task_manager

    manager = get_task_manager()
    workflow_id = DBOS.workflow_id
    await mark_session_login_processing_step(workflow_id, user_id)

    login_session_id: Optional[str] = None
    try:
        try:
            started = await start_login_step(workflow_id, platform)
        except SessionLoginError as e:
            await manager.fail(
                workflow_id,
                f"could not start QR login: {e}",
                metadata_patch={
                    "login": {
                        "platform": platform,
                        "status": e.status,
                        "qrcode_data_url": None,
                        "expires_at": None,
                        "message": str(e),
                    }
                },
            )
            raise
        login_session_id = started["login_session_id"]
        # login_session_id 进 metadata 是 /sms 与取消端点的**唯一**寻址方式：
        # 它们跑在 gateway 进程，而 context 活在浏览器容器里。它不是凭证 ——
        # 浏览器服务只在 docker 内网可达且校验 X-Internal-Token，前端拿到它
        # 也用不了（真正敏感的是同一份 metadata 里的二维码，那是设计使然）。
        await manager.patch_metadata(
            workflow_id,
            {
                "session_login": {
                    "login_session_id": login_session_id,
                    "platform": platform,
                    "scope_type": scope_type,
                    "scope_id": str(scope_id),
                }
            },
        )

        polled = await poll_login_step(workflow_id, platform, login_session_id)
        outcome = polled["outcome"]

        if outcome == "cancelled":
            logger.info(f"[session_login] {workflow_id} cancelled by user")
            return {"status": "cancelled", "platform": platform}

        if outcome != "success":
            message = polled.get("message") or "QR login did not complete"
            await manager.fail(
                workflow_id,
                f"QR login {outcome}: {message}",
                metadata_patch={
                    "login": {
                        "platform": platform,
                        "status": polled["status"],
                        "qrcode_data_url": None,
                        "expires_at": None,
                        "message": message,
                    }
                },
            )
            raise SessionLoginError(
                f"QR login {outcome}: {message}", status=polled["status"]
            )

        try:
            account = await finalize_login_step(
                login_session_id, platform, scope_type, scope_id, user_id
            )
        except SessionLoginError as e:
            await manager.fail(
                workflow_id,
                f"could not persist session: {e}",
                metadata_patch={
                    "login": {
                        "platform": platform,
                        "status": e.status,
                        "qrcode_data_url": None,
                        "expires_at": None,
                        "message": str(e),
                    }
                },
            )
            raise

        username = account.get("username") or platform
        await manager.complete(
            workflow_id,
            subtitle=f"Connected {username}",
            metadata_patch={
                # 二维码在这里被丢弃：它已经作废，且是这行里最占地方的东西。
                "login": {
                    "platform": platform,
                    "status": SessionStatus.SUCCESS.value,
                    "qrcode_data_url": None,
                    "expires_at": None,
                    "message": f"Connected {username}",
                },
                "session_login": {
                    "login_session_id": None,
                    "platform": platform,
                    "scope_type": scope_type,
                    "scope_id": str(scope_id),
                    "account_id": account["account_id"],
                },
            },
        )
        return {
            "status": "completed",
            "platform": platform,
            "account_id": account["account_id"],
        }
    finally:
        # 释放本身也用 try 包住：这段跑在异常传播路径上（也可能在 DBOS 已把
        # workflow 判成 cancelled 之后），此时**发起一个新 step** 本身就可能
        # 抛。清理的异常盖掉原始异常是最难查的一类事故。
        if login_session_id:
            try:
                await close_login_step(login_session_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"[session_login] context release failed for "
                    f"{login_session_id[:8]}: {exc}"
                )


__all__ = [
    "LOGIN_TTL_SECONDS",
    "MAX_CONSECUTIVE_INFRA_FAILURES",
    "MAX_POLLS",
    "POLL_INTERVAL_SECONDS",
    "TASK_TYPE",
    "LoginLoopOutcome",
    "LoginMetadataWriter",
    "SessionLoginError",
    "drive_login_loop",
    "is_task_terminal",
    "session_login_workflow",
]
