import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.distribution_router as dr
import app.services.distribution.module_config as mc


def _make_app(flag: bool, monkeypatch) -> TestClient:
    # The access gate is now the DB-backed module switch (admin-controlled),
    # not an env flag: require_distribution consults module_config.is_module_enabled.
    async def fake_enabled() -> bool:
        return flag

    monkeypatch.setattr(mc, "is_module_enabled", fake_enabled)
    test_app = FastAPI()
    test_app.include_router(dr.router, prefix="/api/v1")
    test_app.dependency_overrides[dr.get_current_user] = lambda: {"id": "u-1"}
    return TestClient(test_app)


def test_flag_off_is_404(monkeypatch):
    client = _make_app(False, monkeypatch)
    assert client.get("/api/v1/distribution/accounts").status_code == 404


def test_connect_returns_auth_url_and_persists_state(monkeypatch):
    client = _make_app(True, monkeypatch)
    saved = {}

    async def fake_save_state(state, user_id, platform, scope_type, scope_id):
        saved.update(state=state, user_id=user_id, platform=platform)

    async def fake_list(user_id, team_ids):
        return []

    async def fake_user_team_ids(user_id):
        return []

    monkeypatch.setattr(dr, "_save_oauth_state", fake_save_state)
    monkeypatch.setattr(dr, "_user_team_ids", fake_user_team_ids)
    monkeypatch.setattr(dr.accounts_repo, "list_for_user", fake_list)

    from app.services.distribution.douyin_adapter import DouyinCredentials

    async def fake_creds():
        return DouyinCredentials("ck", "cs", "https://x/cb")

    monkeypatch.setattr(dr, "get_douyin_credentials", fake_creds)

    resp = client.post(
        "/api/v1/distribution/accounts/connect",
        json={"platform": "douyin", "scope_type": "user", "scope_id": "u-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["auth_url"].startswith("https://open.douyin.com/")
    assert saved["state"] in body["auth_url"] and saved["user_id"] == "u-1"


def test_connect_user_scope_forces_caller_id_ignores_client_scope_id(monkeypatch):
    """IDOR guard: scope_type='user' must never trust a client-supplied
    scope_id — even a syntactically valid uuid belonging to another user
    must be silently overridden with the caller's own id."""
    client = _make_app(True, monkeypatch)
    saved = {}

    async def fake_save_state(state, user_id, platform, scope_type, scope_id):
        saved.update(
            state=state,
            user_id=user_id,
            platform=platform,
            scope_type=scope_type,
            scope_id=scope_id,
        )

    async def fake_user_team_ids(user_id):
        return []

    monkeypatch.setattr(dr, "_save_oauth_state", fake_save_state)
    monkeypatch.setattr(dr, "_user_team_ids", fake_user_team_ids)

    from app.services.distribution.douyin_adapter import DouyinCredentials

    async def fake_creds():
        return DouyinCredentials("ck", "cs", "https://x/cb")

    monkeypatch.setattr(dr, "get_douyin_credentials", fake_creds)

    resp = client.post(
        "/api/v1/distribution/accounts/connect",
        json={
            "platform": "douyin",
            "scope_type": "user",
            "scope_id": "VICTIM-uuid",
        },
    )
    assert resp.status_code == 200
    # the caller (mocked as "u-1") must be the persisted scope_id, never the
    # client-supplied victim value.
    assert saved["scope_id"] == "u-1"
    assert saved["scope_id"] != "VICTIM-uuid"


def test_refresh_account_idor_blocked_for_non_owner(monkeypatch):
    client = _make_app(True, monkeypatch)

    async def fake_get_public(account_id):
        return {
            "id": str(account_id),
            "scope_type": "user",
            "scope_id": "someone-else",
        }

    async def fake_user_team_ids(user_id):
        return []

    get_with_tokens_calls = []

    async def fake_get_with_tokens(account_id):
        get_with_tokens_calls.append(account_id)
        return {"scope_type": "user", "scope_id": "someone-else"}

    monkeypatch.setattr(dr.accounts_repo, "get_public", fake_get_public)
    monkeypatch.setattr(dr, "_user_team_ids", fake_user_team_ids)
    monkeypatch.setattr(dr.accounts_repo, "get_with_tokens", fake_get_with_tokens)

    resp = client.post("/api/v1/distribution/accounts/123/refresh")
    assert resp.status_code == 404
    assert get_with_tokens_calls == []


def test_delete_account_idor_blocked_for_non_owner(monkeypatch):
    client = _make_app(True, monkeypatch)

    async def fake_get_public(account_id):
        return {
            "id": str(account_id),
            "scope_type": "user",
            "scope_id": "someone-else",
        }

    async def fake_user_team_ids(user_id):
        return []

    delete_calls = []

    # mig 416：删除改软删，方法名随之从 delete 变成 soft_delete。
    # monkeypatch.setattr 在属性不存在时会抛 AttributeError，所以这个改名不会
    # 悄悄让守卫失效 —— 它直接把这条测试打红了。
    async def fake_soft_delete(account_id):
        delete_calls.append(account_id)

    monkeypatch.setattr(dr.accounts_repo, "get_public", fake_get_public)
    monkeypatch.setattr(dr, "_user_team_ids", fake_user_team_ids)
    monkeypatch.setattr(dr.accounts_repo, "soft_delete", fake_soft_delete)

    resp = client.delete("/api/v1/distribution/accounts/123")
    assert resp.status_code == 404
    assert delete_calls == []
