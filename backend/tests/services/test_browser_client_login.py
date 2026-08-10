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
        "sms_required",
        "success",
        "timeout",
        "proxy_failed",
        "failed",
    }


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
    # environment 的 6 个键全部显式发出（与 /validate 同款契约）
    assert set(captured["body"]["environment"]) == {
        "proxy_url",
        "user_agent",
        "locale",
        "timezone_id",
        "geo_lat",
        "geo_lng",
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
            200, json={"status": "sms_required", "message": "code rejected"}
        )
    )
    snapshot = await _client().submit_login_sms(SID, "000000")

    assert snapshot.status == SessionStatus.SMS_REQUIRED.value
    assert snapshot.message == "code rejected"
    assert snapshot.is_infra_failure is False


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
