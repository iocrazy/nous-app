"""`/session/probe` 的入口契约 —— 与 `/session/inspect` 同款，同一批闸。

守的还是"在浏览器存在**之前**就该发生"的那三件事：认证、平台覆盖、URL 白名
单。最后一条不只断言返回 400，还断言**根本没去访问**（`run_probe` 换成一枚
地雷）—— 这个端点带着某个真实账号的 cookie，白名单失守就是一次带凭证的
SSRF，"返回了 400"和"没有发出请求"不是同一件事。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.schemas import SessionStatus
from tests.conftest import TEST_TOKEN

pytestmark = pytest.mark.unit

LIVE_STATE = {"cookies": [{"name": "sessionid", "value": "x"}], "origins": []}
CREATOR_URL = "https://creator.douyin.com/creator-micro/content/upload"
EDITOR_SELECTOR = '[contenteditable="true"][data-placeholder*="添加作品描述"]'


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def landmine(monkeypatch):
    """Explodes if anything downstream of the gates ever runs."""
    calls = []

    async def _boom(spec, request):  # pragma: no cover - the point is not to run
        calls.append(request.url)
        raise AssertionError(f"a browser run was started for {request.url}")

    monkeypatch.setattr(main, "run_probe", _boom)
    return calls


def _body(**overrides):
    payload = {
        "platform": "douyin",
        "storage_state": LIVE_STATE,
        "url": CREATOR_URL,
        "probe_text": "#南通",
        "target_selectors": [EDITOR_SELECTOR],
    }
    payload.update(overrides)
    return payload


def _post(client, **overrides):
    return client.post(
        "/session/probe",
        json=_body(**overrides),
        headers={"X-Internal-Token": TEST_TOKEN},
    )


def test_requires_the_internal_token(client):
    resp = client.post("/session/probe", json=_body())
    assert resp.status_code in (401, 403)


def test_a_platform_without_a_recon_target_is_refused(client, landmine):
    resp = _post(client, platform="bilibili")
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == "not_supported"
    assert landmine == []


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://www.douyin.com/",
        "http://creator.douyin.com/",
        "https://creator.douyin.com@evil.example/",
    ],
)
def test_a_url_outside_the_creator_hosts_is_refused_without_fetching(
    client, landmine, url
):
    resp = _post(client, url=url)
    assert resp.status_code == 400
    body = resp.json()["detail"]
    assert body["reason"] == "url_not_allowed"
    assert body["allowed_hosts"] == ["creator.douyin.com"]
    # 没有发起过任何浏览器运行 —— 这才是这条闸的安全属性。
    assert landmine == []


def test_the_url_gate_is_the_same_function_the_read_only_recon_uses(client):
    """两个端点各留一份白名单，迟早会分叉成"一个端点能开、另一个不能"。"""
    from app import inspect as inspect_module

    assert main.url_refusal is inspect_module.url_refusal


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_selectors": []},
        {"probe_text": ""},
        {"probe_text": "x" * 65},
        {"keystroke_delay_ms": 9_000},
        {"max_captures": 999},
        # 白名单外的标签在**浏览器存在之前**就被拒。这一条跟 URL 白名单同级：
        # 返回 422 与"没有起过浏览器"不是同一件事，地雷断言的是后者。
        {"pre_steps": [{"label": "发布"}]},
        {"post_steps": [{"label": "确定"}]},
        {"pre_steps": [{"label": "选择音乐", "candidates": 99}]},
        {"replay_url_contains": ["x"] * 40},
    ],
)
def test_the_callers_limits_are_enforced_by_the_schema(client, landmine, overrides):
    resp = _post(client, **overrides)
    assert resp.status_code == 422
    assert landmine == []


def test_an_allow_listed_step_is_accepted(client, monkeypatch):
    """反面对照：同一个字段，词表里的标签必须能过 —— 否则上面那组 422 什么也
    没证明（一个永远拒绝的字段同样让它们全绿）。"""
    from app.schemas import ProbeResponse

    seen: list = []

    async def _fake(spec, request):
        seen.append([step.label for step in request.pre_steps])
        return ProbeResponse(
            success=True, status=SessionStatus.SESSION_VALID, message="ok"
        )

    monkeypatch.setattr(main, "run_probe", _fake)
    resp = _post(
        client,
        pre_steps=[{"label": "选择音乐", "until_selectors": ['input[placeholder*="搜索音乐"]']}],
        post_steps=[{"label": "热门榜"}],
    )
    assert resp.status_code == 200
    assert seen == [["选择音乐"]]


def test_a_healthy_request_reaches_the_runner(client, monkeypatch):
    from app.schemas import ProbeResponse

    seen: list = []

    async def _fake(spec, request):
        seen.append((spec.platform, request.probe_text))
        return ProbeResponse(
            success=True,
            status=SessionStatus.SESSION_VALID,
            message="typed probe complete",
            typed_text_landed=True,
        )

    monkeypatch.setattr(main, "run_probe", _fake)
    resp = _post(client)

    assert resp.status_code == 200
    assert resp.json()["typed_text_landed"] is True
    assert seen == [("douyin", "#南通")]
