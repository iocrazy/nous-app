"""``POST /accounts/{id}/refresh`` 的 session 分支。

这个端点原本只认 OAuth 的 ``refresh_token``。session 账号按构造就没有那一列，
于是会撞上 OAuth 那条守卫，拿到 ``404 "Account not found or no refresh token"``
—— 一个绑好的、能发布的账号被告知不存在。正是 CLAUDE.md「触发路径必须类型化
失败回显」要消灭的那种死胡同。

覆盖四件必须成立的事：

1. session 账号走 session 分支，不再 404
2. **基建失败绝不改动账号状态** —— 浏览器容器重启期间把账号标成
   needs_relogin，会让用户去重扫码来修我们这边的故障（spec §7.8）
3. 会话真失效 → 400 + needs_relogin，且原因可分辨
4. 校验成功 → 回填 profile；抓不到的字段传 None，不覆盖已有值
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.api.distribution_router as dr
from app.services.distribution.browser_client import SessionErrorKind

_USER = "11111111-1111-1111-1111-111111111111"
_ACCOUNT_ID = 7788

_SESSION_ACCT = {
    "id": _ACCOUNT_ID,
    "platform": "douyin",
    "auth_type": "session",
    "session_state": '{"cookies": [{"name": "sessionid", "value": "x"}]}',
    "environment": None,
}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = lambda: None
    app.dependency_overrides[dr.get_current_user] = lambda: {"id": _USER}
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app())


@pytest.fixture
def repo(monkeypatch):
    """accounts_repo 打桩，默认返回一个活的 session 账号。"""
    fake = MagicMock()
    fake.get_with_session = AsyncMock(return_value=dict(_SESSION_ACCT))
    fake.get_with_tokens = AsyncMock(return_value=None)
    fake.update_profile = AsyncMock()
    fake.update_session_state = AsyncMock()
    fake.mark_needs_relogin = AsyncMock()
    fake.get_public = AsyncMock(
        return_value={"id": _ACCOUNT_ID, "username": "Test Creator"}
    )
    monkeypatch.setattr(dr, "accounts_repo", fake)

    async def _noop_authorize(account_id, user):
        return None

    monkeypatch.setattr(dr, "_authorize_account", _noop_authorize)
    return fake


def _stub_adapter(monkeypatch, result: dict):
    adapter = MagicMock()
    adapter.validate_session = AsyncMock(return_value=result)
    import app.services.distribution.registry as registry

    monkeypatch.setattr(registry, "get_session_adapter", lambda _p: adapter)
    return adapter


def test_session_account_no_longer_404s(client, repo, monkeypatch):
    """回归：session 账号曾被 OAuth 守卫判成「不存在」。"""
    _stub_adapter(
        monkeypatch,
        {"success": True, "status": "session_valid", "message": "ok", "detail": {}},
    )
    resp = client.post(f"/api/v1/distribution/accounts/{_ACCOUNT_ID}/refresh")
    assert resp.status_code != 404
    assert resp.status_code == 200


def test_infra_failure_leaves_the_account_untouched(client, repo, monkeypatch):
    """本条是这个模块存在的主要理由。

    浏览器容器挂了不是账号的错。标 needs_relogin 会把一次我方故障变成
    「所有用户重扫码」。
    """
    _stub_adapter(
        monkeypatch,
        {
            "success": False,
            "status": "failed",
            "message": "browser service unreachable",
            # 用枚举而不是字面量：第一版我编了个 "connect_error"，不在
            # SessionErrorKind 里，于是走了业务失败分支。这么写让枚举
            # 收窄时测试跟着失败，而不是继续测一个不存在的取值。
            "detail": {"error_kind": SessionErrorKind.UNREACHABLE.value},
        },
    )
    resp = client.post(f"/api/v1/distribution/accounts/{_ACCOUNT_ID}/refresh")

    assert resp.status_code == 503
    body = resp.json()["detail"]
    assert body["error"] == "session_check_unavailable"
    assert body["error_kind"] == SessionErrorKind.UNREACHABLE.value
    # 关键断言：一个字段都没动
    repo.mark_needs_relogin.assert_not_awaited()
    repo.update_session_state.assert_not_awaited()
    repo.update_profile.assert_not_awaited()


def test_invalid_session_marks_relogin_with_a_readable_reason(
    client, repo, monkeypatch
):
    _stub_adapter(
        monkeypatch,
        {
            "success": False,
            "status": "session_invalid",
            "message": "redirected to the login page",
            "detail": {"reason": "login_page_detected"},
        },
    )
    resp = client.post(f"/api/v1/distribution/accounts/{_ACCOUNT_ID}/refresh")

    assert resp.status_code == 400
    body = resp.json()["detail"]
    assert body["error"] == "session_invalid"
    assert body["reason"] == "login_page_detected"
    repo.mark_needs_relogin.assert_awaited_once()
    repo.update_profile.assert_not_awaited()


def test_success_writes_back_the_scraped_profile(client, repo, monkeypatch):
    """绑定后再也没被刷新过的昵称/头像，在这里被修好。"""
    _stub_adapter(
        monkeypatch,
        {
            "success": True,
            "status": "session_valid",
            "message": "ok",
            "detail": {
                "profile": {
                    "platform_user_id": "MS4wLjABAAAA",
                    "username": "Test Creator",
                    "avatar_url": "https://cdn/aweme-avatar/x.jpeg",
                }
            },
        },
    )
    resp = client.post(f"/api/v1/distribution/accounts/{_ACCOUNT_ID}/refresh")

    assert resp.status_code == 200
    repo.update_profile.assert_awaited_once()
    kwargs = repo.update_profile.await_args.kwargs
    assert kwargs["username"] == "Test Creator"
    assert kwargs["avatar_url"] == "https://cdn/aweme-avatar/x.jpeg"
    # session_state 不在这里续期：validate 不产出新 cookie，publish 才产出。
    repo.update_session_state.assert_awaited_once()
    assert repo.update_session_state.await_args.args[1] is None


def test_missing_profile_does_not_blank_the_stored_name(client, repo, monkeypatch):
    """选择器全落空时，宁可不动，也不能把好名字覆盖成空。"""
    _stub_adapter(
        monkeypatch,
        {"success": True, "status": "session_valid", "message": "ok", "detail": {}},
    )
    resp = client.post(f"/api/v1/distribution/accounts/{_ACCOUNT_ID}/refresh")

    assert resp.status_code == 200
    kwargs = repo.update_profile.await_args.kwargs
    assert kwargs["username"] is None
    assert kwargs["avatar_url"] is None


def test_unknown_account_still_404s(client, repo, monkeypatch):
    """404 必须来自「账号不存在」，不是「路由不存在」。

    这个用例第一版写错了 URL（漏了 /distribution 前缀）却照样绿 —— 路由不
    存在同样返回 404。断言里加上 detail 文案，让判据不能被自己的失败满足。
    """
    repo.get_with_session = AsyncMock(return_value=None)
    resp = client.post(f"/api/v1/distribution/accounts/{_ACCOUNT_ID}/refresh")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Account not found"
