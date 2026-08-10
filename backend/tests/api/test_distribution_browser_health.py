"""``GET /distribution/browser/health`` —— 绑定入口的前置门（D1）。

每一次扫码绑定都跑在 ``nous-browser`` 里。那个容器重启（一次后端部署就会）
或宕掉的窗口里，点「QR Code Login」是**必然失败**的，但用户要等到扫码弹窗
打开、卡在 "Starting..." 直到超时才知道。这个端点把那句话提前说。

所以这里钉的是四件事：

* 不健康是 **200 + ``ok: false``**，不是 5xx —— 前端要把"浏览器不可用"和
  "这个请求本身失败了"分开处理，后者不构成任何关于浏览器的证据；
* ``ok`` 只能来自一次**真的探测成功**。没有硬编码 healthy 的路径（仓库有
  ``/health`` 返回字面量 healthy 而 DBOS 死了三天的疤）；
* 探测**必须带秒级超时** —— 有人在等这个答案，它要比它替代的那次失败更快；
* 走和隔壁端点同一个 ACCESS 门（模块关掉 → 404）与同一套认证。

⚠️ 这个端点**故意不接进** ``/api/v1/readyz``：那是 ``deploy-gpu.yml`` 的
smoke 门禁，把兄弟容器的健康度接进去，等于 browser 每次重启都把 backend 判成
not-ready，然后 smoke 失败自动回滚整次发布。
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.api.distribution_router as dr
from app.services.distribution.browser_client import SessionErrorKind

_USER = "11111111-1111-1111-1111-111111111111"
_BASE = "http://nous-browser:8090"
_PATH = "/api/v1/distribution/browser/health"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = lambda: None
    app.dependency_overrides[dr.get_current_user] = lambda: {"id": _USER}
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app())


@pytest.fixture(autouse=True)
def configured_browser(monkeypatch):
    """让 BrowserClient 认为服务已配置 —— 否则每个用例都短路在
    ``not_configured``，永远走不到传输层，等于什么都没测。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", _BASE, raising=False)
    monkeypatch.setattr(settings, "BROWSER_INTERNAL_TOKEN", "tok_test", raising=False)


def _healthz(**overrides) -> dict:
    body = {"status": "ok", "browser_ready": True, "xvfb": True, "version": "1.2.3"}
    body.update(overrides)
    return body


@respx.mock
def test_healthy_browser_reports_ok(client):
    route = respx.get(f"{_BASE}/healthz").mock(
        return_value=httpx.Response(200, json=_healthz())
    )

    resp = client.get(_PATH)

    # 探测真的发生了 —— 否则下面那个 ok:true 只是端点在自说自话。
    assert route.called
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "error_kind": None, "message": "ok"}


@respx.mock
def test_unreachable_browser_is_200_with_typed_unhealthy(client):
    """容器没起来 → 端点**正常返回**，把原因说清楚。

    换成 500/503 的话，前端只能看到"请求失败"，而那和"后端自己也不通"长得
    一模一样 —— 恰恰是这个门要区分的两件事。
    """
    respx.get(f"{_BASE}/healthz").mock(side_effect=httpx.ConnectError("no route"))

    resp = client.get(_PATH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error_kind"] == SessionErrorKind.UNREACHABLE.value


@respx.mock
def test_browser_degraded_503_is_reported_not_propagated(client):
    """浏览器侧 degraded 时回的是 HTTP 503（它的 healthcheck 契约）。

    那个 503 **不能**原样冒出去变成本端点的状态码 —— 见上一条。
    """
    respx.get(f"{_BASE}/healthz").mock(
        return_value=httpx.Response(
            503, json=_healthz(status="degraded", browser_ready=False)
        )
    )

    resp = client.get(_PATH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error_kind"] == SessionErrorKind.SERVER_ERROR.value


@respx.mock
def test_process_alive_but_no_chromium_is_not_ok(client):
    """``browser_ready=False`` 时 200 也不算健康。

    进程活着 ≠ 浏览器能用。这一档如果放行，门就退化成 liveness ping ——
    正是 CLAUDE.md「验收纪律」里那三天全绿的形状。
    """
    respx.get(f"{_BASE}/healthz").mock(
        return_value=httpx.Response(200, json=_healthz(browser_ready=False))
    )

    resp = client.get(_PATH)

    assert resp.status_code == 200
    assert resp.json()["ok"] is False


@respx.mock
def test_not_configured_is_unhealthy_not_a_silent_pass(client, monkeypatch):
    """没配 URL/token 时，绑定同样必然失败，所以这也是"不健康"。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "BROWSER_SERVICE_URL", "", raising=False)
    route = respx.get(f"{_BASE}/healthz").mock(
        return_value=httpx.Response(200, json=_healthz())
    )

    resp = client.get(_PATH)

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error_kind"] == SessionErrorKind.NOT_CONFIGURED.value
    # 没配就根本不该发请求（而且发了也不知道往哪发）。
    assert not route.called


@respx.mock
def test_probe_uses_a_seconds_scale_timeout(client, monkeypatch):
    """有人在等这个答案：探测超时必须是秒级，不能继承发布/校验那档。

    直接钉构造参数而不是钉常量的值 —— 常量改了但没传进去，是这类"配置写了
    却没生效"的经典形状。
    """
    captured: dict = {}

    # 端点里是惰性 import，所以要打在源模块上而不是 ``dr`` 上。
    import app.services.distribution.browser_client as bc

    original = bc.BrowserClient

    class _Recording(original):  # type: ignore[misc,valid-type]
        def __init__(self, **kw):
            captured.update(kw)
            super().__init__(**kw)

    monkeypatch.setattr(bc, "BrowserClient", _Recording)
    respx.get(f"{_BASE}/healthz").mock(
        return_value=httpx.Response(200, json=_healthz())
    )

    assert client.get(_PATH).status_code == 200
    assert captured["health_timeout"] <= 5.0
    # connect 也要钉：容器整个消失时最常见的形状是连不上而不是连上后不回，
    # 只钉 read 的话最坏情况是 connect 默认值 + read。
    assert captured["connect_timeout"] <= 5.0


def test_health_gate_is_behind_the_module_access_switch(client):
    """模块关掉 → 404，和没注册过这条路由一样（隔壁端点同款）。"""
    app = _make_app()

    def _off():
        raise HTTPException(status_code=404, detail="Not found")

    app.dependency_overrides[dr.require_distribution] = _off
    assert TestClient(app).get(_PATH).status_code == 404


@respx.mock
def test_health_gate_requires_authentication():
    """匿名调用拿不到答案 —— 这是内网基建状态，不是公开 status page。

    状态码是 422 而不是 401：``get_current_user`` 把 ``Authorization`` 声明成
    必填 Header，缺了它 FastAPI 在依赖解析阶段就拒了（全站同一形状）。这里钉
    的是"不是 200 且根本没去探浏览器"，不是那个具体数字。
    """
    app = FastAPI()
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = lambda: None
    route = respx.get(f"{_BASE}/healthz").mock(
        return_value=httpx.Response(200, json=_healthz())
    )

    assert TestClient(app).get(_PATH).status_code != 200
    assert not route.called
