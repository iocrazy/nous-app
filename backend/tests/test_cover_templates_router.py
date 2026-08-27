"""``/cover-templates`` —— 模板库 = 系统文件夹（migration 441）。

端点层只做三件事，所以只测这三件：

- **GET /folder** 回传文件夹身份，且把"这次是认领了你已有的文件夹"这件事说出来
  （adopted）—— 用户自己建过「封面」文件夹，静默旁边再建一个就是"两套账"重演。
- **GET /** 列出文件夹里的图，`thumb_url` 是无鉴权的 `/resources/{id}/cover`
  （可裸 <img>），**不是**参考 URL —— 参考 URL 在选中时才 import。
- **POST /use** 按 resource id 记用量；超过 9 个响亮拒绝。

不再有 create / rename / delete：归属就是文件夹，由素材库管。
"""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

ct = sys.modules["app.api.cover_templates_router"]

FAKE_USER_ID = "00000000-0000-0000-0000-000000000042"
SCOPE_ID = 42
FOLDER_ID = "342691028665012"
RES_ID = "341582104263581"  # > 2^53：wire 上必须是字符串


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture(autouse=True)
def _fake_scope(monkeypatch):
    async def _scope(auth):
        return SCOPE_ID

    monkeypatch.setattr(ct, "_scope", _scope)


class _FakeRepo:
    def __init__(self, *, adopted=False, items=None):
        self._adopted = adopted
        self._items = (
            items
            if items is not None
            else [
                {
                    "resource_id": RES_ID,
                    "name": "krea2.avif",
                    "mime_type": "image/avif",
                    "usage_count": 3,
                    "last_used_at": None,
                },
                {
                    "resource_id": "9",
                    "name": "owen.png",
                    "mime_type": "image/png",
                    "usage_count": 0,
                    "last_used_at": None,
                },
            ]
        )
        self.bumped = []

    async def ensure_folder(self, scope_id, user_id):
        return {"id": FOLDER_ID, "name": "封面", "adopted": self._adopted}

    async def list_images(self, scope_id, folder_id):
        assert folder_id == int(FOLDER_ID)
        return self._items

    async def bump_usage(self, resource_ids, scope_id):
        self.bumped.append((resource_ids, scope_id))


def _wire(monkeypatch, repo=None):
    r = repo or _FakeRepo()
    monkeypatch.setattr(ct, "CoverTemplatesRepository", lambda: r)
    return r


@pytest.mark.asyncio
async def test_folder_says_when_it_adopted_the_users_own_folder(monkeypatch, client):
    _wire(monkeypatch, _FakeRepo(adopted=True))

    resp = await client.get("/api/v1/cover-templates/folder")

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "folder_id": FOLDER_ID,
        "name": "封面",
        "adopted": True,
    }


@pytest.mark.asyncio
async def test_list_returns_folder_images_with_public_thumbs(monkeypatch, client):
    _wire(monkeypatch)

    resp = await client.get("/api/v1/cover-templates")

    body = resp.json()["data"]
    assert body["folder"]["folder_id"] == FOLDER_ID
    assert [i["resource_id"] for i in body["items"]] == [RES_ID, "9"]
    # 无鉴权缩略图路由，可裸 <img>；不是参考 URL（那个在选中时才 import）。
    assert body["items"][0]["thumb_url"] == f"/api/v1/resources/{RES_ID}/cover"
    assert isinstance(body["items"][0]["resource_id"], str)


@pytest.mark.asyncio
async def test_use_bumps_by_resource_id(monkeypatch, client):
    r = _wire(monkeypatch)

    resp = await client.post(
        "/api/v1/cover-templates/use", json={"resource_ids": [RES_ID, "9"]}
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["counted"] == 2
    assert r.bumped == [([int(RES_ID), 9], SCOPE_ID)]


@pytest.mark.asyncio
async def test_use_refuses_more_than_nine(monkeypatch, client):
    r = _wire(monkeypatch)

    resp = await client.post(
        "/api/v1/cover-templates/use",
        json={"resource_ids": [str(i) for i in range(10)]},
    )

    assert resp.status_code == 422
    assert r.bumped == []


@pytest.mark.asyncio
async def test_a_non_numeric_id_is_400_not_500(monkeypatch, client):
    _wire(monkeypatch)

    resp = await client.post(
        "/api/v1/cover-templates/use", json={"resource_ids": ["abc"]}
    )

    assert resp.status_code == 400
