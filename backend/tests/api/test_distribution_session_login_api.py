"""会话通道扫码登录的 REST 面（S2, spec §4.1）。

覆盖三类必须成立的东西：

1. **模块开关 fail-closed** —— 三个端点都在 ``require_distribution`` 后面。
2. **scope 绑定的 IDOR 口径与 OAuth 通道逐字相同** —— 一个 session 账号和一个
   OAuth 账号一样能发布，新端点上放松检查就是整个洞。
3. **类型化失败回显** —— 验证码错、context 还没起来、任务已终态，三种情况
   给三种可分辨的答案，不是 500 也不是静默成功。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.api.distribution_router as dr

_USER = "11111111-1111-1111-1111-111111111111"
SID = "ls_abc123"


def _make_app(*, module_on=True) -> FastAPI:
    async def fake_require():
        if not module_on:
            raise HTTPException(status_code=404, detail="Not found")

    app = FastAPI()
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = fake_require
    app.dependency_overrides[dr.get_current_user] = lambda: {"id": _USER}
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app())


@pytest.fixture
def configured_browser(monkeypatch):
    """BrowserClient 打桩：默认已配置，方法由各用例覆盖。"""
    fake = MagicMock()
    fake.is_configured = True
    fake.submit_login_sms = AsyncMock()
    fake.close_login = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.services.distribution.browser_client.BrowserClient", lambda: fake
    )
    return fake


def _login_body(**over) -> dict:
    body = {"platform": "douyin", "scope_type": "user", "scope_id": _USER}
    body.update(over)
    return body


# ── POST /accounts/session/login ────────────────────────────


def test_start_login_is_behind_the_module_switch():
    app = _make_app(module_on=False)
    resp = TestClient(app).post(
        "/api/v1/distribution/accounts/session/login", json=_login_body()
    )
    assert resp.status_code == 404


def test_start_login_creates_task_and_dispatches_same_workflow_id(
    client, configured_browser, monkeypatch
):
    manager = MagicMock()
    manager.create = AsyncMock(return_value="wf")
    dispatched: dict = {}

    async def fake_dispatch(task_type, **kw):
        dispatched["task_type"] = task_type
        dispatched.update(kw)
        return {"mode": "dbos"}

    monkeypatch.setattr(dr, "get_task_manager", lambda: manager)
    monkeypatch.setattr(dr, "start_workflow_routed", fake_dispatch)

    resp = client.post(
        "/api/v1/distribution/accounts/session/login", json=_login_body()
    )

    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    # 路线 C：task_tracking 行与 workflow 用同一个 id，否则前端订阅不到
    assert manager.create.await_args.kwargs["dbos_workflow_id"] == task_id
    assert dispatched["workflow_id"] == task_id
    assert dispatched["task_type"] == dr.SESSION_LOGIN_TASK_TYPE
    assert dispatched["dbos_workflow_kwargs"]["platform"] == "douyin"
    # 建行时就带上 login blob，二维码还没来之前 modal 也有东西渲染
    assert manager.create.await_args.kwargs["metadata"]["login"]["platform"] == "douyin"


def test_start_login_forces_user_scope_to_the_caller(
    client, configured_browser, monkeypatch
):
    """IDOR：客户端传的 user scope_id 一律不信 —— 否则攻击者能把自己扫的号
    绑进别人的个人空间（与 OAuth connect 同一条守卫）。"""
    manager = MagicMock()
    manager.create = AsyncMock(return_value="wf")
    dispatched: dict = {}

    async def fake_dispatch(task_type, **kw):
        dispatched.update(kw)
        return {}

    monkeypatch.setattr(dr, "get_task_manager", lambda: manager)
    monkeypatch.setattr(dr, "start_workflow_routed", fake_dispatch)

    resp = client.post(
        "/api/v1/distribution/accounts/session/login",
        json=_login_body(scope_id="22222222-2222-2222-2222-222222222222"),
    )

    assert resp.status_code == 200
    assert dispatched["dbos_workflow_kwargs"]["scope_id"] == _USER


def test_start_login_team_scope_requires_membership(
    client, configured_browser, monkeypatch
):
    async def no_teams(_uid):
        return []

    monkeypatch.setattr(dr, "_user_team_ids", no_teams)
    resp = client.post(
        "/api/v1/distribution/accounts/session/login",
        json=_login_body(scope_type="team", scope_id="777"),
    )
    assert resp.status_code == 403


def test_start_login_rejects_an_unsupported_platform(client, configured_browser):
    resp = client.post(
        "/api/v1/distribution/accounts/session/login",
        json=_login_body(platform="kuaishou"),
    )
    assert resp.status_code == 400
    assert "kuaishou" in resp.json()["detail"]


def test_start_login_503s_when_the_browser_service_is_not_configured(
    client, monkeypatch
):
    """派发一个只会失败的 workflow，用户会看着任务出现然后死掉 —— 503 才说得清
    是服务端没配，不是他做错了。"""
    fake = MagicMock()
    fake.is_configured = False
    monkeypatch.setattr(
        "app.services.distribution.browser_client.BrowserClient", lambda: fake
    )
    dispatched = []
    monkeypatch.setattr(
        dr,
        "start_workflow_routed",
        AsyncMock(side_effect=lambda *a, **k: dispatched.append(a)),
    )

    resp = client.post(
        "/api/v1/distribution/accounts/session/login", json=_login_body()
    )
    assert resp.status_code == 503
    assert dispatched == []


@pytest.mark.parametrize(
    "body",
    [
        {"scope_type": "user"},  # 缺 scope_id
        {"platform": "", "scope_type": "user", "scope_id": _USER},
        {"platform": "douyin", "scope_type": "org", "scope_id": _USER},
    ],
)
def test_start_login_validates_the_request_body(client, configured_browser, body):
    resp = client.post("/api/v1/distribution/accounts/session/login", json=body)
    assert resp.status_code == 422


# ── POST /accounts/session/login/{task_id}/sms ──────────────


def _patch_task(monkeypatch, *, phase="in_progress", login_session_id=SID, owner=_USER):
    async def fake_load(task_id, user):
        if str(user["id"]) != owner:
            raise HTTPException(status_code=404, detail="Login task not found")
        meta = {}
        if login_session_id:
            meta["session_login"] = {"login_session_id": login_session_id}
        return {"phase": phase, "metadata": meta}

    monkeypatch.setattr(dr, "_load_login_task", fake_load)


def test_sms_forwards_the_code_and_returns_the_typed_result(
    client, configured_browser, monkeypatch
):
    _patch_task(monkeypatch)
    snapshot = MagicMock()
    snapshot.result.to_dict.return_value = {
        "success": True,
        "status": "scanned",
        "message": "accepted",
        "detail": {},
    }
    configured_browser.submit_login_sms.return_value = snapshot

    resp = client.post(
        f"/api/v1/distribution/accounts/session/login/{'wf-1'}/sms",
        json={"code": "123456"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "scanned"
    configured_browser.submit_login_sms.assert_awaited_once_with(SID, "123456")


def test_sms_rejection_is_a_200_with_a_readable_status(
    client, configured_browser, monkeypatch
):
    """码错了是业务结论，不是 HTTP 错误 —— 前端要能原地提示重输。"""
    _patch_task(monkeypatch)
    snapshot = MagicMock()
    snapshot.result.to_dict.return_value = {
        "success": False,
        "status": "sms_required",
        "message": "code rejected",
        "detail": {},
    }
    configured_browser.submit_login_sms.return_value = snapshot

    resp = client.post(
        "/api/v1/distribution/accounts/session/login/wf-1/sms", json={"code": "000000"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "sms_required"
    assert resp.json()["message"] == "code rejected"


def test_sms_rejection_reaches_the_browser_with_success_false_and_the_marker(
    client, configured_browser, monkeypatch
):
    """信封整条链都要活着：``success=False`` **和** ``detail.code_rejected``。

    前者决定前端走不走失败分支（走错就是零反馈），后者决定它说哪句话（"码没
    过，再输一次" vs 回显一句机器写的英文）。中间任何一层把 ``detail`` 抹平
    成 ``{}``，第二件事就静默丢了 —— 而没有断言的话没人会发现。
    """
    _patch_task(monkeypatch)
    snapshot = MagicMock()
    snapshot.result.to_dict.return_value = {
        "success": False,
        "status": "sms_required",
        "message": "the verification code was not accepted",
        "detail": {"code_rejected": True, "submitted": True},
    }
    configured_browser.submit_login_sms.return_value = snapshot

    resp = client.post(
        "/api/v1/distribution/accounts/session/login/wf-1/sms", json={"code": "000000"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["detail"]["code_rejected"] is True
    # 提交的验证码本身绝不回显。
    assert "000000" not in resp.text


def test_sms_on_a_terminal_task_is_409(client, configured_browser, monkeypatch):
    """终态任务的 context 早已释放，转发进去只会在容器里 404。"""
    _patch_task(monkeypatch, phase="cancelled")
    resp = client.post(
        "/api/v1/distribution/accounts/session/login/wf-1/sms", json={"code": "123456"}
    )
    assert resp.status_code == 409
    configured_browser.submit_login_sms.assert_not_awaited()


def test_sms_before_the_context_exists_is_409(client, configured_browser, monkeypatch):
    _patch_task(monkeypatch, login_session_id=None)
    resp = client.post(
        "/api/v1/distribution/accounts/session/login/wf-1/sms", json={"code": "123456"}
    )
    assert resp.status_code == 409


def test_sms_on_someone_elses_task_is_404(client, configured_browser, monkeypatch):
    _patch_task(monkeypatch, owner="99999999-9999-9999-9999-999999999999")
    resp = client.post(
        "/api/v1/distribution/accounts/session/login/wf-1/sms", json={"code": "123456"}
    )
    assert resp.status_code == 404


@pytest.mark.parametrize("code", ["", "123", "1234567890", "12a456"])
def test_sms_validates_the_code(client, configured_browser, monkeypatch, code):
    """校验口径与 nous-browser 的 SmsCodeRequest 逐字相同（纯数字 4–8 位）——
    放宽一格的话浏览器侧会 422，用户看到的是"browser service returned HTTP
    422"，一个输入错误被报成基建故障。"""
    _patch_task(monkeypatch)
    resp = client.post(
        "/api/v1/distribution/accounts/session/login/wf-1/sms", json={"code": code}
    )
    assert resp.status_code == 422


