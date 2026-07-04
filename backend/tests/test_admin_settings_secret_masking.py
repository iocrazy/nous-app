"""Secret-at-rest hardening — server-side masking in the admin settings API.

- GET /admin/settings list: registered secret keys' values become
  ``{"set": bool}`` — never plaintext, never ``enc:v1:`` ciphertext.
- PATCH /admin/settings/{key} echo: same masking (routes through
  ``_to_response``).
- GET/PUT /admin/settings/platform-ai-providers: masked per-provider
  ``api_key_set`` / ``app_id_set``; PUT blank-means-keep semantics.
"""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from app.core.admin_deps import get_admin_auth
from app.core.deps import AuthContext
from app.main import app

settings_router = sys.modules["app.api.admin.settings_router"]

BASE = "/api/v1/admin/settings"
FAKE_ADMIN = "admin-1"


async def _fake_admin() -> AuthContext:
    return AuthContext(user_id=FAKE_ADMIN, auth_type="jwt")


class _FakeRepo:
    """In-memory system_settings store; mirrors the real repo's conceal
    chokepoint by delegating to conceal_for_key on writes."""

    def __init__(self, rows: dict[str, object]):
        self._rows = dict(rows)
        self.upserts: list[tuple[str, object]] = []

    async def list_non_transcode(self):
        return [
            {"key": k, "value": v, "updated_at": "2026-01-01T00:00:00"}
            for k, v in self._rows.items()
        ]

    async def exists(self, key):
        return key in self._rows

    async def update(self, key, value, updated_by):
        from app.core.secure_settings import conceal_for_key

        concealed = conceal_for_key(key, value)
        self._rows[key] = concealed
        return {"key": key, "value": concealed, "updated_at": "2026-01-01T00:00:00"}

    async def upsert_setting(self, key, value, updated_by):
        from app.core.secure_settings import conceal_for_key

        concealed = conceal_for_key(key, value)
        self._rows[key] = concealed
        self.upserts.append((key, concealed))
        return {"key": key, "value": concealed}


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


@pytest.fixture(autouse=True)
def _real_key(monkeypatch):
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _install_repo(monkeypatch, repo):
    monkeypatch.setattr(settings_router, "get_system_settings_repository", lambda: repo)


# ── list GET masking ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_masks_secret_keys(monkeypatch, client):
    repo = _FakeRepo(
        {
            "ai_module.caption.api_key": "sk-raw-secret",
            "telemetry.langfuse.secret_key": "enc:v1:gAAAAAsomething",
            "graph_falkordb_host": "10.0.0.9",
        }
    )
    _install_repo(monkeypatch, repo)
    r = await client.get(BASE)
    assert r.status_code == 200
    body = {row["key"]: row["value"] for row in r.json()}
    assert body["ai_module.caption.api_key"] == {"set": True}
    assert body["telemetry.langfuse.secret_key"] == {"set": True}
    assert body["graph_falkordb_host"] == "10.0.0.9"  # non-secret untouched
    assert "sk-raw-secret" not in r.text
    assert "enc:v1:" not in r.text


@pytest.mark.asyncio
async def test_list_masks_platform_providers(monkeypatch, client):
    repo = _FakeRepo(
        {"platform.ai_providers": {"doubao": {"api_key": "enc:v1:gAAAAAx"}}}
    )
    _install_repo(monkeypatch, repo)
    r = await client.get(BASE)
    assert r.status_code == 200
    body = {row["key"]: row["value"] for row in r.json()}
    assert body["platform.ai_providers"] == {"set": True}
    assert "enc:v1:" not in r.text


@pytest.mark.asyncio
async def test_list_secret_absent_value_set_false(monkeypatch, client):
    repo = _FakeRepo({"ai_module.caption.api_key": ""})
    _install_repo(monkeypatch, repo)
    r = await client.get(BASE)
    body = {row["key"]: row["value"] for row in r.json()}
    assert body["ai_module.caption.api_key"] == {"set": False}


# ── PATCH echo masking ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_secret_key_echo_is_masked(monkeypatch, client):
    repo = _FakeRepo({"ai_module.caption.api_key": "old"})
    _install_repo(monkeypatch, repo)
    r = await client.patch(
        f"{BASE}/ai_module.caption.api_key", json={"value": "sk-new-secret"}
    )
    assert r.status_code == 200
    assert r.json()["value"] == {"set": True}
    assert "sk-new-secret" not in r.text
    assert "enc:v1:" not in r.text
    # And the store actually holds ciphertext, not plaintext.
    assert str(repo._rows["ai_module.caption.api_key"]).startswith("enc:v1:")


