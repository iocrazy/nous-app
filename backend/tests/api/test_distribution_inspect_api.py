"""``POST /admin/distribution/inspect`` —— 只读勘探的 admin 入口（T0）。

这个端点会拿一个真实账号的明文会话去打开创作者后台，所以入口这一层守的全是
"谁能调、能调谁的账号、以及**响应里不许有什么**"：

* admin 角色（AdminAuthDep），不是普通用户面功能；
* 与其余分发端点同一个模块开关（关掉 → 404）；
* IDOR 守卫复用用户面那一个 seam —— admin 也不能勘探别人 scope 的账号；
* 探针文件只收 resource_id，URL 由发布链同一条解析路径生成（收任意 URL 等于
  给一个持有用户 cookie 的容器加一条 SSRF）；
* 响应里没有 storage_state，且永远不会有。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.api.admin.distribution_inspect_router as ir
import app.api.distribution_router as dr

_ADMIN = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
_PATH = "/api/v1/admin/distribution/inspect"
_URL = "https://creator.douyin.com/creator-micro/content/upload"
_ACCOUNT = 337271352171182


class _Auth:
    user_id = _ADMIN


def _make_app(*, module_on: bool = True) -> FastAPI:
    from app.core.admin_deps import get_admin_auth

    async def _module():
        if not module_on:
            raise HTTPException(status_code=404, detail="Not found")

    app = FastAPI()
    app.include_router(ir.router, prefix="/api/v1/admin/distribution/inspect")
    app.dependency_overrides[ir._require_distribution] = _module
    app.dependency_overrides[get_admin_auth] = lambda: _Auth()
    return app


@pytest.fixture(autouse=True)
def _no_audit_writes(monkeypatch):
    """审计写库不在本文件的射程内 —— 但**必须被调到**，见下面的用例。"""
    calls: list[dict] = []

    async def _log(**kw):
        calls.append(kw)

    monkeypatch.setattr(ir, "create_audit_log", _log)
    return calls


@pytest.fixture
def authorized(monkeypatch):
    """默认放行 IDOR 守卫。想测越权的用例自己再 patch 回去。"""

    async def _ok(account_id, user):
        return {"id": str(account_id), "scope_type": "user", "scope_id": _ADMIN}

    monkeypatch.setattr(dr, "_authorize_account", _ok)


@pytest.fixture
def service(monkeypatch):
    """替换掉真正会开浏览器的那一层，记录它收到了什么。"""
    import app.services.distribution.session_inspect as si

    calls: list[dict] = []

    async def _inspect(account_id, url, **kw):
        calls.append({"account_id": account_id, "url": url, **kw})
        return {
            "success": True,
            "status": "session_valid",
            "message": "page read",
            "detail": {"stage": "observe"},
            "observation": {"texts": {"定时发布": {"exact": 1}}},
            "session_refreshed": True,
        }

    monkeypatch.setattr(si, "inspect_account_page", _inspect)
    return calls


def _body(**overrides) -> dict:
    payload = {"account_id": _ACCOUNT, "url": _URL}
    payload.update(overrides)
    return payload


def test_module_switch_off_is_a_404(authorized, service):
    client = TestClient(_make_app(module_on=False))
    assert client.post(_PATH, json=_body()).status_code == 404
    assert service == []


def test_a_foreign_account_404s_before_anything_opens(monkeypatch, service):
    async def _deny(account_id, user):
        raise HTTPException(status_code=404, detail="Account not found")

    monkeypatch.setattr(dr, "_authorize_account", _deny)
    client = TestClient(_make_app())

    assert client.post(_PATH, json=_body()).status_code == 404
    assert service == []


def test_a_successful_read_returns_the_summary_and_no_credentials(
    authorized, service, _no_audit_writes
):
    client = TestClient(_make_app())
    resp = client.post(
        _PATH,
        json=_body(text_probes=["定时发布"], selector_probes=['input[type="file"]']),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["observation"]["texts"]["定时发布"]["exact"] == 1
    assert body["session_refreshed"] is True
    assert "storage_state" not in body
    assert "updated_storage_state" not in body

    # 谁在什么时候看了哪一页必须留痕 —— 但**只记 URL 与探针数量**，不记观测
    # 内容（那是页面文本）。
    assert len(_no_audit_writes) == 1
    details = _no_audit_writes[0]["details"]
    assert details["url"] == _URL
    assert details["text_probes"] == 1
    assert "observation" not in details


def test_seed_files_are_resolved_through_the_publish_url_path(
    monkeypatch, authorized, service
):
    """探针文件只收 resource_id；URL 由 ``get_resource_media_url`` 生成 ——
    与发布链同一条解析路径，没有第二处签名逻辑。"""
    import app.repositories.publish_tasks_repository as ptr

    class _Repo:
        async def get_resource_media_url(self, resource_id):
            return f"https://store.example/library/probe-image-{resource_id}.jpg?sig=x"

    monkeypatch.setattr(ptr, "PublishTasksRepository", _Repo)
    client = TestClient(_make_app())

    resp = client.post(_PATH, json=_body(seed_resource_ids=[1, 2]))

    assert resp.status_code == 200
    seeds = service[0]["seed_files"]
    assert [item["filename"] for item in seeds] == [
        "probe-image-1.jpg",
        "probe-image-2.jpg",
    ]
    assert all(item["kind"] == "image" for item in seeds)
    # 顺序即入参顺序 —— 探针图的顺序就是页面上的顺序。
    assert seeds[0]["url"].endswith("probe-image-1.jpg?sig=x")


def test_an_unresolvable_seed_resource_fails_loud(monkeypatch, authorized, service):
    """少传一张探针图会让后面数出来的匹配数对应一个我们以为不存在的页面状态。
    整体失败比静默少传好。"""
    import app.repositories.publish_tasks_repository as ptr

    class _Repo:
        async def get_resource_media_url(self, resource_id):
            return None

    monkeypatch.setattr(ptr, "PublishTasksRepository", _Repo)
    client = TestClient(_make_app())

    resp = client.post(_PATH, json=_body(seed_resource_ids=[99]))

    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "seed_resource_unresolved"
    assert service == []


def test_the_url_is_not_second_guessed_here(authorized, service):
    """白名单只有一处 —— 在真正会去访问的那一侧（浏览器）。这里透传，
    由下游给出类型化拒绝，而不是在中途抄一份 allow-list 出来慢慢腐烂。"""
    client = TestClient(_make_app())
    resp = client.post(_PATH, json=_body(url="https://example.com/"))

    assert resp.status_code == 200
    assert service[0]["url"] == "https://example.com/"


def test_probe_caps_are_a_422(authorized, service):
    client = TestClient(_make_app())
    resp = client.post(_PATH, json=_body(text_probes=["x"] * 41))
    assert resp.status_code == 422
    assert service == []