def test_sms_is_behind_the_module_switch(monkeypatch):
    _patch_task(monkeypatch)
    app = _make_app(module_on=False)
    resp = TestClient(app).post(
        "/api/v1/distribution/accounts/session/login/wf-1/sms", json={"code": "123456"}
    )
    assert resp.status_code == 404


# ── DELETE /accounts/session/login/{task_id} ────────────────


def test_cancel_cancels_the_task_and_releases_the_context(
    client, configured_browser, monkeypatch
):
    _patch_task(monkeypatch)
    manager = MagicMock()
    manager.cancel = AsyncMock()
    monkeypatch.setattr(dr, "get_task_manager", lambda: manager)

    resp = client.delete("/api/v1/distribution/accounts/session/login/wf-1")

    assert resp.status_code == 200
    assert resp.json() == {
        "cancelled": True,
        "context_released": True,
        "message": "Login cancelled",
    }
    manager.cancel.assert_awaited_once_with("wf-1", _USER)
    configured_browser.close_login.assert_awaited_once_with(SID)


def test_cancel_reports_an_unconfirmed_release_instead_of_500(
    client, configured_browser, monkeypatch
):
    """容器不可达时任务确实取消了，但那个有头 context 要等 TTL —— 说出来。"""
    _patch_task(monkeypatch)
    manager = MagicMock()
    manager.cancel = AsyncMock()
    monkeypatch.setattr(dr, "get_task_manager", lambda: manager)
    configured_browser.close_login.return_value = False

    resp = client.delete("/api/v1/distribution/accounts/session/login/wf-1")

    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True
    assert resp.json()["context_released"] is False


