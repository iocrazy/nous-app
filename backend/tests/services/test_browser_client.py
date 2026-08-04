"""BrowserClient —— nous-browser 内网客户端的传输层与结果映射。

重点覆盖 spec §7.8 的类型化失败：每种传输故障都要落到一个明确的
``error_kind``，且**基建失败与"账号真掉线"必须可区分**（巡检会据此决定
要不要把账号标 needs_relogin）。
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.distribution.browser_client import (
    INTERNAL_TOKEN_HEADER,
    BrowserClient,
    SessionEnvironment,
    SessionErrorKind,
    SessionStatus,
    is_infra_failure,
)

BASE = "http://nous-browser:8090"
STORAGE_STATE = {"cookies": [{"name": "sessionid", "value": "s3cr3t"}], "origins": []}


def _client(**kw) -> BrowserClient:
    kw.setdefault("base_url", BASE)
    kw.setdefault("token", "tok_test")
    return BrowserClient(**kw)


# ── /session/validate 成功路径 ───────────────────────────────


@respx.mock
async def test_validate_session_valid_maps_to_success():
    route = respx.post(f"{BASE}/session/validate").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "status": "session_valid",
                "message": "ok",
                "detail": {"username": "Test Creator"},
            },
        )
    )
    result = await _client().validate_session("douyin", STORAGE_STATE)

    assert route.called
    assert result.success is True
    assert result.status == SessionStatus.SESSION_VALID.value
    assert result.detail["username"] == "Test Creator"
    assert result.is_infra_failure is False


@respx.mock
async def test_validate_sends_token_header_and_full_environment():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["headers"] = request.headers
        captured["body"] = _json.loads(request.content)
        return httpx.Response(
            200, json={"success": True, "status": "session_valid", "message": ""}
        )

    respx.post(f"{BASE}/session/validate").mock(side_effect=_handler)
    env = SessionEnvironment(
        proxy_url="http://user:pw@proxy:8080",
        user_agent="UA/1",
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        geo_lat=39.9,
        geo_lng=116.4,
    )
    await _client().validate_session("douyin", STORAGE_STATE, env)

    assert captured["headers"][INTERNAL_TOKEN_HEADER] == "tok_test"
    body = captured["body"]
    assert body["platform"] == "douyin"
    # storage_state 明文原样透传（解密是 backend 的责任，browser 不碰密钥）
    assert body["storage_state"] == STORAGE_STATE
    # 契约固定的 6 个 environment 键必须全部显式发出（含 null）
    assert set(body["environment"]) == {
        "proxy_url",
        "user_agent",
        "locale",
        "timezone_id",
        "geo_lat",
        "geo_lng",
    }
    assert body["environment"]["geo_lat"] == 39.9


@respx.mock
async def test_validate_defaults_environment_when_omitted():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(
            200, json={"success": False, "status": "session_invalid", "message": ""}
        )

    respx.post(f"{BASE}/session/validate").mock(side_effect=_handler)
    await _client().validate_session("douyin", STORAGE_STATE)

    env = captured["body"]["environment"]
    assert env["proxy_url"] is None
    assert env["locale"] == "zh-CN"
    assert env["timezone_id"] == "Asia/Shanghai"


@respx.mock
async def test_validate_session_invalid_is_a_business_answer_not_infra():
    """账号真掉线 —— 没有 error_kind，巡检据此才敢标 needs_relogin。"""
    respx.post(f"{BASE}/session/validate").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": False,
                "status": "session_invalid",
                "message": "redirected to login",
                "detail": {},
            },
        )
    )
    result = await _client().validate_session("douyin", STORAGE_STATE)

    assert result.success is False
    assert result.status == SessionStatus.SESSION_INVALID.value
    assert result.error_kind is None
    assert is_infra_failure(result.to_dict()) is False


@respx.mock
async def test_validate_success_is_derived_from_status_not_the_flag():
    """浏览器侧两个字段写不一致时以 status 为准 —— 单一真相源。"""
    respx.post(f"{BASE}/session/validate").mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "status": "session_invalid", "message": "oops"},
        )
    )
    result = await _client().validate_session("douyin", STORAGE_STATE)
    assert result.success is False


@respx.mock
async def test_validate_proxy_failed_passes_through():
    respx.post(f"{BASE}/session/validate").mock(
        return_value=httpx.Response(
            200,
            json={"success": False, "status": "proxy_failed", "message": "407"},
        )
    )
    result = await _client().validate_session("douyin", STORAGE_STATE)
    assert result.status == SessionStatus.PROXY_FAILED.value
    # 平台/代理侧的业务结论，不是我们这边的基建故障
    assert is_infra_failure(result.to_dict()) is False


# ── 传输失败：每种一个 error_kind ───────────────────────────


@respx.mock
async def test_validate_unauthorized_is_typed_and_infra():
    respx.post(f"{BASE}/session/validate").mock(
        return_value=httpx.Response(401, json={"detail": "bad token"})
    )
    result = await _client().validate_session("douyin", STORAGE_STATE)

    assert result.status == SessionStatus.FAILED.value
    assert result.error_kind == SessionErrorKind.UNAUTHORIZED.value
    assert result.detail["status_code"] == 401
    # token 错配是基建问题 —— 绝不能把账号判死
    assert is_infra_failure(result.to_dict()) is True


@respx.mock
async def test_validate_connection_error_is_unreachable():
    respx.post(f"{BASE}/session/validate").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    result = await _client().validate_session("douyin", STORAGE_STATE)

    assert result.status == SessionStatus.FAILED.value
    assert result.error_kind == SessionErrorKind.UNREACHABLE.value
    assert is_infra_failure(result.to_dict()) is True


@respx.mock
async def test_validate_timeout_maps_to_timeout_status():
    respx.post(f"{BASE}/session/validate").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    result = await _client(validate_timeout=90.0).validate_session(
        "douyin", STORAGE_STATE
    )

    # timeout 在 §7.8 里有对外同名 status，同时也是基建失败
    assert result.status == SessionStatus.TIMEOUT.value
    assert result.error_kind == SessionErrorKind.TIMEOUT.value
    assert result.detail["timeout_seconds"] == 90.0
    assert is_infra_failure(result.to_dict()) is True


@respx.mock
async def test_validate_server_error_is_typed():
    respx.post(f"{BASE}/session/validate").mock(
        return_value=httpx.Response(503, text="upstream down")
    )
    result = await _client().validate_session("douyin", STORAGE_STATE)
    assert result.error_kind == SessionErrorKind.SERVER_ERROR.value
    assert result.detail["status_code"] == 503


@respx.mock
async def test_validate_non_json_body_is_bad_response():
    respx.post(f"{BASE}/session/validate").mock(
        return_value=httpx.Response(200, text="<html>nginx</html>")
    )
    result = await _client().validate_session("douyin", STORAGE_STATE)
    assert result.error_kind == SessionErrorKind.BAD_RESPONSE.value


@respx.mock
async def test_validate_unknown_status_value_fails_loud():
    """非法 status 绝不"猜一个"——猜成 valid 会放行一个死账号,
    猜成 invalid 会把好账号判死。"""
    respx.post(f"{BASE}/session/validate").mock(
        return_value=httpx.Response(
            200, json={"success": True, "status": "totally_new_state", "message": ""}
        )
    )
    result = await _client().validate_session("douyin", STORAGE_STATE)

    assert result.status == SessionStatus.FAILED.value
    assert result.error_kind == SessionErrorKind.BAD_RESPONSE.value
    assert is_infra_failure(result.to_dict()) is True


# ── 未配置 / 参数校验 ────────────────────────────────────────


async def test_validate_without_token_never_calls_unauthenticated():
    """token 缺失时 fail loud，不降级为裸奔调用（服务持有解密后的会话）。"""
    with respx.mock:
        route = respx.post(f"{BASE}/session/validate")
        result = await _client(token="").validate_session("douyin", STORAGE_STATE)

    assert route.called is False
    assert result.error_kind == SessionErrorKind.NOT_CONFIGURED.value
    assert "BROWSER_INTERNAL_TOKEN" in result.message


async def test_validate_without_base_url_is_not_configured():
    result = await _client(base_url="").validate_session("douyin", STORAGE_STATE)
    assert result.error_kind == SessionErrorKind.NOT_CONFIGURED.value
    assert "BROWSER_SERVICE_URL" in result.message


@pytest.mark.parametrize("bad", [None, {}, "cookie=1", []])
async def test_validate_rejects_non_object_storage_state(bad):
    # 编程错误 → raise；运行时状态 → 类型化结果。两者不混。
    with pytest.raises(ValueError):
        await _client().validate_session("douyin", bad)


async def test_environment_repr_never_leaks_proxy_credentials():
    env = SessionEnvironment(proxy_url="http://user:hunter2@proxy.example:8080")
    assert "hunter2" not in repr(env)
    assert "proxy.example" not in repr(env)


# ── /healthz ────────────────────────────────────────────────


@respx.mock
async def test_health_ok_requires_browser_ready():
    respx.get(f"{BASE}/healthz").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "browser_ready": True,
                "xvfb": True,
                "version": "1.2.3",
            },
        )
    )
    health = await _client().health()
    assert health.ok is True
    assert health.xvfb is True
    assert health.version == "1.2.3"


@respx.mock
async def test_health_not_ok_when_browser_not_ready():
    """探针必须探真信号：进程活着 ≠ 浏览器能用（CLAUDE.md 验收纪律）。"""
    respx.get(f"{BASE}/healthz").mock(
        return_value=httpx.Response(
            200,
            json={"status": "ok", "browser_ready": False, "xvfb": True, "version": "1"},
        )
    )
    health = await _client().health()
    assert health.ok is False
    assert health.browser_ready is False


@respx.mock
async def test_health_unreachable_is_typed_not_raised():
    respx.get(f"{BASE}/healthz").mock(side_effect=httpx.ConnectError("nope"))
    health = await _client().health()
    assert health.ok is False
    assert health.error_kind == SessionErrorKind.UNREACHABLE.value


async def test_health_not_configured():
    health = await BrowserClient(base_url="", token="").health()
    assert health.ok is False
    assert health.error_kind == SessionErrorKind.NOT_CONFIGURED.value
