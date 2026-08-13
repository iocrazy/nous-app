"""BrowserClient 的扫码登录面（S2, spec §4.1 / §7.8）。

关注三件容易做错的事：

1. **状态枚举全集对齐**。§7.8 明写两侧枚举必须逐值一致；少一个值不会报错，
   只会静默降级成 ``failed`` —— 把"已扫码，等你确认"显示成"登录失败"。
2. **二维码必须能持续送出**。刷新后的新码走的是同一个 ``/status`` 响应。
3. **凭证不进日志**（§7.6）。``LoginState``/``LoginSnapshot`` 的 repr 是最
   容易的泄漏路径（loguru 的 f-string 会带上它）。
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.distribution.browser_client import (
    INTERNAL_TOKEN_HEADER,
    LOGIN_STATUSES,
    BrowserClient,
    SessionEnvironment,
    SessionErrorKind,
    SessionStatus,
    is_infra_failure,
)

BASE = "http://nous-browser:8090"
SID = "ls_abc123"
QR = "data:image/png;base64,AAAA"


def _client(**kw) -> BrowserClient:
    kw.setdefault("base_url", BASE)
    kw.setdefault("token", "tok_test")
    return BrowserClient(**kw)


# ── 枚举对齐（§7.8 权威表） ─────────────────────────────────


def test_login_status_enum_matches_spec_table():
    """spec §7.8 的登录子集，逐值。多一个少一个都是跨服务契约漂移。"""
    assert LOGIN_STATUSES == {
        "waiting_scan",
        "scanned",
        "qrcode_expired",
        # 平台的「身份验证」选择页（2026-08-11）。它必须是独立值：
        # ``sms_required`` 授权的那句 UI 文案是"把收到的验证码填进来"，而这一屏
        # 上平台还在等我们选验证方式，**一条短信都没发**。合并两者正是那次
        # 用户对着不存在的验证码干等到超时的直接原因。
        "identity_challenge",
        # 没有扫码登录的平台（2026-08-13，小红书）。同样必须是独立值，理由与
        # 上一个完全同族：``sms_required`` 的意思是"把收到的码填进来"，而在还
        # 没有人给过手机号的时候，那条短信**不可能存在** —— 没人知道该发给谁。
        "phone_required",
        "sms_required",
        "success",
        "timeout",
        "proxy_failed",
        "failed",
    }


def test_the_phone_step_is_pending_and_a_refusal_is_not_a_success():
    """两条不变量，各自对应一个真实事故形状。

    1. ``phone_required`` 必须在 PENDING 集合里 —— 掉出去，``drive_login_loop``
       会走进 "unhandled login status" 把一次正常等待的登录当场判死。
    2. 用户提交了手机号却没成（页面上找不到输入框 / 发码按钮点不动）时，
       ``success`` 必须是 False。它是进行中状态，不特判就会以 success=True 回到
       前端，前端走成功分支——清空输入框、什么都不说，用户对着一个毫无变化的
       表单发呆。这正是"验证码输错零反馈"那个 bug 的同一形状。
    """
    from app.services.distribution.browser_client import (
        LOGIN_FAILURE_STATUSES,
        LOGIN_PENDING_STATUSES,
    )

    assert SessionStatus.PHONE_REQUIRED.value in LOGIN_PENDING_STATUSES
    assert SessionStatus.PHONE_REQUIRED.value not in LOGIN_FAILURE_STATUSES

    snapshot = BrowserClient._login_snapshot(
        {
            "status": "phone_required",
            "message": "no phone number field was found on the sign-in page",
            "detail": {"reason": "phone_input_missing"},
        },
        login_session_id=SID,
    )
    assert snapshot.result.success is False
    assert snapshot.result.detail["reason"] == "phone_input_missing"

    # 第一次要号码（没有 reason）仍然是 success=True：那不是失败，是流程的一步。
    fresh = BrowserClient._login_snapshot(
        {"status": "phone_required", "message": "enter the phone number", "detail": {}},
        login_session_id=SID,
    )
    assert fresh.result.success is True


def test_the_identity_challenge_is_a_pending_status_not_a_verdict():
    """浏览器侧在同一响应里已经替我们点了选项 —— 所以它跟 qrcode_expired 一样
    是"画面更新"，循环要继续等。掉出 PENDING 集合会让 ``drive_login_loop``
    走进 "unhandled login status" 当场判死。"""
    from app.services.distribution.browser_client import (
        LOGIN_FAILURE_STATUSES,
        LOGIN_PENDING_STATUSES,
    )

    assert SessionStatus.IDENTITY_CHALLENGE.value in LOGIN_PENDING_STATUSES
    assert SessionStatus.IDENTITY_CHALLENGE.value not in LOGIN_FAILURE_STATUSES


# ── /session/login/start ────────────────────────────────────


@respx.mock
async def test_start_login_returns_session_and_qrcode():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["headers"] = request.headers
        captured["body"] = _json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "login_session_id": SID,
                "status": "waiting_scan",
                "qrcode_data_url": QR,
                "expires_at": "2026-08-05T01:30:00Z",
                "message": "Scan with the Douyin app",
            },
        )

    respx.post(f"{BASE}/session/login/start").mock(side_effect=_handler)
    snapshot = await _client().start_login("douyin")

    assert captured["headers"][INTERNAL_TOKEN_HEADER] == "tok_test"
    assert captured["body"]["platform"] == "douyin"
    # environment 的 8 个键全部显式发出（与 /validate 同款契约）。
    # viewport_* 是 mig 424 加的 —— 登录用的 context 必须和之后每次校验/发布
    # 用的是同一套尺寸，所以两条链的键集合必须一致。
    assert set(captured["body"]["environment"]) == {
        "proxy_url",
        "user_agent",
        "locale",
        "timezone_id",
        "geo_lat",
        "geo_lng",
        "viewport_width",
        "viewport_height",
    }
    assert snapshot.success is True
    assert snapshot.login_session_id == SID
    assert snapshot.qrcode_data_url == QR
    assert snapshot.expires_at == "2026-08-05T01:30:00Z"
    assert snapshot.status == SessionStatus.WAITING_SCAN.value


@respx.mock
async def test_start_login_forwards_environment():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(
            200,
            json={"login_session_id": SID, "status": "waiting_scan", "message": ""},
        )

    respx.post(f"{BASE}/session/login/start").mock(side_effect=_handler)
    await _client().start_login(
        "douyin",
        SessionEnvironment(proxy_url="http://u:p@proxy:8080", timezone_id="Asia/Tokyo"),
    )
    assert captured["body"]["environment"]["proxy_url"] == "http://u:p@proxy:8080"
    assert captured["body"]["environment"]["timezone_id"] == "Asia/Tokyo"


@respx.mock
async def test_start_login_without_session_id_is_a_bad_response():
    """没有 id 就无法轮询、更无法释放 —— 会永久泄漏一个有头浏览器 context。"""
    respx.post(f"{BASE}/session/login/start").mock(
        return_value=httpx.Response(
            200, json={"status": "waiting_scan", "qrcode_data_url": QR, "message": ""}
        )
    )
    snapshot = await _client().start_login("douyin")

    assert snapshot.success is False
    assert snapshot.result.error_kind == SessionErrorKind.BAD_RESPONSE.value


@respx.mock
async def test_start_login_prefers_the_typed_body_over_the_http_code():
    """浏览器侧起不来时回 502 + status=proxy_failed。只看 HTTP 码会把"代理不通"
    塌缩成基建失败，用户被告知"重扫二维码"——扫一百次也没用。"""
    respx.post(f"{BASE}/session/login/start").mock(
        return_value=httpx.Response(
            502,
            json={
                "success": False,
                "status": "proxy_failed",
                "message": "proxy handshake failed",
                "detail": {"reason": "proxy_connect"},
            },
        )
    )
    snapshot = await _client().start_login("douyin")

    assert snapshot.status == SessionStatus.PROXY_FAILED.value
    assert snapshot.message == "proxy handshake failed"
    # 这是一个**结论**，不是"没能问出结论"
    assert snapshot.is_infra_failure is False


@respx.mock
async def test_start_login_5xx_without_a_typed_body_stays_infra():
    respx.post(f"{BASE}/session/login/start").mock(
        return_value=httpx.Response(500, text="nginx boom")
    )
    snapshot = await _client().start_login("douyin")

    assert snapshot.is_infra_failure is True
    assert snapshot.result.error_kind == SessionErrorKind.SERVER_ERROR.value


@respx.mock
async def test_unauthorized_never_borrows_a_channel_status():
    """token 错配没有通道结论可言 —— 必须留在 unauthorized/基建失败一侧。"""
    respx.post(f"{BASE}/session/login/start").mock(
        return_value=httpx.Response(401, json={"status": "failed", "message": "nope"})
    )
    snapshot = await _client().start_login("douyin")

    assert snapshot.result.error_kind == SessionErrorKind.UNAUTHORIZED.value
    assert snapshot.is_infra_failure is True


@respx.mock
async def test_status_404_is_a_gone_session_not_a_blip():
    """404 == 那个 context 真没了，再轮询三轮也变不回来。"""
    respx.get(f"{BASE}/session/login/{SID}/status").mock(
        return_value=httpx.Response(
            404,
            json={
                "status": "failed",
                "message": "unknown login_session_id",
                "detail": {"reason": "unknown_login_session"},
            },
        )
    )
    snapshot = await _client().get_login_status(SID)

    assert snapshot.status == SessionStatus.FAILED.value
    assert snapshot.is_infra_failure is False
    assert snapshot.detail["reason"] == "unknown_login_session"


@respx.mock
async def test_start_login_unreachable_is_infra_failure():
    respx.post(f"{BASE}/session/login/start").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    snapshot = await _client().start_login("douyin")

    assert snapshot.status == SessionStatus.FAILED.value
    assert snapshot.is_infra_failure is True


async def test_start_login_not_configured_fails_loud():
    """token 缺失不降级为不鉴权调用 —— 该服务持有解密后的会话。"""
    snapshot = await BrowserClient(base_url=BASE, token="").start_login("douyin")
    assert snapshot.result.error_kind == SessionErrorKind.NOT_CONFIGURED.value
    assert snapshot.is_infra_failure is True


# ── /session/login/{id}/status ──────────────────────────────


@pytest.mark.parametrize(
    "status",
    ["waiting_scan", "scanned", "sms_required", "qrcode_expired", "success"],
)
@respx.mock
async def test_status_passes_through_every_pending_and_success_value(status):
    respx.get(f"{BASE}/session/login/{SID}/status").mock(
        return_value=httpx.Response(200, json={"status": status, "message": "m"})
    )
    snapshot = await _client().get_login_status(SID)

    assert snapshot.status == status
    # 进行中 + 成功都算"问出了结论"，不是失败
    assert snapshot.success is True
    assert snapshot.is_infra_failure is False
    # id 从请求路径回填，浏览器侧不重复回带也不丢
    assert snapshot.login_session_id == SID


@respx.mock
async def test_status_qrcode_expired_carries_the_refreshed_code():
    """刷新后的新码走同一个响应 —— 否则用户会一直盯着一张作废的图。"""
    respx.get(f"{BASE}/session/login/{SID}/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "qrcode_expired",
                "qrcode_data_url": "data:image/png;base64,NEW",
                "expires_at": "2026-08-05T01:35:00Z",
                "message": "QR refreshed",
            },
        )
    )
    snapshot = await _client().get_login_status(SID)
    assert snapshot.qrcode_data_url == "data:image/png;base64,NEW"


@pytest.mark.parametrize("status", ["timeout", "failed", "proxy_failed"])
@respx.mock
async def test_status_failure_values_are_business_answers_not_infra(status):
    respx.get(f"{BASE}/session/login/{SID}/status").mock(
        return_value=httpx.Response(200, json={"status": status, "message": "x"})
    )
    snapshot = await _client().get_login_status(SID)

    assert snapshot.success is False
    # 平台侧的结论，不是我们这边的基建故障 —— 两者的处置完全不同
    assert is_infra_failure(snapshot.result.to_dict()) is False


@respx.mock
async def test_status_unknown_value_is_rejected_not_guessed():
    respx.get(f"{BASE}/session/login/{SID}/status").mock(
        return_value=httpx.Response(200, json={"status": "session_valid"})
    )
    snapshot = await _client().get_login_status(SID)

    assert snapshot.status == SessionStatus.FAILED.value
    assert snapshot.result.error_kind == SessionErrorKind.BAD_RESPONSE.value


@respx.mock
async def test_status_server_error_is_infra_not_login_failure():
    """容器重启时手机上那次扫码可能已经成功 —— 不许据此判死。"""
    respx.get(f"{BASE}/session/login/{SID}/status").mock(
        return_value=httpx.Response(503, json={"detail": "restarting"})
    )
    snapshot = await _client().get_login_status(SID)

    assert snapshot.is_infra_failure is True
    assert snapshot.result.error_kind == SessionErrorKind.SERVER_ERROR.value


# ── /session/login/{id}/phone ───────────────────────────────


@respx.mock
async def test_submit_phone_sends_the_number_and_returns_the_next_state():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "status": "sms_required",
                "message": "the platform is asking for a verification code",
                "detail": {"code_requested": True, "phone_submitted": True},
            },
        )

    respx.post(f"{BASE}/session/login/{SID}/phone").mock(side_effect=_handler)
    snapshot = await _client().submit_login_phone(SID, "13800000000")

    assert captured["body"] == {"phone": "13800000000"}
    assert snapshot.status == SessionStatus.SMS_REQUIRED.value
    assert snapshot.success is True
    # 只有平台自己的发码按钮真被点到，才允许前端说"短信在路上"。
    assert snapshot.detail["code_requested"] is True


@respx.mock
async def test_a_phone_number_that_got_nowhere_is_not_a_successful_operation():
    """与上面那条"码被拒"墓碑同族，换成手机号这一步。

    ``phone_required`` 是进行中状态，所以"号码填不进去"天然会算 success=True ——
    前端于是走成功分支：清空输入框、什么都不说。而这个平台的每一个选择器都还没
    被任何一次真实绑定验证过，猜错时最不能做的就是**看起来像在等待**。
    """
    respx.post(f"{BASE}/session/login/{SID}/phone").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "phone_required",
                "message": "no phone number field was found on the sign-in page",
                "detail": {"reason": "phone_input_missing"},
            },
        )
    )
    snapshot = await _client().submit_login_phone(SID, "13800000000")

    assert snapshot.success is False
    # 平台没问题、账号没问题 —— 是我们的选择器。别让前端把它说成账号被限制。
    assert snapshot.is_infra_failure is False
    assert snapshot.detail["reason"] == "phone_input_missing"


async def test_submit_phone_refuses_an_empty_number_before_the_round_trip():
    with pytest.raises(ValueError):
        await _client().submit_login_phone(SID, "   ")


# ── /session/login/{id}/sms ─────────────────────────────────


@respx.mock
async def test_submit_sms_sends_code_and_returns_status():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"status": "scanned", "message": "accepted"})

    respx.post(f"{BASE}/session/login/{SID}/sms").mock(side_effect=_handler)
    snapshot = await _client().submit_login_sms(SID, "123456")

    assert captured["body"] == {"code": "123456"}
    assert snapshot.status == SessionStatus.SCANNED.value
    assert snapshot.message == "accepted"


@respx.mock
async def test_submit_sms_rejection_stays_sms_required():
    """码错了是业务结论（带 message），不是 HTTP 错误 —— 前端才好回显。"""
    respx.post(f"{BASE}/session/login/{SID}/sms").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "sms_required",
                "message": "code rejected",
                "detail": {"code_rejected": True},
            },
        )
    )
    snapshot = await _client().submit_login_sms(SID, "000000")

    assert snapshot.status == SessionStatus.SMS_REQUIRED.value
    assert snapshot.message == "code rejected"
    assert snapshot.is_infra_failure is False


@respx.mock
async def test_a_rejected_code_is_not_a_successful_operation():
    """**这条测试是"验证码输错界面零反馈"那个 bug 的墓碑。**

    ``sms_required`` 是进行中状态，而 ``success`` 由 status 推导 —— 于是"码
    被拒"曾经算 success=True：前端走成功分支，清空输入框，什么都不说。用户
    唯一能看到的变化是自己刚打的六位数没了。

    否决它的是 ``detail.code_rejected``（浏览器侧 ``submit_sms`` 打的标记）。
    去掉那个判断，这条立刻红 —— 它守的正是"用户动作失败必须有类型化回显"。
    """
    respx.post(f"{BASE}/session/login/{SID}/sms").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "sms_required",
                "message": "the verification code was not accepted",
                "detail": {"code_rejected": True, "submitted": True},
            },
        )
    )
    snapshot = await _client().submit_login_sms(SID, "000000")

    assert snapshot.success is False
    # 但它**不是**基建故障：平台确实答复了，账号也没问题。前端据此选文案，
    # 混淆的代价是让用户去查账号是不是被限制了。
    assert snapshot.is_infra_failure is False
    assert snapshot.detail["code_rejected"] is True
    # 标记必须原样传到前端 —— 它是那边区分"码没过"与"第一次要码"的唯一依据。
    assert snapshot.result.to_dict()["detail"]["code_rejected"] is True


@respx.mock
async def test_the_first_sms_challenge_is_still_a_successful_call():
    """另一半：没人提交过码，就不该被判成失败。

    ``_login_snapshot`` 是 /start、/status、/sms 共用的，所以"见到
    sms_required 就判 False"是错的 —— 轮询到平台开始要码是正常进展。
    """
    respx.get(f"{BASE}/session/login/{SID}/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "sms_required",
                "message": "the platform is asking for a verification code",
                "detail": {"reason": "asking for a code"},
            },
        )
    )
    snapshot = await _client().get_login_status(SID)

    assert snapshot.success is True


async def test_submit_sms_rejects_empty_code_as_programming_error():
    with pytest.raises(ValueError):
        await _client().submit_login_sms(SID, "  ")


# ── /session/login/{id}/state ───────────────────────────────


@respx.mock
async def test_get_login_state_returns_storage_state_and_identity():
    respx.get(f"{BASE}/session/login/{SID}/state").mock(
        return_value=httpx.Response(
            200,
            json={
                "storage_state": {"cookies": [{"name": "sessionid", "value": "s3"}]},
                "platform_user_id": "douyin-uid-1",
                "username": "Test Creator",
                "avatar_url": "https://cdn/a.png",
            },
        )
    )
    state = await _client().get_login_state(SID)

    assert state.success is True
    assert state.storage_state["cookies"][0]["name"] == "sessionid"
    assert state.platform_user_id == "douyin-uid-1"
    assert state.username == "Test Creator"


@respx.mock
async def test_get_login_state_carries_the_display_handle():
    """抖音号跟着一起回来 —— 它是显示字段,与身份键分家(mig 414)。"""
    respx.get(f"{BASE}/session/login/{SID}/state").mock(
        return_value=httpx.Response(
            200,
            json={
                "storage_state": {"cookies": [{"name": "sessionid", "value": "s3"}]},
                "platform_user_id": "41cf16775ee3e9fdf5e021f9c1ddfc12",
                "username": "MioPoo",
                "platform_handle": "miopoo",
            },
        )
    )
    state = await _client().get_login_state(SID)

    assert state.platform_user_id == "41cf16775ee3e9fdf5e021f9c1ddfc12"
    assert state.platform_handle == "miopoo"


@respx.mock
async def test_get_login_state_surfaces_identity_unresolved_as_the_reason():
    """浏览器认得这一页、也真的登录了,只是拿不到身份 cookie,于是 409 + 类型化
    结论。丢掉 body 会把它压成 "browser service returned HTTP 409" —— 用户看到
    一个数字,不知道该重扫还是该报障(§7.8 / 触发路径必须类型化失败回显)。"""
    respx.get(f"{BASE}/session/login/{SID}/state").mock(
        return_value=httpx.Response(
            409,
            json={
                "success": False,
                "status": "failed",
                "message": (
                    "cannot identify the douyin account: cookie 'uid_tt' was not "
                    "present after login"
                ),
                "detail": {
                    "reason": "identity_unresolved",
                    "identity_cookie": "uid_tt",
                    "terminal": True,
                },
            },
        )
    )
    state = await _client().get_login_state(SID)

    assert state.success is False
    assert state.status == SessionStatus.FAILED.value
    assert state.result.detail["reason"] == "identity_unresolved"
    assert "uid_tt" in state.result.message
    # 不是基建故障:重试同一个 session 不会有别的结果,别把它归到"我们这边挂了"。
    assert state.result.is_infra_failure is False


@respx.mock
async def test_get_login_state_without_storage_state_fails():
    """扫了码却没有会话物料 == 白扫。绝不能建一个空 session_state 的账号行。"""
    respx.get(f"{BASE}/session/login/{SID}/state").mock(
        return_value=httpx.Response(200, json={"platform_user_id": "u1"})
    )
    state = await _client().get_login_state(SID)

    assert state.success is False
    assert state.result.error_kind == SessionErrorKind.BAD_RESPONSE.value


@respx.mock
async def test_get_login_state_without_platform_user_id_fails():
    """没有平台 id 就没有 upsert 的自然键 —— 重扫会建出第二行账号。"""
    respx.get(f"{BASE}/session/login/{SID}/state").mock(
        return_value=httpx.Response(200, json={"storage_state": {"cookies": []}})
    )
    state = await _client().get_login_state(SID)

    assert state.success is False
    assert state.result.error_kind == SessionErrorKind.BAD_RESPONSE.value


@respx.mock
async def test_login_state_repr_never_leaks_storage_state():
    """repr 是最容易的泄漏路径：loguru 的 f-string 会把它带进日志（§7.6）。"""
    respx.get(f"{BASE}/session/login/{SID}/state").mock(
        return_value=httpx.Response(
            200,
            json={
                "storage_state": {
                    "cookies": [{"name": "sessionid", "value": "s3cr3t"}]
                },
                "platform_user_id": "u1",
                "username": "Test Creator",
            },
        )
    )
    state = await _client().get_login_state(SID)
    assert "s3cr3t" not in repr(state)
    assert "storage_state=set" in repr(state)


@respx.mock
async def test_login_snapshot_repr_never_dumps_the_qrcode():
    respx.get(f"{BASE}/session/login/{SID}/status").mock(
        return_value=httpx.Response(
            200, json={"status": "waiting_scan", "qrcode_data_url": QR}
        )
    )
    snapshot = await _client().get_login_status(SID)
    assert "base64" not in repr(snapshot)
    assert "qrcode=set" in repr(snapshot)


# ── /session/login/{id}/close ───────────────────────────────


@respx.mock
async def test_close_login_confirms_release():
    route = respx.post(f"{BASE}/session/login/{SID}/close").mock(
        return_value=httpx.Response(200, json={"closed": True})
    )
    assert await _client().close_login(SID) is True
    assert route.called


@respx.mock
async def test_close_login_never_raises_on_transport_failure():
    """它总是在 finally 里被调用 —— 清理失败不得盖掉真实错误。"""
    respx.post(f"{BASE}/session/login/{SID}/close").mock(
        side_effect=httpx.ConnectError("gone")
    )
    assert await _client().close_login(SID) is False


@respx.mock
async def test_close_login_treats_an_unknown_session_as_released():
    """TTL 自毁过、或 workflow 的 finally 先关过一次 —— 那就是"已释放"，
    报"释放未确认"只会让取消端点对用户说一句吓人的话。"""
    respx.post(f"{BASE}/session/login/{SID}/close").mock(
        return_value=httpx.Response(
            404, json={"status": "failed", "message": "unknown login_session_id"}
        )
    )
    assert await _client().close_login(SID) is True


async def test_close_login_not_configured_returns_false():
    assert await BrowserClient(base_url=BASE, token="").close_login(SID) is False
