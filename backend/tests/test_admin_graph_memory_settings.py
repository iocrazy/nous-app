"""Admin Memory-settings endpoint (Phase 4 — frontend-settable graph memory).

GET /admin/settings/graph-memory returns the Graphiti config with api_keys
MASKED (never leaks the extractor/embedder key, even to an admin); PUT updates
only the provided graph_* keys (api_key fields stay unchanged unless a new
value is sent). Auth is admin-only.
"""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.admin_deps import get_admin_auth
from app.core.deps import AuthContext
from app.main import app

# app.api.admin.__init__ rebinds the name ``settings_router`` to the APIRouter
# instance, so a plain ``from app.api.admin import settings_router`` yields the
# router, not the module. Grab the real module from sys.modules.
settings_router = sys.modules["app.api.admin.settings_router"]

URL = "/api/v1/admin/settings/graph-memory"
FAKE_ADMIN = "admin-1"


async def _fake_admin() -> AuthContext:
    return AuthContext(user_id=FAKE_ADMIN, auth_type="jwt")


class _FakeRepo:
    def __init__(self, rows: dict[str, str]):
        self._rows = rows
        self.updates: list[tuple[str, object]] = []

    async def list_non_transcode(self):
        return [{"key": k, "value": v} for k, v in self._rows.items()]

    async def update(self, key, value, updated_by):
        self.updates.append((key, value))
        return {"key": key, "value": value}


def _seed_rows(**over) -> dict[str, str]:
    rows = {
        "graph_memory_enabled": "false",
        "graph_falkordb_host": "",
        "graph_falkordb_port": "6379",
        "graph_falkordb_database": "mediahub_memory",
        "graph_extractor_base_url": "",
        "graph_extractor_api_key": "",
        "graph_extractor_model": "",
        "graph_extractor_structured_output_mode": "json_object",
        "graph_embedder_base_url": "",
        "graph_embedder_api_key": "",
        "graph_embedder_model": "",
        # an unrelated setting that must NOT leak into the bundle
        "some_other_setting": "x",
    }
    rows.update(over)
    return rows


@pytest.fixture(autouse=True)
def _override_admin():
    app.dependency_overrides[get_admin_auth] = _fake_admin
    yield
    app.dependency_overrides.pop(get_admin_auth, None)


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    async def _noop(**_):
        return None

    monkeypatch.setattr(settings_router, "create_audit_log", _noop)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _install_repo(monkeypatch, repo):
    monkeypatch.setattr(settings_router, "get_system_settings_repository", lambda: repo)


@pytest.mark.asyncio
async def test_get_masks_api_keys(monkeypatch, client):
    repo = _FakeRepo(
        _seed_rows(
            graph_memory_enabled="true",
            graph_falkordb_host="db-host",
            graph_extractor_api_key="sk-super-secret",
            graph_extractor_model="Qwen/Qwen2.5",
        )
    )
    _install_repo(monkeypatch, repo)
    r = await client.get(URL)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["falkordb_host"] == "db-host"
    assert body["extractor_model"] == "Qwen/Qwen2.5"
    assert body["extractor_structured_output_mode"] == "json_object"
    assert body["extractor_api_key_set"] is True
    assert body["embedder_api_key_set"] is False
    # the raw key must never appear anywhere in the response
    assert "sk-super-secret" not in r.text
    assert "api_key" not in body  # only *_set booleans, no raw key field
    assert "some_other_setting" not in r.text


@pytest.mark.asyncio
async def test_put_updates_only_provided_keys(monkeypatch, client):
    repo = _FakeRepo(_seed_rows())
    _install_repo(monkeypatch, repo)
    r = await client.put(
        URL, json={"enabled": True, "falkordb_host": "h2", "extractor_model": "m"}
    )
    assert r.status_code == 200
    keys = {k for k, _ in repo.updates}
    assert keys == {
        "graph_memory_enabled",
        "graph_falkordb_host",
        "graph_extractor_model",
    }
    by_key = dict(repo.updates)
    assert by_key["graph_memory_enabled"] == "true"
    assert by_key["graph_falkordb_host"] == "h2"
    # api_key NOT touched (not provided)
    assert "graph_extractor_api_key" not in keys


@pytest.mark.asyncio
async def test_put_writes_api_key_only_when_provided(monkeypatch, client):
    repo = _FakeRepo(_seed_rows())
    _install_repo(monkeypatch, repo)
    r = await client.put(URL, json={"extractor_api_key": "sk-new"})
    assert r.status_code == 200
    by_key = dict(repo.updates)
    assert by_key.get("graph_extractor_api_key") == "sk-new"


@pytest.mark.asyncio
async def test_put_writes_structured_output_mode(monkeypatch, client):
    repo = _FakeRepo(_seed_rows())
    _install_repo(monkeypatch, repo)
    r = await client.put(URL, json={"extractor_structured_output_mode": "json_schema"})
    assert r.status_code == 200
    by_key = dict(repo.updates)
    assert by_key.get("graph_extractor_structured_output_mode") == "json_schema"


@pytest.mark.asyncio
async def test_put_rejects_invalid_structured_output_mode(monkeypatch, client):
    repo = _FakeRepo(_seed_rows())
    _install_repo(monkeypatch, repo)
    r = await client.put(URL, json={"extractor_structured_output_mode": "garbage"})
    assert r.status_code == 422
    # nothing written on a rejected payload
    assert repo.updates == []


@pytest.mark.asyncio
async def test_requires_admin(monkeypatch, client):
    app.dependency_overrides.pop(get_admin_auth, None)  # restore real admin gate
    try:
        r = await client.get(URL)
        assert r.status_code in (401, 403)
    finally:
        app.dependency_overrides[get_admin_auth] = _fake_admin