def test_cancel_before_the_context_exists_still_cancels(
    client, configured_browser, monkeypatch
):
    """workflow 刚派发、还没起浏览器时点取消 —— 不能 409 把用户卡住。"""
    _patch_task(monkeypatch, login_session_id=None)
    manager = MagicMock()
    manager.cancel = AsyncMock()
    monkeypatch.setattr(dr, "get_task_manager", lambda: manager)

    resp = client.delete("/api/v1/distribution/accounts/session/login/wf-1")

    assert resp.status_code == 200
    manager.cancel.assert_awaited_once()
    configured_browser.close_login.assert_not_awaited()


def test_cancel_on_someone_elses_task_is_404(client, configured_browser, monkeypatch):
    _patch_task(monkeypatch, owner="99999999-9999-9999-9999-999999999999")
    manager = MagicMock()
    manager.cancel = AsyncMock()
    monkeypatch.setattr(dr, "get_task_manager", lambda: manager)

    resp = client.delete("/api/v1/distribution/accounts/session/login/wf-1")

    assert resp.status_code == 404
    manager.cancel.assert_not_awaited()


# ── _load_login_task 本身（其余用例把它整个打了桩） ─────────


def _fake_read_scope(row):
    """read_scope() 的替身：execute() 只认 scalar_one_or_none。"""
    from contextlib import asynccontextmanager

    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


