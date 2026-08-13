"""session_login_workflow —— 扫码登录的编排（会话通道 S2）。

两层覆盖：

1. ``drive_login_loop`` —— 各状态分支 / 二维码刷新 / sms / 超时 / 取消 /
   基建抖动。浏览器、时钟、DB 全部注入，纯编排。
2. workflow 体（``inspect.unwrap`` 穿过 @DBOS.workflow，steps 打桩）——
   这里只关心三件**必须成立**的事：
   - 四条路径（成功/失败/超时/取消）都释放浏览器 context；
   - 失败 raise、取消 return（路线 C 纪律 4 与 mig 184 守卫的交互）；
   - 明文凭证不进 step 返回值（§7.6）。
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.workflows.session_login as m
from app.services.distribution.browser_client import (
    LOGIN_STATUSES,
    LoginSnapshot,
    SessionErrorKind,
    SessionOpResult,
    SessionStatus,
)

pytestmark = pytest.mark.asyncio

SID = "ls_abc123"
QR = "data:image/png;base64,AAAA"
_USER = "11111111-1111-1111-1111-111111111111"


def _snap(status: str, *, qr=None, message="", error_kind=None) -> LoginSnapshot:
    detail = {"error_kind": error_kind} if error_kind else {}
    return LoginSnapshot(
        result=SessionOpResult(
            success=error_kind is None
            and status not in ("timeout", "failed", "proxy_failed"),
            status=status,
            message=message,
            detail=detail,
        ),
        login_session_id=SID,
        qrcode_data_url=qr,
    )


class _FakeClient:
    """按剧本逐轮回答 /status。最后一条会被重复返回（防止测试因轮次多而炸）。"""

    def __init__(self, script: list[LoginSnapshot]) -> None:
        self.script = script
        self.calls = 0

    async def get_login_status(self, login_session_id: str) -> LoginSnapshot:
        assert login_session_id == SID
        idx = min(self.calls, len(self.script) - 1)
        self.calls += 1
        return self.script[idx]


class _Harness:
    """循环的外部世界：收集 publish、控制取消与时钟。"""

    def __init__(self, *, cancel_after: int | None = None, tick: float = 3.0) -> None:
        self.published: list[LoginSnapshot] = []
        self.slept: list[float] = []
        self.now = 0.0
        self.tick = tick
        self.cancel_after = cancel_after
        self.cancel_checks = 0

    async def publish(self, snapshot: LoginSnapshot) -> None:
        self.published.append(snapshot)

    async def is_cancelled(self) -> bool:
        self.cancel_checks += 1
        return self.cancel_after is not None and self.cancel_checks > self.cancel_after

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


async def _run(client, harness, **kw):
    return await m.drive_login_loop(
        client=client,
        login_session_id=SID,
        publish=harness.publish,
        is_cancelled=harness.is_cancelled,
        sleep=harness.sleep,
        monotonic=harness.monotonic,
        **kw,
    )


# ── 正常流转 ────────────────────────────────────────────────


async def test_waiting_scan_then_scanned_then_success():
    client = _FakeClient(
        [
            _snap("waiting_scan", qr=QR),
            _snap("scanned"),
            _snap("success"),
        ]
    )
    harness = _Harness()
    outcome = await _run(client, harness)

    assert outcome.ok is True
    assert outcome.status == SessionStatus.SUCCESS.value
    assert [s.status for s in harness.published] == [
        "waiting_scan",
        "scanned",
        "success",
    ]


async def test_repeated_status_is_not_republished():
    """同一状态每轮都写库 = 每 3 秒一次 Realtime 广播，前端白闪一轮。"""
    client = _FakeClient(
        [
            _snap("waiting_scan", qr=QR),
            _snap("waiting_scan", qr=QR),
            _snap("waiting_scan", qr=QR),
            _snap("success"),
        ]
    )
    harness = _Harness()
    await _run(client, harness)

    assert [s.status for s in harness.published] == ["waiting_scan", "success"]


async def test_qrcode_refresh_is_republished_even_on_same_status():
    """qrcode_expired 后浏览器自动刷新 —— 新码必须送出去，否则用户扫的是废码。"""
    client = _FakeClient(
        [
            _snap("waiting_scan", qr=QR),
            _snap("qrcode_expired", qr="data:image/png;base64,NEW"),
            _snap("qrcode_expired", qr="data:image/png;base64,NEW"),
            _snap("success"),
        ]
    )
    harness = _Harness()
    outcome = await _run(client, harness)

    assert outcome.ok is True
    qrcodes = [s.qrcode_data_url for s in harness.published]
    assert qrcodes == [QR, "data:image/png;base64,NEW", None]


async def test_sms_required_keeps_polling_until_success():
    """验证码由 REST 端点直接送进浏览器，循环只等状态翻转。"""
    client = _FakeClient(
        [
            _snap("sms_required", message="Enter the code"),
            _snap("sms_required", message="Enter the code"),
            _snap("success"),
        ]
    )
    harness = _Harness()
    outcome = await _run(client, harness)

    assert outcome.ok is True
    assert harness.published[0].status == "sms_required"


async def test_identity_challenge_keeps_polling_until_the_code_step():
    """平台插了一屏「身份验证」—— 这是进行中，不是结论。

    浏览器侧在同一响应里**已经替我们点了**「接收短信验证码」，所以循环该做的
    是继续等状态翻转。少了 ``LOGIN_PENDING_STATUSES`` 里的那一项，它会掉进
    "unhandled login status" 分支当场判死，用户在选择页上就被告知登录失败。
    """
    client = _FakeClient(
        [
            _snap("scanned"),
            _snap("identity_challenge", message="verifying identity"),
            _snap("sms_required", message="Enter the code"),
            _snap("success"),
        ]
    )
    harness = _Harness()
    outcome = await _run(client, harness)

    assert outcome.ok is True
    assert [s.status for s in harness.published] == [
        "scanned",
        "identity_challenge",
        "sms_required",
        "success",
    ]


async def test_code_requested_reaches_the_frontend_blob():
    """``code_requested`` 是前端唯一能据以说"已代你请求发送验证码"的证据。

    ``metadata.login.detail`` 是白名单裁剪的，漏掉这个键不会报错 —— 只会让
    前端永远拿不到它，于是要么退回那句假话，要么对**真的**发了码的那次也说
    含糊话。两个方向都是回归。
    """
    manager = MagicMock()
    manager.patch_metadata = AsyncMock()
    manager.update_progress = AsyncMock()
    writer = m.LoginMetadataWriter(manager, "wf-1", "douyin")
    snapshot = _snap("sms_required", message="Enter the code")
    snapshot.result.detail["code_requested"] = True

    await writer.publish(snapshot)

    login = manager.patch_metadata.await_args_list[0].args[1]["login"]
    assert login["detail"]["code_requested"] is True


async def test_a_code_nobody_requested_carries_no_claim():
    """反向：没有那个键时，blob 里也不该凭空出现它。"""
    manager = MagicMock()
    manager.patch_metadata = AsyncMock()
    manager.update_progress = AsyncMock()
    writer = m.LoginMetadataWriter(manager, "wf-1", "douyin")

    await writer.publish(_snap("sms_required", message="Enter the code"))

    login = manager.patch_metadata.await_args_list[0].args[1]["login"]
    assert (login["detail"] or {}).get("code_requested") is None


@pytest.mark.parametrize("status", ["failed", "timeout", "proxy_failed"])
async def test_failure_status_ends_the_loop(status):
    client = _FakeClient([_snap(status, message="nope")])
    harness = _Harness()
    outcome = await _run(client, harness)

    assert outcome.outcome == "failed"
    assert outcome.status == status
    # 失败也要回显（§7.8 类型化失败回显，silent no-op 不可接受）
    assert harness.published[-1].status == status


# ── 上界（§7.2） ────────────────────────────────────────────


async def test_ttl_exhaustion_is_a_timeout():
    client = _FakeClient([_snap("waiting_scan", qr=QR)])
    harness = _Harness()
    outcome = await _run(client, harness, ttl_seconds=9.0, poll_interval=3.0)

    assert outcome.outcome == "timeout"
    assert outcome.status == SessionStatus.TIMEOUT.value
    # 循环真的停了，而不是靠调用方外面兜
    assert client.calls <= 4


async def test_poll_count_is_bounded_even_with_a_frozen_clock():
    """只有时间上界不够：一个恒定 0 延迟的对端会把循环变成忙等。"""
    client = _FakeClient([_snap("waiting_scan", qr=QR)])
    harness = _Harness()
    harness.tick = 0.0

    async def _no_sleep(_seconds: float) -> None:
        return None

    outcome = await m.drive_login_loop(
        client=client,
        login_session_id=SID,
        publish=harness.publish,
        is_cancelled=harness.is_cancelled,
        sleep=_no_sleep,
        monotonic=lambda: 0.0,  # 时钟永远不走
        max_polls=5,
    )
    assert outcome.outcome == "timeout"
    assert client.calls == 5


# ── 取消 ────────────────────────────────────────────────────


async def test_cancel_is_detected_before_the_next_poll():
    client = _FakeClient([_snap("waiting_scan", qr=QR)])
    harness = _Harness(cancel_after=1)
    outcome = await _run(client, harness)

    assert outcome.outcome == "cancelled"
    assert client.calls == 1  # 第二轮开头就停了


async def test_cancel_before_first_poll_never_touches_the_browser():
    client = _FakeClient([_snap("waiting_scan", qr=QR)])
    harness = _Harness(cancel_after=0)
    outcome = await _run(client, harness)

    assert outcome.outcome == "cancelled"
    assert client.calls == 0


# ── 基建抖动 vs 登录失败（§7.8 的核心区分） ────────────────


async def test_transient_infra_failure_is_tolerated():
    """容器重启那一轮，手机上那次扫码可能已经成功 —— 不许立刻判死。"""
    client = _FakeClient(
        [
            _snap("failed", error_kind=SessionErrorKind.UNREACHABLE.value),
            _snap("failed", error_kind=SessionErrorKind.UNREACHABLE.value),
            _snap("success"),
        ]
    )
    harness = _Harness()
    outcome = await _run(client, harness)

    assert outcome.ok is True
    # 抖动期不写 metadata —— 在用户眼前闪一串错误比沉默更糟
    assert [s.status for s in harness.published] == ["success"]


async def test_infra_failure_streak_gives_up():
    client = _FakeClient(
        [_snap("failed", error_kind=SessionErrorKind.UNREACHABLE.value)]
    )
    harness = _Harness()
    outcome = await _run(client, harness, max_infra_failures=3)

    assert outcome.outcome == "failed"
    assert client.calls == 3


async def test_infra_failure_streak_carries_the_error_kind_out_of_the_loop():
    """认输时 status 就是 ``failed`` —— 与"平台拒绝"完全同形。

    ``error_kind`` 是唯一能把两者分开的东西，掉在循环里就再也补不回来了：
    下游（workflow 体 → metadata → 前端）没有任何其他信号可用。
    """
    client = _FakeClient(
        [_snap("failed", error_kind=SessionErrorKind.UNREACHABLE.value)]
    )
    outcome = await _run(client, _Harness(), max_infra_failures=3)

    assert outcome.outcome == "failed"
    assert outcome.detail.get("error_kind") == "unreachable"


async def test_platform_rejection_carries_no_error_kind():
    """反向：平台真的拒绝时 detail 必须是空的。

    如果这里也带上 error_kind，"基建失败"这个标记就失去了鉴别力 ——
    前端会把每一次真实的平台拒绝都说成"我们这边的问题"。
    """
    outcome = await _run(
        _FakeClient([_snap("failed", message="account blocked")]), _Harness()
    )

    assert outcome.outcome == "failed"
    assert outcome.detail.get("error_kind") is None


async def test_an_unhandled_status_ends_the_loop_instead_of_spinning():
    """§7.8 枚举扩了新值而循环没跟上时，默认"继续等"= 静默空转 5 分钟。"""
    snap = _snap("waiting_scan")
    object.__setattr__(snap.result, "status", "some_new_status")
    outcome = await _run(_FakeClient([snap]), _Harness())

    assert outcome.outcome == "failed"
    assert "some_new_status" in outcome.message


async def test_infra_failure_counter_resets_after_recovery():
    script = [_snap("failed", error_kind=SessionErrorKind.UNREACHABLE.value)] * 2
    script += [_snap("waiting_scan", qr=QR)]
    script += [_snap("failed", error_kind=SessionErrorKind.UNREACHABLE.value)] * 2
    script += [_snap("success")]
    harness = _Harness()
    outcome = await _run(_FakeClient(script), harness, max_infra_failures=3)

    assert outcome.ok is True


# ── metadata writer ─────────────────────────────────────────


def test_status_subtitle_covers_every_login_status():
    """8 条兜底必须**正好**盖住 §7.8 的登录子集，不多不少。

    它有两个消费者，两个都会在漏一条时静默降级而不报错：

    - ``update_progress(subtitle=...)`` —— 任务中心那一行字。漏了就退回
      ``"Signing in"``，于是一个新状态在任务列表里跟"正在登录"长得一模一样。
    - ``login_metadata(message=snapshot.message or _STATUS_SUBTITLE[...])``
      —— 浏览器侧没给判词时的兜底。

    多一条同样是问题：那说明这里的键和 ``LOGIN_STATUSES`` 已经对不上了，而
    枚举漂移在这条链上的历史后果是把"已扫码"显示成"登录失败"（见文件头）。
    """
    assert set(m._STATUS_SUBTITLE) == LOGIN_STATUSES


async def test_metadata_writer_keeps_the_last_qrcode_sticky():
    """/status 只在刷新时带回码；不粘住的话第二轮二维码就凭空消失了。"""
    manager = MagicMock()
    manager.patch_metadata = AsyncMock()
    manager.update_progress = AsyncMock()
    writer = m.LoginMetadataWriter(manager, "wf-1", "douyin")

    await writer.publish(_snap("waiting_scan", qr=QR))
    await writer.publish(_snap("scanned"))  # 无码

    first, second = [c.args[1]["login"] for c in manager.patch_metadata.await_args_list]
    assert first["qrcode_data_url"] == QR
    assert second["qrcode_data_url"] == QR
    assert second["status"] == "scanned"
    # 前端契约的 6 个键（spec §4.1 + §7.8 的 detail）
    assert set(second) == {
        "platform",
        "status",
        "qrcode_data_url",
        "expires_at",
        "message",
        "detail",
    }


async def test_metadata_write_survives_a_throttled_subtitle_update():
    """subtitle 被 1 write/sec 节流丢弃是常态；二维码绝不能跟着丢。"""
    manager = MagicMock()
    manager.patch_metadata = AsyncMock()
    manager.update_progress = AsyncMock(side_effect=RuntimeError("throttled"))
    writer = m.LoginMetadataWriter(manager, "wf-1", "douyin")

    await writer.publish(_snap("waiting_scan", qr=QR))

    manager.patch_metadata.assert_awaited_once()


async def test_metadata_writer_publishes_the_error_kind():
    manager = MagicMock()
    manager.patch_metadata = AsyncMock()
    manager.update_progress = AsyncMock()
    writer = m.LoginMetadataWriter(manager, "wf-1", "douyin")

    await writer.publish(_snap("failed", error_kind=SessionErrorKind.UNREACHABLE.value))

    login = manager.patch_metadata.await_args.args[1]["login"]
    assert login["detail"] == {"error_kind": "unreachable"}


async def test_public_detail_only_lets_the_two_contract_keys_through():
    """``metadata.login`` 经 Realtime 广播到浏览器，所以是白名单而非整包透传。

    上游 detail 里混着传输层内务（HTTP 码、超时秒数、浏览器侧的 stage），
    它们对用户没有意义，也不该变成前端可以依赖的契约。
    """
    public = m._public_detail(
        {
            "error_kind": "unreachable",
            "reason": "no session bound",
            "status_code": 502,
            "timeout_seconds": 20.0,
            "stage": "driver",
        }
    )

    assert public == {"error_kind": "unreachable", "reason": "no session bound"}


async def test_public_detail_carries_the_identity_challenge_diagnostics():
    """身份验证失败的证据必须落到那条失败记录上（2026-08-12）。

    上一次失败之所以只能靠猜，正是因为浏览器侧算出来的 ``options_seen`` /
    ``polls`` 卡在这份白名单外面：排障时 ``SELECT metadata->'login'->'detail'``
    只有一个 reason，而"点击没打中 handler"/"平台换了页但我们的文案还匹配"
    /"平台上了滑块"三种情况在库里长得一模一样。

    ⚠️ 这一组不是给 UI 的文案（UI 只认 ``reason``），是给排障的证据 —— 浏览器
    context 在失败瞬间就释放了，页面没有第二次机会被观察。
    """
    public = m._public_detail(
        {
            "reason": "identity_challenge_stalled",
            "options_seen": ["接收短信验证码", "发送短信验证"],
            "polls": 12,
            "identity_option": "接收短信验证码",
            "progress_signals": [],
            "escalated_click": 'xpath=ancestor-or-self::*[@role="button"][1]',
            "page_evidence": {"url": "https://creator.douyin.com/"},
            # 传输层内务照旧不透传。
            "stage": "driver",
            "status_code": 502,
        }
    )

    assert public is not None
    assert public["options_seen"] == ["接收短信验证码", "发送短信验证"]
    assert public["polls"] == 12
    assert public["page_evidence"] == {"url": "https://creator.douyin.com/"}
    # 空列表必须活着穿过白名单：``progress_signals: []`` 的意思是"点了、页面
    # 一动不动"，是结论本身，不是"没有值"。过滤条件写 ``is not None`` 而不是
    # 真值判断，正是为了这个。
    assert public["progress_signals"] == []
    assert "stage" not in public
    assert "status_code" not in public


async def test_public_detail_is_none_when_there_is_nothing_to_say():
    """平台拒绝 → 没有 error_kind → 前端的 ``detail?.error_kind`` 落 undefined。"""
    assert m._public_detail({}) is None
    assert m._public_detail(None) is None
    assert m._public_detail({"stage": "driver"}) is None


async def test_metadata_message_falls_back_to_an_english_status_line():
    """浏览器侧没给 message 时不能留空 —— 空回显等于没有回显。"""
    manager = MagicMock()
    manager.patch_metadata = AsyncMock()
    manager.update_progress = AsyncMock()
    writer = m.LoginMetadataWriter(manager, "wf-1", "douyin")

    await writer.publish(_snap("scanned"))
    login = manager.patch_metadata.await_args.args[1]["login"]
    assert login["message"] == "Confirm on your phone"


# ── workflow 体 ─────────────────────────────────────────────


def _manager() -> MagicMock:
    mgr = MagicMock()
    for name in ("patch_metadata", "update_progress", "complete", "fail", "start"):
        setattr(mgr, name, AsyncMock())
    return mgr


async def _run_workflow(*, poll_outcome: dict, finalize=None, start=None):
    manager = _manager()
    closed: list[str] = []

    async def _close(login_session_id: str) -> None:
        closed.append(login_session_id)

    start_step = start or AsyncMock(
        return_value={"login_session_id": SID, "status": "waiting_scan"}
    )
    finalize_step = finalize or AsyncMock(
        return_value={
            "account_id": "900",
            "username": "Test Creator",
            "platform_user_id": "uid-1",
        }
    )
    dbos = MagicMock()
    dbos.workflow_id = "wf-1"

    with (
        patch.object(m, "DBOS", dbos),
        patch.object(m, "mark_session_login_processing_step", AsyncMock()),
        patch.object(m, "start_login_step", start_step),
        patch.object(m, "poll_login_step", AsyncMock(return_value=poll_outcome)),
        patch.object(m, "finalize_login_step", finalize_step),
        patch.object(m, "close_login_step", AsyncMock(side_effect=_close)),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=manager,
        ),
    ):
        try:
            result = await inspect.unwrap(m.session_login_workflow)(
                platform="douyin",
                scope_type="user",
                scope_id=_USER,
                user_id=_USER,
            )
            error = None
        except Exception as exc:  # noqa: BLE001 — 断言 raise 与否是测试目的
            result, error = None, exc
    return result, error, manager, closed


async def test_success_completes_and_releases_the_context():
    result, error, manager, closed = await _run_workflow(
        poll_outcome={"outcome": "success", "status": "success", "message": "ok"}
    )

    assert error is None
    assert result["status"] == "completed"
    assert result["account_id"] == "900"
    manager.complete.assert_awaited_once()
    assert closed == [SID]
    # 作废的二维码不该留在库里
    login = manager.complete.await_args.kwargs["metadata_patch"]["login"]
    assert login["qrcode_data_url"] is None
    assert login["status"] == "success"


async def test_login_session_id_is_published_for_the_sms_endpoint():
    """/sms 与取消端点跑在 gateway 进程，只能靠 metadata 找到那个 context。"""
    _, _, manager, _ = await _run_workflow(
        poll_outcome={"outcome": "success", "status": "success", "message": ""}
    )
    patches = [c.args[1] for c in manager.patch_metadata.await_args_list]
    session_login = next(p["session_login"] for p in patches if "session_login" in p)
    assert session_login["login_session_id"] == SID
    # 前端契约的 login blob 不被污染
    assert "login_session_id" not in (patches[0].get("login") or {})


async def test_timeout_fails_the_task_raises_and_releases():
    result, error, manager, closed = await _run_workflow(
        poll_outcome={
            "outcome": "timeout",
            "status": "timeout",
            "message": "QR login timed out",
        }
    )

    # 路线 C 纪律 4 + §7.2：超时必须 raise，绝不 return failed dict
    assert result is None
    assert isinstance(error, m.SessionLoginError)
    manager.fail.assert_awaited_once()
    assert closed == [SID]


async def test_browser_failure_fails_the_task_and_releases():
    result, error, manager, closed = await _run_workflow(
        poll_outcome={"outcome": "failed", "status": "proxy_failed", "message": "407"}
    )

    assert result is None
    assert error is not None
    login = manager.fail.await_args.kwargs["metadata_patch"]["login"]
    assert login["status"] == "proxy_failed"
    assert closed == [SID]


async def test_browser_unreachable_reaches_the_frontend_as_an_infra_failure():
    """P3-1：容器连不上时 UI 曾显示"平台拒绝了本次登录，确认账号是否被限制"。

    那句话把用户支去查自己账号有没有被封，而真相是我们的 nous-browser 正在
    重启。前端**已经**有分支能说对话（``failedInfraHint``），它读的是
    ``metadata.login.detail.error_kind`` —— workflow 从来没写过这个键，所以
    那个分支在生产里一次都没触发过。这条断言就是那根接线。
    """
    _, error, manager, _ = await _run_workflow(
        poll_outcome={
            "outcome": "failed",
            "status": "failed",
            "message": "browser service unreachable (ConnectError)",
            "detail": {"error_kind": SessionErrorKind.UNREACHABLE.value},
        }
    )

    assert error is not None
    login = manager.fail.await_args.kwargs["metadata_patch"]["login"]
    assert login["status"] == "failed"
    assert login["detail"] == {"error_kind": "unreachable"}


async def test_a_real_platform_rejection_stays_a_platform_rejection():
    """反向对照：平台真的拒绝时不能带 error_kind。

    带了的话前端会把每一次真实拒绝都说成"我们这边的问题，稍后重试"，
    用户就永远不会去查那个真的被限制了的账号。
    """
    _, error, manager, _ = await _run_workflow(
        poll_outcome={
            "outcome": "failed",
            "status": "failed",
            "message": "account is restricted",
            "detail": {},
        }
    )

    assert error is not None
    login = manager.fail.await_args.kwargs["metadata_patch"]["login"]
    assert login["detail"] is None


async def test_start_failure_carries_its_error_kind_into_metadata():
    """起不来的那一刻同样分两类（容器没起来 vs 平台把登录页关了）。"""
    start = AsyncMock(
        side_effect=m.SessionLoginError(
            "browser service unreachable (ConnectError)",
            status="failed",
            detail={"error_kind": SessionErrorKind.UNREACHABLE.value},
        )
    )
    _, error, manager, closed = await _run_workflow(
        poll_outcome={"outcome": "success", "status": "success", "message": ""},
        start=start,
    )

    assert isinstance(error, m.SessionLoginError)
    assert closed == []
    login = manager.fail.await_args.kwargs["metadata_patch"]["login"]
    assert login["detail"] == {"error_kind": "unreachable"}


async def test_cancel_returns_without_failing_the_task():
    """取消不是失败：raise 会让 DBOS 落 ERROR，trigger 把 cancelled 改写成
    failed，用户按了取消却看到一条红色失败记录（mig 184 守卫只挡 SUCCESS）。"""
    result, error, manager, closed = await _run_workflow(
        poll_outcome={"outcome": "cancelled", "status": "failed", "message": ""}
    )

    assert error is None
    assert result["status"] == "cancelled"
    manager.fail.assert_not_awaited()
    manager.complete.assert_not_awaited()
    assert closed == [SID]


async def test_finalize_failure_still_releases_the_context():
    """会话已经拿到手却入库失败 —— context 照样要还。"""
    finalize = AsyncMock(side_effect=m.SessionLoginError("db down", status="failed"))
    result, error, manager, closed = await _run_workflow(
        poll_outcome={"outcome": "success", "status": "success", "message": ""},
        finalize=finalize,
    )

    assert result is None
    assert isinstance(error, m.SessionLoginError)
    manager.fail.assert_awaited_once()
    assert closed == [SID]


async def test_start_failure_fails_loud_and_has_nothing_to_release():
    """起不来就没有 context —— 不该对着一个不存在的 id 调 close。"""
    start = AsyncMock(side_effect=m.SessionLoginError("browser down", status="failed"))
    result, error, manager, closed = await _run_workflow(
        poll_outcome={"outcome": "success", "status": "success", "message": ""},
        start=start,
    )

    assert result is None
    assert isinstance(error, m.SessionLoginError)
    manager.fail.assert_awaited_once()
    assert closed == []


async def test_context_release_failure_does_not_mask_the_real_error():
    """清理异常盖掉原始异常是最难查的一类事故。"""
    manager = _manager()
    dbos = MagicMock()
    dbos.workflow_id = "wf-1"
    with (
        patch.object(m, "DBOS", dbos),
        patch.object(m, "mark_session_login_processing_step", AsyncMock()),
        patch.object(
            m,
            "start_login_step",
            AsyncMock(return_value={"login_session_id": SID, "status": "waiting_scan"}),
        ),
        patch.object(
            m,
            "poll_login_step",
            AsyncMock(
                return_value={
                    "outcome": "timeout",
                    "status": "timeout",
                    "message": "timed out",
                }
            ),
        ),
        patch.object(
            m, "close_login_step", AsyncMock(side_effect=RuntimeError("close blew up"))
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=manager,
        ),
        pytest.raises(m.SessionLoginError),  # 原始错误，不是 close 的那个
    ):
        await inspect.unwrap(m.session_login_workflow)(
            platform="douyin",
            scope_type="user",
            scope_id=_USER,
            user_id=_USER,
        )


# ── 凭证边界（§7.6） ────────────────────────────────────────


async def test_finalize_step_never_returns_the_plaintext_session():
    """DBOS 会持久化 step 输出 —— 凭证进了返回值就等于写进引擎表且不可撤回。"""
    from app.services.distribution.browser_client import LoginState

    state = LoginState(
        result=SessionOpResult(True, "success", "ok", {}),
        storage_state={"cookies": [{"name": "sessionid", "value": "s3cr3t"}]},
        platform_user_id="uid-1",
        username="Test Creator",
        avatar_url=None,
    )
    repo = MagicMock()
    repo.upsert_session_account = AsyncMock(
        return_value={"id": "900", "username": "Test Creator"}
    )
    client = MagicMock()
    client.get_login_state = AsyncMock(return_value=state)

    with (
        patch(
            "app.services.distribution.browser_client.BrowserClient",
            return_value=client,
        ),
        patch(
            "app.repositories.social_accounts_repository.SocialAccountsRepository",
            return_value=repo,
        ),
    ):
        out = await inspect.unwrap(m.finalize_login_step)(
            SID, "douyin", "user", _USER, _USER
        )

    assert "s3cr3t" not in str(out)
    assert set(out) == {"account_id", "username", "platform_user_id"}
    # 明文只走到 repository（那里做 Fernet 加密），且是 JSON 字符串
    written = repo.upsert_session_account.await_args.kwargs
    assert "s3cr3t" in written["session_state"]
    assert written["platform_user_id"] == "uid-1"
    assert written["scope_type"] == "user"


async def test_finalize_step_writes_the_handle_without_touching_the_identity():
    """抖音号进 platform_handle,身份键仍是 cookie id(mig 414 / P0-1)。

    这两个字段一旦又合流,重新绑定就会因为用户改了抖音号而建出第二行账号 ——
    2026-08-09 的原样复发。
    """
    from app.services.distribution.browser_client import LoginState

    state = LoginState(
        result=SessionOpResult(True, "success", "ok", {}),
        storage_state={"cookies": []},
        platform_user_id="41cf16775ee3e9fdf5e021f9c1ddfc12",
        username="MioPoo",
        platform_handle="miopoo",
    )
    repo = MagicMock()
    repo.upsert_session_account = AsyncMock(
        return_value={"id": "900", "username": "MioPoo"}
    )
    client = MagicMock()
    client.get_login_state = AsyncMock(return_value=state)

    with (
        patch(
            "app.services.distribution.browser_client.BrowserClient",
            return_value=client,
        ),
        patch(
            "app.repositories.social_accounts_repository.SocialAccountsRepository",
            return_value=repo,
        ),
    ):
        await inspect.unwrap(m.finalize_login_step)(SID, "douyin", "user", _USER, _USER)

    written = repo.upsert_session_account.await_args.kwargs
    assert written["platform_user_id"] == "41cf16775ee3e9fdf5e021f9c1ddfc12"
    assert written["platform_handle"] == "miopoo"


async def test_finalize_step_falls_back_to_the_handle_for_a_missing_nickname():
    """username 是 NOT NULL。没昵称时 `miopoo` 比 32 位 hex 可读得多 —— 但这只是
    显示兜底,platform_user_id 一步也不许跟着动。"""
    from app.services.distribution.browser_client import LoginState

    state = LoginState(
        result=SessionOpResult(True, "success", "ok", {}),
        storage_state={"cookies": []},
        platform_user_id="41cf16775ee3e9fdf5e021f9c1ddfc12",
        username=None,
        platform_handle="miopoo",
    )
    repo = MagicMock()
    repo.upsert_session_account = AsyncMock(return_value={"id": "900"})
    client = MagicMock()
    client.get_login_state = AsyncMock(return_value=state)

    with (
        patch(
            "app.services.distribution.browser_client.BrowserClient",
            return_value=client,
        ),
        patch(
            "app.repositories.social_accounts_repository.SocialAccountsRepository",
            return_value=repo,
        ),
    ):
        await inspect.unwrap(m.finalize_login_step)(SID, "douyin", "user", _USER, _USER)

    written = repo.upsert_session_account.await_args.kwargs
    assert written["username"] == "miopoo"
    assert written["platform_user_id"] == "41cf16775ee3e9fdf5e021f9c1ddfc12"


async def test_finalize_step_raises_when_the_browser_has_no_state():
    """扫了码却没有会话物料时建一个空账号行，用户会以为绑好了。"""
    from app.services.distribution.browser_client import LoginState

    client = MagicMock()
    client.get_login_state = AsyncMock(
        return_value=LoginState(
            result=SessionOpResult(False, "failed", "no state", {}),
        )
    )
    with patch(
        "app.services.distribution.browser_client.BrowserClient", return_value=client
    ):
        with pytest.raises(m.SessionLoginError):
            await inspect.unwrap(m.finalize_login_step)(
                SID, "douyin", "user", _USER, _USER
            )


async def test_session_login_error_survives_a_pickle_round_trip():
    """DBOS 会 pickle step 抛出的异常；必填 keyword-only 参数会炸在反序列化。

    走 ``importlib`` 拿当前 sys.modules 里的那份类对象：同目录的
    ``test_role_aware_imports`` 会 reload ``app.workflows``，之后本模块顶部
    import 到的类与 pickle 按名字查回来的类可能不是同一个对象。
    """
    import importlib
    import pickle

    mod = importlib.import_module("app.workflows.session_login")
    revived = pickle.loads(pickle.dumps(mod.SessionLoginError("x", status="timeout")))
    assert isinstance(revived, mod.SessionLoginError)


# ── 每账号浏览器环境（P2-4，mig 402 + 424） ─────────────────────────


def _login_client(**extra):
    client = MagicMock()
    client.start_login = AsyncMock(
        return_value=_snap(SessionStatus.WAITING_SCAN.value, qr=QR)
    )
    for k, v in extra.items():
        setattr(client, k, v)
    return client


def _patched_start(client, repo):
    return (
        patch(
            "app.services.distribution.browser_client.BrowserClient",
            return_value=client,
        ),
        patch(
            "app.repositories.social_accounts_repository.SocialAccountsRepository",
            return_value=repo,
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=_manager(),
        ),
    )


async def test_start_step_opens_the_login_context_with_a_generated_environment():
    """**这条钉的是 P2-4 的实际缺口。**

    在此之前 ``start_login_step`` 调的是 ``client.start_login(platform)`` ——
    不带 environment。于是浏览器侧 ``_login_context_kwargs`` 那句"账号的环境在
    绑定那一刻就钉住"的承诺是空的：登录用的永远是库默认值。
    """
    client = _login_client()
    repo = MagicMock()
    repo.taken_viewports = AsyncMock(return_value=[])

    a, b, c = _patched_start(client, repo)
    with a, b, c:
        out = await inspect.unwrap(m.start_login_step)("wf-1", "douyin", "user", _USER)

    env = client.start_login.await_args.kwargs.get("environment")
    assert env is not None, "登录 context 没拿到 environment —— P2-4 的缺口原样还在"
    assert env.locale == "zh-CN"
    assert env.timezone_id == "Asia/Shanghai"
    assert env.viewport_width and env.viewport_height
    # 登录用的那一套必须原样传给 finalize 去钉住，否则"绑定时见到的指纹"与
    # "以后每次用的指纹"是两套。
    assert out["environment"]["viewport_width"] == env.viewport_width
    assert out["environment"]["viewport_height"] == env.viewport_height


async def test_start_step_avoids_a_viewport_a_sibling_account_already_uses():
    from app.services.distribution.account_environment import SESSION_VIEWPORTS

    client = _login_client()
    repo = MagicMock()
    repo.taken_viewports = AsyncMock(return_value=list(SESSION_VIEWPORTS[:-1]))

    a, b, c = _patched_start(client, repo)
    with a, b, c:
        out = await inspect.unwrap(m.start_login_step)("wf-1", "douyin", "user", _USER)

    assert repo.taken_viewports.await_args.args == ("user", _USER, "douyin")
    w, h = SESSION_VIEWPORTS[-1]
    got = (out["environment"]["viewport_width"], out["environment"]["viewport_height"])
    assert got == (w, h)


async def test_start_step_still_binds_when_the_viewport_lookup_fails():
    """避让是锦上添花，不是绑号的前置条件 —— DB 抖一下不该挡住用户绑账号。"""
    client = _login_client()
    repo = MagicMock()
    repo.taken_viewports = AsyncMock(side_effect=RuntimeError("db down"))

    a, b, c = _patched_start(client, repo)
    with a, b, c:
        out = await inspect.unwrap(m.start_login_step)("wf-1", "douyin", "user", _USER)

    assert out["login_session_id"] == SID
    assert out["environment"]["viewport_width"]


def _finalize_fakes(pin=None):
    from app.services.distribution.browser_client import LoginState

    state = LoginState(
        result=SessionOpResult(True, "success", "ok", {}),
        storage_state={"cookies": []},
        platform_user_id="uid-1",
        username="Test Creator",
    )
    repo = MagicMock()
    repo.upsert_session_account = AsyncMock(
        return_value={"id": "900", "username": "Test Creator"}
    )
    repo.pin_environment = pin or AsyncMock(
        return_value={"viewport_width": 1600, "viewport_height": 900}
    )
    client = MagicMock()
    client.get_login_state = AsyncMock(return_value=state)
    return repo, client


async def test_finalize_step_pins_the_environment_the_login_actually_used():
    """账号入库后立刻钉住**本次登录用过的**那一套。

    传别的（比如现场重新生成一套）就等于：平台在绑定那一刻见到的指纹，与它
    之后每次见到的不是同一个。
    """
    repo, client = _finalize_fakes()
    env_payload = {
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "viewport_width": 1600,
        "viewport_height": 900,
    }
    with (
        patch(
            "app.services.distribution.browser_client.BrowserClient",
            return_value=client,
        ),
        patch(
            "app.repositories.social_accounts_repository.SocialAccountsRepository",
            return_value=repo,
        ),
    ):
        await inspect.unwrap(m.finalize_login_step)(
            SID, "douyin", "user", _USER, _USER, env_payload
        )

    repo.pin_environment.assert_awaited_once()
    assert repo.pin_environment.await_args.args == ("900",)
    written = repo.pin_environment.await_args.kwargs
    assert written["viewport_width"] == 1600
    assert written["viewport_height"] == 900
    assert written["locale"] == "zh-CN"
    assert written["timezone_id"] == "Asia/Shanghai"
    # 决定不设的字段一个都不许出现 —— 显式 None 会让"决定不设"与"忘了设"同形
    for absent in (
        "user_agent",
        "geo_lat",
        "geo_lng",
        "proxy_url",
        "fingerprint_profile_id",
    ):
        assert absent not in written


async def test_a_failed_pin_does_not_undo_a_successful_bind():
    """账号已经绑上了，环境没钉住只是退回默认值（mig 424 之前的行为）。
    为此把成功的扫码判成失败、让用户重扫，是拿降级当故障。"""
    repo, client = _finalize_fakes(pin=AsyncMock(side_effect=RuntimeError("db down")))
    with (
        patch(
            "app.services.distribution.browser_client.BrowserClient",
            return_value=client,
        ),
        patch(
            "app.repositories.social_accounts_repository.SocialAccountsRepository",
            return_value=repo,
        ),
    ):
        out = await inspect.unwrap(m.finalize_login_step)(
            SID,
            "douyin",
            "user",
            _USER,
            _USER,
            {"viewport_width": 1600, "viewport_height": 900},
        )

    assert out["account_id"] == "900"


async def test_workflow_hands_the_login_environment_to_finalize():
    """编排层的接线：``start`` 生成的环境必须一路走到 ``finalize``。

    两个 step 各自对了、中间没接上，是这类改动最容易留的缺口 —— 结果就是
    环境生成了、登录也用了，但一行都没落库。
    """
    finalize = AsyncMock(
        return_value={
            "account_id": "900",
            "username": "Test Creator",
            "platform_user_id": "uid-1",
        }
    )
    start = AsyncMock(
        return_value={
            "login_session_id": SID,
            "status": "waiting_scan",
            "environment": {
                "locale": "zh-CN",
                "timezone_id": "Asia/Shanghai",
                "viewport_width": 1680,
                "viewport_height": 1050,
            },
        }
    )
    result, error, _mgr, _closed = await _run_workflow(
        poll_outcome={"outcome": "success", "status": "success", "message": "ok"},
        finalize=finalize,
        start=start,
    )

    assert error is None and result["status"] == "completed"
    passed = finalize.await_args.args[5]
    assert passed["viewport_width"] == 1680
    assert passed["viewport_height"] == 1050


async def test_workflow_replays_a_pre_migration_start_step_without_an_environment():
    """mig 424 之前起的 workflow 重放时，DBOS 记着的 step 返回值没有
    ``environment`` 键。用 ``[...]`` 取会 KeyError，把一次本可完成的重放变成
    引擎错误。"""
    finalize = AsyncMock(
        return_value={
            "account_id": "900",
            "username": "Test Creator",
            "platform_user_id": "uid-1",
        }
    )
    start = AsyncMock(return_value={"login_session_id": SID, "status": "waiting_scan"})
    result, error, _mgr, _closed = await _run_workflow(
        poll_outcome={"outcome": "success", "status": "success", "message": "ok"},
        finalize=finalize,
        start=start,
    )

    assert error is None and result["status"] == "completed"
    assert finalize.await_args.args[5] is None
