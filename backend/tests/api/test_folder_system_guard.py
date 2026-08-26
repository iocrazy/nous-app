"""系统文件夹保护（migration 441）。

`folders.is_system` 此前是个死字段：零处读写、生产 0 行 true。现在它真的拦：
改名 / 移动 / 回收站 / 永久删除 四条路径对 is_system 文件夹回 **类型化 409**
（code=system_folder）—— 不是 500（那会伪装成"服务坏了"），也不是静默 no-op。
文件夹**内容**不受保护：往里放、从里面移走都自由，那正是这个文件夹的用途。
cosmetic 字段（icon/color/sort_order）照常可改。
"""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

fr = sys.modules["app.api.resources_folders_router"]

FAKE_USER_ID = "00000000-0000-0000-0000-000000000042"
SYSTEM_FOLDER = {
    "id": 342691028665012,
    "name": "封面",
    "scope_id": 42,
    "is_system": True,
    "system_key": "cover_templates",
    "is_trashed": False,
}
PLAIN_FOLDER = {
    "id": 7,
    "name": "misc",
    "scope_id": 42,
    "is_system": False,
    "system_key": None,
    "is_trashed": False,
}


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


class _Repo:
    def __init__(self, folder):
        self.folder = folder
        self.updated = []
        self.trashed = []

    async def get_folder_by_id(self, folder_id):
        return dict(self.folder)

    async def update_folder(self, folder_id, data):
        self.updated.append(data)
        return {**self.folder, **data}

    async def trash_folder_cascade(self, folder_id):
        self.trashed.append(folder_id)
        return {"trashed": True}


class _Svc:
    def __init__(self):
        self.deleted = []

    async def permanent_delete_folder(self, folder_id, user_id):
        self.deleted.append(folder_id)
        return {"deleted": True}


def _wire(monkeypatch, folder):
    repo, svc = _Repo(folder), _Svc()
    monkeypatch.setattr(fr, "ResourcesRepository", lambda: repo)
    monkeypatch.setattr(fr, "ResourcesService", lambda: svc)

    async def _ok(folder, auth):
        return None

    monkeypatch.setattr(fr, "_verify_folder_ownership_inline", _ok)
    return repo, svc


@pytest.mark.asyncio
async def test_rename_of_a_system_folder_is_a_typed_409(monkeypatch, client):
    repo, _ = _wire(monkeypatch, SYSTEM_FOLDER)

    resp = await client.patch(
        "/api/v1/resources/folders/342691028665012", json={"name": "x"}
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "system_folder"
    assert repo.updated == [], "被拒绝的改名不该落库"


@pytest.mark.asyncio
async def test_cosmetic_edits_of_a_system_folder_still_work(monkeypatch, client):
    repo, _ = _wire(monkeypatch, SYSTEM_FOLDER)

    resp = await client.patch(
        "/api/v1/resources/folders/342691028665012", json={"color": "#1E7A5B"}
    )

    assert resp.status_code == 200, resp.text
    assert repo.updated and repo.updated[0]["color"] == "#1E7A5B"


@pytest.mark.asyncio
async def test_trash_of_a_system_folder_is_a_typed_409(monkeypatch, client):
    repo, _ = _wire(monkeypatch, SYSTEM_FOLDER)

    resp = await client.post("/api/v1/resources/folders/342691028665012/trash")

    assert resp.status_code == 409
    assert resp.json()["code"] == "system_folder"
    assert repo.trashed == []


@pytest.mark.asyncio
async def test_delete_of_a_system_folder_is_a_typed_409(monkeypatch, client):
    _, svc = _wire(monkeypatch, SYSTEM_FOLDER)

    resp = await client.delete("/api/v1/resources/folders/342691028665012")

    assert resp.status_code == 409
    assert resp.json()["code"] == "system_folder"
    assert svc.deleted == []


@pytest.mark.asyncio
async def test_a_plain_folder_is_untouched_by_the_guard(monkeypatch, client):
    # 正向对照：保护只作用于 is_system，普通文件夹的删除照常。
    _, svc = _wire(monkeypatch, PLAIN_FOLDER)

    resp = await client.delete("/api/v1/resources/folders/7")

    assert resp.status_code == 200, resp.text
    assert svc.deleted == ["7"]