def _task_row(**over):
    from app.models import TaskTracking

    row = TaskTracking()
    row.user_id = _USER
    row.phase = "in_progress"
    row.task_type = dr.SESSION_LOGIN_TASK_TYPE
    row.metadata_ = {"session_login": {"login_session_id": SID}}
    for k, v in over.items():
        setattr(row, k, v)
    return row


async def test_load_login_task_reads_metadata_off_the_orm_attribute(monkeypatch):
    """``metadata`` 在 ORM 上叫 ``metadata_``（``metadata`` 是保留名）。取错名
    字不会报错，只会永远拿到 None —— 表现成"登录会话还没就绪"，像是浏览器坏了。
    """
    monkeypatch.setattr("app.db.session.read_scope", _fake_read_scope(_task_row()))
    task = await dr._load_login_task("wf-1", {"id": _USER})

    assert task["phase"] == "in_progress"
    assert dr._login_session_id(task) == SID


async def test_load_login_task_404s_a_foreign_task(monkeypatch):
    monkeypatch.setattr(
        "app.db.session.read_scope",
        _fake_read_scope(_task_row(user_id="99999999-9999-9999-9999-999999999999")),
    )
    with pytest.raises(HTTPException) as exc:
        await dr._load_login_task("wf-1", {"id": _USER})
    assert exc.value.status_code == 404


async def test_load_login_task_404s_a_task_of_another_type(monkeypatch):
    """任务 id 是用户可见的 —— 不能让它变成对任意任务调 cancel 的通道。"""
    monkeypatch.setattr(
        "app.db.session.read_scope", _fake_read_scope(_task_row(task_type="publish"))
    )
    with pytest.raises(HTTPException) as exc:
        await dr._load_login_task("wf-1", {"id": _USER})
    assert exc.value.status_code == 404


async def test_load_login_task_404s_a_missing_row(monkeypatch):
    monkeypatch.setattr("app.db.session.read_scope", _fake_read_scope(None))
    with pytest.raises(HTTPException) as exc:
        await dr._load_login_task("nope", {"id": _USER})
    assert exc.value.status_code == 404


def test_cancel_is_behind_the_module_switch(monkeypatch):
    _patch_task(monkeypatch)
    app = _make_app(module_on=False)
    resp = TestClient(app).delete("/api/v1/distribution/accounts/session/login/wf-1")
    assert resp.status_code == 404
