"""``BrowserClient.inspect_page`` —— 只读勘探的传输层（T0）。

守两件事：

1. **白名单拒绝必须原样穿回来。** 浏览器侧对越界 URL 回 HTTP 400 + 一个完整
   的 §7.8 信封（``reason=url_not_allowed``）。把它降级成 "browser service
   returned HTTP 400" 会让调用方失去唯一能告诉用户"你给的地址不在允许范围"
   的那句话 —— 这正是本仓库反复强调的「类型化失败不得降级」。
2. **非法 status 不猜。** 勘探的合法 status 只有五个；出现别的值一律按坏响应
   处理，而不是当成成功。
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.services.distribution.browser_client import (
    INTERNAL_TOKEN_HEADER,
    BrowserClient,
    SessionEnvironment,
    SessionErrorKind,
    SessionStatus,
)

BASE = "http://nous-browser:8090"
URL = "https://creator.douyin.com/creator-micro/content/upload"
STORAGE_STATE = {"cookies": [{"name": "sessionid", "value": "s3cr3t"}], "origins": []}


def _client() -> BrowserClient:
    return BrowserClient(base_url=BASE, token="tok_test")


def _ok_body(**overrides) -> dict:
    body = {
        "success": True,
        "status": "session_valid",
        "message": "page read",
        "detail": {"stage": "observe", "platform": "douyin"},
        "url_after": URL,
        "page_title": "创作服务平台",
        "texts": {"定时发布": {"exact": 1, "exact_visible": 1, "substring": 2}},
        "selectors": {'input[type="file"]': {"total": 4, "visible": 0}},
        "body_text_excerpt": "…",
        "body_text_truncated": True,
        "input_summary": [{"tag": "input", "type": "file", "multiple": True}],
        "input_total": 9,
        "seeded_files": ["probe-image-1.jpg"],
        "updated_storage_state": {"cookies": [{"name": "sessionid", "value": "new"}]},
    }
    body.update(overrides)
    return body


@respx.mock
async def test_a_successful_read_maps_to_success_and_keeps_the_counts():
    route = respx.post(f"{BASE}/session/inspect").mock(
        return_value=httpx.Response(200, json=_ok_body())
    )
    result = await _client().inspect_page("douyin", STORAGE_STATE, URL)

    assert route.called
    assert result.success is True
    assert result.status == SessionStatus.SESSION_VALID.value
    assert result.observation["texts"]["定时发布"]["exact"] == 1
    assert result.observation["input_total"] == 9
    assert result.updated_storage_state == {
        "cookies": [{"name": "sessionid", "value": "new"}]
    }


@respx.mock
async def test_the_request_carries_the_token_and_the_full_envelope():
    captured: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ok_body())

    respx.post(f"{BASE}/session/inspect").mock(side_effect=_handler)
    await _client().inspect_page(
        "douyin",
        STORAGE_STATE,
        URL,
        environment=SessionEnvironment(user_agent="UA"),
        text_probes=["允许"],
        selector_probes=["div"],
        seed_files=[{"kind": "image", "url": "http://x/a.jpg", "filename": "a.jpg"}],
        options={"settle_ms": 6000, "budget_s": None},
    )

    assert captured["headers"][INTERNAL_TOKEN_HEADER] == "tok_test"
    body = captured["body"]
    assert body["url"] == URL
    assert body["text_probes"] == ["允许"]
    assert body["seed_files"][0]["filename"] == "a.jpg"
    assert body["environment"]["user_agent"] == "UA"
    # None 的旋钮不发出去 —— 让浏览器侧的默认值生效，而不是发一个 null 过去
    # 把对面的默认覆盖掉。
    assert body["settle_ms"] == 6000
    assert "budget_s" not in body


@respx.mock
async def test_an_out_of_allow_list_url_comes_back_as_a_conclusion_not_a_transport_error():
    """浏览器侧的 400 带着完整信封。降级它 = 丢掉唯一能解释拒绝原因的那句话。"""
    respx.post(f"{BASE}/session/inspect").mock(
        return_value=httpx.Response(
            400,
            json={
                "success": False,
                "status": "failed",
                "message": "host 'example.com' is outside this platform's creator hosts",
                "detail": {
                    "reason": "url_not_allowed",
                    "allowed_hosts": ["creator.douyin.com"],
                },
            },
        )
    )
    result = await _client().inspect_page(
        "douyin", STORAGE_STATE, "https://example.com/"
    )

    assert result.success is False
    assert result.result.detail["reason"] == "url_not_allowed"
    assert result.result.detail["allowed_hosts"] == ["creator.douyin.com"]
    assert "HTTP 400" not in result.result.message


@respx.mock
async def test_an_unreachable_container_is_infra_not_a_verdict():
    respx.post(f"{BASE}/session/inspect").mock(side_effect=httpx.ConnectError("nope"))
    result = await _client().inspect_page("douyin", STORAGE_STATE, URL)

    assert result.success is False
    assert result.result.detail["error_kind"] == SessionErrorKind.UNREACHABLE.value


@respx.mock
async def test_an_illegal_status_is_not_guessed():
    respx.post(f"{BASE}/session/inspect").mock(
        return_value=httpx.Response(200, json=_ok_body(status="published"))
    )
    result = await _client().inspect_page("douyin", STORAGE_STATE, URL)

    assert result.success is False
    assert result.result.detail["error_kind"] == SessionErrorKind.BAD_RESPONSE.value


@respx.mock
async def test_an_empty_updated_state_is_treated_as_absent():
    """空对象写回库等于抹掉账号会话，比不写坏得多。"""
    respx.post(f"{BASE}/session/inspect").mock(
        return_value=httpx.Response(200, json=_ok_body(updated_storage_state={}))
    )
    result = await _client().inspect_page("douyin", STORAGE_STATE, URL)
    assert result.updated_storage_state is None


async def test_bad_arguments_raise_rather_than_returning_a_fake_verdict():
    with pytest.raises(ValueError):
        await _client().inspect_page("douyin", {}, URL)
    with pytest.raises(ValueError):
        await _client().inspect_page("douyin", STORAGE_STATE, "   ")


async def test_the_repr_never_carries_the_page_or_the_session():
    from app.services.distribution.browser_client import InspectResult, SessionOpResult

    result = InspectResult(
        result=SessionOpResult(True, "session_valid", "ok"),
        observation={"body_text_excerpt": "SECRET PAGE TEXT"},
        updated_storage_state={"cookies": [{"name": "sessionid", "value": "s3cr3t"}]},
    )
    text = repr(result)
    assert "SECRET PAGE TEXT" not in text
    assert "s3cr3t" not in text