@pytest.mark.asyncio
async def test_patch_non_secret_key_echo_unmasked(monkeypatch, client):
    repo = _FakeRepo({"graph_falkordb_host": "old-host"})
    _install_repo(monkeypatch, repo)
    r = await client.patch(f"{BASE}/graph_falkordb_host", json={"value": "new-host"})
    assert r.status_code == 200
    assert r.json()["value"] == "new-host"


# ── platform-ai-providers endpoint ─────────────────────────────────


@pytest.mark.asyncio
async def test_get_platform_providers_masked(monkeypatch, client):
    repo = _FakeRepo(
        {
            "platform.ai_providers": {
                "doubao": {
                    "api_key": "enc:v1:gAAAAAct",
                    "app_id": "",
                    "base_url": "https://ark/v3",
                },
                "qwen": {"api_key": ""},
            }
        }
    )
    _install_repo(monkeypatch, repo)
    r = await client.get(f"{BASE}/platform-ai-providers")
    assert r.status_code == 200
    body = r.json()["providers"]
    assert body["doubao"]["api_key_set"] is True
    assert body["doubao"]["app_id_set"] is False
    assert body["doubao"]["base_url"] == "https://ark/v3"
    assert body["qwen"]["api_key_set"] is False
    assert "enc:v1:" not in r.text


@pytest.mark.asyncio
async def test_get_platform_providers_absent_is_empty(monkeypatch, client):
    repo = _FakeRepo({})
    _install_repo(monkeypatch, repo)
    r = await client.get(f"{BASE}/platform-ai-providers")
    assert r.status_code == 200
    assert r.json() == {"providers": {}}


@pytest.mark.asyncio
async def test_put_platform_providers_writes_and_encrypts(monkeypatch, client):
    repo = _FakeRepo({})
    _install_repo(monkeypatch, repo)
    r = await client.put(
        f"{BASE}/platform-ai-providers",
        json={
            "providers": {
                "doubao": {"api_key": "ark-new", "base_url": "https://ark/v3"}
            }
        },
    )
    assert r.status_code == 200
    assert r.json()["providers"]["doubao"]["api_key_set"] is True
    assert "ark-new" not in r.text  # raw key never echoed
    key, stored = repo.upserts[-1]
    assert key == "platform.ai_providers"
    assert stored["doubao"]["api_key"].startswith("enc:v1:")
    assert stored["doubao"]["base_url"] == "https://ark/v3"


@pytest.mark.asyncio
async def test_put_platform_providers_blank_means_keep(monkeypatch, client):
    from app.core.secure_settings import conceal_for_key, reveal

    existing = conceal_for_key(
        "platform.ai_providers",
        {"doubao": {"api_key": "ark-old", "base_url": "https://old/v3"}},
    )
    repo = _FakeRepo({"platform.ai_providers": existing})
    _install_repo(monkeypatch, repo)
    r = await client.put(
        f"{BASE}/platform-ai-providers",
        json={"providers": {"doubao": {"api_key": "", "base_url": "https://new/v3"}}},
    )
    assert r.status_code == 200
    _key, stored = repo.upserts[-1]
    # base_url updated; blank api_key kept the stored ciphertext.
    assert stored["doubao"]["base_url"] == "https://new/v3"
    assert reveal(stored["doubao"]["api_key"]) == "ark-old"
    assert r.json()["providers"]["doubao"]["api_key_set"] is True


@pytest.mark.asyncio
async def test_put_platform_providers_untouched_providers_survive(monkeypatch, client):
    from app.core.secure_settings import conceal_for_key, reveal

    existing = conceal_for_key("platform.ai_providers", {"qwen": {"api_key": "q-old"}})
    repo = _FakeRepo({"platform.ai_providers": existing})
    _install_repo(monkeypatch, repo)
    r = await client.put(
        f"{BASE}/platform-ai-providers",
        json={"providers": {"doubao": {"api_key": "ark-1"}}},
    )
    assert r.status_code == 200
    _key, stored = repo.upserts[-1]
    assert reveal(stored["qwen"]["api_key"]) == "q-old"  # untouched provider kept
    assert reveal(stored["doubao"]["api_key"]) == "ark-1"


@pytest.mark.asyncio
async def test_platform_providers_requires_admin(monkeypatch, client):
    app.dependency_overrides.pop(get_admin_auth, None)
    try:
        r = await client.get(f"{BASE}/platform-ai-providers")
        assert r.status_code in (401, 403)
    finally:
        app.dependency_overrides[get_admin_auth] = _fake_admin
