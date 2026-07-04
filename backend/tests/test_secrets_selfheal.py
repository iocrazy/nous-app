"""Secret-at-rest self-heal sweep — unit tests with an in-memory fake DB.

- mixed-table sweep: plaintext + dev-keyed values across all four surfaces
  are rewritten to real-key ciphertext (marked for the enc:v1: surfaces,
  raw Fernet for user_mcp_servers.bearer_token).
- idempotent: an immediate second pass rewrites 0 rows.
- not configured: no-op with reason (and no DB access).
- POST /admin/settings/encrypt-secrets: 409 without a key; summary with one.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from cryptography.fernet import Fernet

from app.core import secret_box
from app.core.secure_settings import MARKER, encrypt_marked, reveal
from app.services.infra import secrets_selfheal as heal_mod
from app.services.infra.secrets_selfheal import run_secrets_selfheal


def _dev_encrypt(plaintext: str) -> str:
    return (
        Fernet(secret_box.DEV_TOKEN_ENCRYPTION_KEY.encode())
        .encrypt(plaintext.encode())
        .decode()
    )


class _FakeDB:
    """Answers the exact SQL shapes the selfheal module emits."""

    def __init__(self) -> None:
        self.system_settings: dict[str, Any] = {}
        self.mediahub_models: dict[int, str] = {}  # id → api_key
        self.user_mcp_servers: dict[int, str] = {}  # id → bearer_token
        self.writes = 0

    def is_configured(self) -> bool:
        return True

    async def fetch_all(self, sql: str, params: dict | None = None):
        params = params or {}
        if "FROM public.system_settings" in sql and "IN (" in sql:
            wanted = set(params.values())
            return [
                {"key": k, "value": v}
                for k, v in self.system_settings.items()
                if k in wanted
            ]
        if "FROM public.mediahub_models" in sql:
            return [
                {"id": i, "api_key": v} for i, v in self.mediahub_models.items() if v
            ]
        if "FROM public.user_mcp_servers" in sql:
            return [
                {"id": i, "bearer_token": v}
                for i, v in self.user_mcp_servers.items()
                if v
            ]
        raise AssertionError(f"unexpected fetch_all: {sql}")

    async def fetch_val(self, sql: str, params: dict | None = None):
        params = params or {}
        if "FROM public.system_settings" in sql:
            return self.system_settings.get(params["k"])
        raise AssertionError(f"unexpected fetch_val: {sql}")

    async def execute(self, sql: str, params: dict | None = None) -> int:
        import json

        params = params or {}
        self.writes += 1
        if "UPDATE public.system_settings" in sql:
            self.system_settings[params["k"]] = json.loads(params["v"])
            return 1
        if "UPDATE public.mediahub_models" in sql:
            self.mediahub_models[params["id"]] = params["v"]
            return 1
        if "UPDATE public.user_mcp_servers" in sql:
            self.user_mcp_servers[params["id"]] = params["v"]
            return 1
        raise AssertionError(f"unexpected execute: {sql}")


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    db = _FakeDB()

    import app.db.engine as engine_mod

    monkeypatch.setattr(engine_mod, "is_configured", db.is_configured)
    monkeypatch.setattr(engine_mod, "fetch_all", db.fetch_all)
    monkeypatch.setattr(engine_mod, "fetch_val", db.fetch_val)
    monkeypatch.setattr(engine_mod, "execute", db.execute)
    return db


@pytest.fixture
def real_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    return key


@pytest.mark.asyncio
async def test_mixed_table_sweep_marks_everything(real_key, fake_db: _FakeDB):
    already_good = encrypt_marked("already-fine")
    fake_db.system_settings = {
        "ai_module.caption.api_key": "plain-caption-key",  # plaintext → heal
        "telemetry.langfuse.secret_key": already_good,  # real-keyed → skip
        "graph_extractor_api_key": MARKER + _dev_encrypt("dev-keyed"),  # → heal
        "graph_falkordb_host": "10.0.0.9",  # non-secret key, not scanned
        "platform.ai_providers": {
            "doubao": {"api_key": "plain-ark", "base_url": "https://ark/v3"},
        },
    }
    fake_db.mediahub_models = {1: "plain-model-key", 2: encrypt_marked("ok")}
    fake_db.user_mcp_servers = {
        10: _dev_encrypt("dev-keyed-token"),  # dev Fernet → re-encrypt
        11: "legacy-plaintext-token",  # plaintext → encrypt
        12: secret_box.encrypt("real-token", allow_dev_fallback=False),  # skip
    }

    summary = await run_secrets_selfheal()

    assert summary["ok"] is True
    assert summary["errors"] == 0
    assert summary["system_settings"] == 2  # caption + graph_extractor
    assert summary["platform_ai_providers"] == 1  # doubao.api_key
    assert summary["mediahub_models"] == 1
    assert summary["user_mcp_servers"] == 2

    # Everything decrypts back under the real key (no dev fallback).
    ss = fake_db.system_settings
    assert ss["ai_module.caption.api_key"].startswith(MARKER)
    assert reveal(ss["ai_module.caption.api_key"]) == "plain-caption-key"
    assert reveal(ss["graph_extractor_api_key"]) == "dev-keyed"
    assert ss["telemetry.langfuse.secret_key"] == already_good  # untouched
    assert ss["graph_falkordb_host"] == "10.0.0.9"
    prov = ss["platform.ai_providers"]["doubao"]
    assert prov["api_key"].startswith(MARKER)
    assert reveal(prov["api_key"]) == "plain-ark"
    assert prov["base_url"] == "https://ark/v3"
    assert reveal(fake_db.mediahub_models[1]) == "plain-model-key"
    assert (
        secret_box.decrypt(fake_db.user_mcp_servers[10], allow_dev_fallback=False)
        == "dev-keyed-token"
    )
    assert (
        secret_box.decrypt(fake_db.user_mcp_servers[11], allow_dev_fallback=False)
        == "legacy-plaintext-token"
    )


@pytest.mark.asyncio
async def test_second_pass_is_zero_rewrites(real_key, fake_db: _FakeDB):
    fake_db.system_settings = {
        "ai_module.caption.api_key": "plain-1",
        "platform.ai_providers": {"qwen": {"api_key": "plain-2"}},
    }
    fake_db.mediahub_models = {1: "plain-3"}
    fake_db.user_mcp_servers = {1: "plain-4"}

    first = await run_secrets_selfheal()
    assert (
        first["system_settings"]
        + first["platform_ai_providers"]
        + first["mediahub_models"]
        + first["user_mcp_servers"]
        == 4
    )

    writes_after_first = fake_db.writes
    second = await run_secrets_selfheal()
    assert second["system_settings"] == 0
    assert second["platform_ai_providers"] == 0
    assert second["mediahub_models"] == 0
    assert second["user_mcp_servers"] == 0
    assert fake_db.writes == writes_after_first  # zero extra UPDATEs


@pytest.mark.asyncio
async def test_not_configured_is_noop(monkeypatch, fake_db: _FakeDB):
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    fake_db.system_settings = {"ai_module.caption.api_key": "plain"}
    summary = await run_secrets_selfheal()
    assert summary == {"ok": False, "reason": "encryption_key_not_configured"}
    assert fake_db.writes == 0
    assert fake_db.system_settings["ai_module.caption.api_key"] == "plain"


@pytest.mark.asyncio
async def test_unhealable_ciphertext_left_alone(real_key, fake_db: _FakeDB):
    """Marked value under a rotated-away key: neither real nor dev decrypts —
    left untouched (ERROR logged inside), not clobbered."""
    foreign = Fernet(Fernet.generate_key()).encrypt(b"lost").decode()
    fake_db.system_settings = {"graph_embedder_api_key": MARKER + foreign}
    summary = await run_secrets_selfheal()
    assert summary["system_settings"] == 0
    assert fake_db.system_settings["graph_embedder_api_key"] == MARKER + foreign


@pytest.mark.asyncio
async def test_old_key_rotation_counts_as_healthy(monkeypatch, fake_db: _FakeDB):
    """A value under _OLD is real-key decryptable (MultiFernet) — no rewrite."""
    old = Fernet.generate_key().decode()
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", old)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    ct = encrypt_marked("v")
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", old)
    fake_db.system_settings = {"graph_extractor_api_key": ct}
    summary = await run_secrets_selfheal()
    assert summary["system_settings"] == 0
    assert fake_db.system_settings["graph_extractor_api_key"] == ct


# ── POST /admin/settings/encrypt-secrets ───────────────────────────


@pytest.mark.asyncio
async def test_encrypt_secrets_endpoint_409_without_key(monkeypatch):
    import pytest_asyncio  # noqa: F401 — ensure plugin importable
    from httpx import ASGITransport, AsyncClient

    from app.core.admin_deps import get_admin_auth
    from app.core.deps import AuthContext
    from app.main import app

    async def _fake_admin() -> AuthContext:
        return AuthContext(user_id="admin-1", auth_type="jwt")

    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", raising=False)
    app.dependency_overrides[get_admin_auth] = _fake_admin
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/v1/admin/settings/encrypt-secrets")
        assert r.status_code == 409
        # exception envelope puts the message in "error", not raw "detail"
        assert "MEDIAHUB_TOKEN_ENCRYPTION_KEY" in r.text
    finally:
        app.dependency_overrides.pop(get_admin_auth, None)


@pytest.mark.asyncio
async def test_encrypt_secrets_endpoint_runs_sweep(monkeypatch, real_key):
    from httpx import ASGITransport, AsyncClient

    from app.core.admin_deps import get_admin_auth
    from app.core.deps import AuthContext
    from app.main import app

    async def _fake_admin() -> AuthContext:
        return AuthContext(user_id="admin-1", auth_type="jwt")

    async def _fake_sweep():
        return {
            "ok": True,
            "errors": 0,
            "system_settings": 3,
            "platform_ai_providers": 1,
            "mediahub_models": 2,
            "user_mcp_servers": 5,
        }

    monkeypatch.setattr(heal_mod, "run_secrets_selfheal", _fake_sweep)
    app.dependency_overrides[get_admin_auth] = _fake_admin
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/v1/admin/settings/encrypt-secrets")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["user_mcp_servers"] == 5
    finally:
        app.dependency_overrides.pop(get_admin_auth, None)


# ── bootstrap warning wiring ────────────────────────────────────────


@pytest.mark.asyncio
async def test_bootstrap_warns_when_not_configured(monkeypatch, capsys):
    from app.startup.bootstrap import _bg_secrets_selfheal

    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", raising=False)

    warnings: list[str] = []

    import app.startup.bootstrap as bootstrap_mod

    class _CaptureLogger:
        def __getattr__(self, name):
            def _log(msg, *a, **kw):
                if name == "warning":
                    warnings.append(str(msg))

            return _log

    monkeypatch.setattr(bootstrap_mod, "logger", _CaptureLogger())
    await _bg_secrets_selfheal()
    assert any(
        re.search(r"MEDIAHUB_TOKEN_ENCRYPTION_KEY is NOT set", w) for w in warnings
    )


@pytest.mark.asyncio
async def test_bootstrap_runs_sweep_when_configured(monkeypatch, real_key):
    from app.startup.bootstrap import _bg_secrets_selfheal

    calls: list[bool] = []

    async def _fake_sweep():
        calls.append(True)
        return {"ok": True}

    monkeypatch.setattr(heal_mod, "run_secrets_selfheal", _fake_sweep)
    await _bg_secrets_selfheal()
    assert calls == [True]
