"""Secret-at-rest self-heal sweep — unit tests with an in-memory fake DB.

- mixed-table sweep: plaintext + dev-keyed values across all four surfaces
  are rewritten to real-key ciphertext (marked for the enc:v1: surfaces,
  raw Fernet for user_mcp_servers.bearer_token).
- idempotent: an immediate second pass rewrites 0 rows.
- not configured: no-op with reason (and no DB access).
- POST /admin/settings/encrypt-secrets: 409 without a key; summary with one.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects import postgresql

from app.core import secret_box
from app.core.secure_settings import (
    MARKER,
    encrypt_byok,
    encrypt_marked,
    parse_byok_frame,
    reveal,
)
from app.services.infra import secrets_selfheal as heal_mod
from app.services.infra.secrets_selfheal import run_secrets_selfheal


def _dev_encrypt(plaintext: str) -> str:
    return (
        Fernet(secret_box.DEV_TOKEN_ENCRYPTION_KEY.encode())
        .encrypt(plaintext.encode())
        .decode()
    )


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None, scalar_value: Any = None) -> None:
        self._rows = rows or []
        self._scalar_value = scalar_value

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeOrmSession:
    def __init__(self, db: "_FakeDB") -> None:
        self._db = db

    async def execute(self, stmt: Any) -> _FakeResult:
        return self._db._dispatch(stmt)

    async def scalar(self, stmt: Any) -> Any:
        return self._db._dispatch(stmt)._scalar_value


class _ScopeCM:
    def __init__(self, session: _FakeOrmSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeOrmSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeDB:
    """Answers the ORM statements the selfheal module emits (Phase B2 Task 2
    raw-SQL → ORM rewrite) by compiling each statement to SQL text and
    pattern-matching the known shapes — same technique the compile-level
    coverage tests use, applied here as a functional in-memory fake instead
    of an assertion. Kept in-memory so tests stay DB-free."""

    def __init__(self) -> None:
        self.system_settings: dict[str, Any] = {}
        self.mediahub_models: dict[int, str] = {}  # id → api_key
        self.user_mcp_servers: dict[int, str] = {}  # id → bearer_token
        # user_id → full settings_json blob (mirrors the real jsonb column —
        # used to assert the healer's jsonb_set touches ONLY ai_providers).
        self.user_settings: dict[str, dict[str, Any]] = {}
        self.writes = 0

    def is_configured(self) -> bool:
        return True

    def read_scope(self) -> _ScopeCM:
        return _ScopeCM(_FakeOrmSession(self))

    def write_scope(self) -> _ScopeCM:
        return _ScopeCM(_FakeOrmSession(self))

    def _dispatch(self, stmt: Any) -> _FakeResult:
        sql, params = _compile(stmt)

        if sql.startswith("SELECT"):
            if "public.system_settings.key IN" in sql:
                wanted = set(params["key_1"])
                rows = [
                    {"key": k, "value": v}
                    for k, v in self.system_settings.items()
                    if k in wanted
                ]
                return _FakeResult(rows=rows)
            if "SELECT public.system_settings.value" in sql:
                return _FakeResult(
                    scalar_value=self.system_settings.get(params["key_1"])
                )
            if "public.mediahub_models.id, public.mediahub_models.api_key" in sql:
                rows = [
                    {"id": i, "api_key": v}
                    for i, v in self.mediahub_models.items()
                    if v
                ]
                return _FakeResult(rows=rows)
            if (
                "public.user_mcp_servers.id, public.user_mcp_servers.bearer_token"
                in sql
            ):
                rows = [
                    {"id": i, "bearer_token": v}
                    for i, v in self.user_mcp_servers.items()
                    if v
                ]
                return _FakeResult(rows=rows)
            if "AS ai_providers" in sql:
                rows = []
                for uid, blob in self.user_settings.items():
                    providers = (blob.get("ai_settings") or {}).get("ai_providers")
                    if providers is not None:
                        rows.append({"user_id": uid, "ai_providers": providers})
                return _FakeResult(rows=rows)
            raise AssertionError(f"unexpected SELECT: {sql}")

        if sql.startswith("UPDATE"):
            self.writes += 1
            if "UPDATE public.system_settings" in sql:
                self.system_settings[params["key_1"]] = params["value"]
                return _FakeResult()
            if "UPDATE public.mediahub_models" in sql:
                self.mediahub_models[params["id_1"]] = params["api_key"]
                return _FakeResult()
            if "UPDATE public.user_mcp_servers" in sql:
                self.user_mcp_servers[params["id_1"]] = params["bearer_token"]
                return _FakeResult()
            if "UPDATE public.user_settings" in sql:
                # Emulates jsonb_set(settings_json, '{ai_settings,ai_providers}',
                # v) — replaces ONLY that subtree, every sibling key survives.
                blob = self.user_settings.setdefault(params["user_id_1"], {})
                ai_settings = blob.setdefault("ai_settings", {})
                ai_settings["ai_providers"] = json.loads(params["param_3"])
                return _FakeResult()
            raise AssertionError(f"unexpected UPDATE: {sql}")

        raise AssertionError(f"unexpected statement: {sql}")


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    db = _FakeDB()

    import app.db.engine as engine_mod
    from app.db import session as db_session

    monkeypatch.setattr(engine_mod, "is_configured", db.is_configured)
    monkeypatch.setattr(db_session, "read_scope", db.read_scope)
    monkeypatch.setattr(db_session, "write_scope", db.write_scope)
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


# ── user_settings.ai_providers (BYOK, secret-at-rest Phase 2) ──────


@pytest.mark.asyncio
async def test_user_settings_ai_providers_mixed_rows_healed(real_key, fake_db: _FakeDB):
    """Plaintext heals to the OWNER-BOUND format; an already-bound real-keyed
    value is skipped; multi-key list elements heal independently, each bound;
    sibling settings_json keys (parse_mode, whisper_provider) survive
    untouched — the targeted jsonb_set never whole-blob replaces."""
    # A genuinely-healthy BYOK value is bound to its owner (user-1).
    already_bound = encrypt_byok("already-fine", "user-1")
    # An already-bound list element for user-2 (captured so we can assert it
    # is left byte-for-byte untouched — Fernet IV is random, not reproducible).
    bound_c = encrypt_byok("bound-c", "user-2")
    fake_db.user_settings = {
        "user-1": {
            "parse_mode": "auto",
            "ai_settings": {
                "whisper_provider": "openai",
                "ai_providers": {
                    "openai": {"api_key": "plain-openai", "base_url": "https://x"},
                    "doubao": {"api_key": already_bound, "enabled": True},
                },
            },
        },
        "user-2": {
            "ai_settings": {
                "ai_providers": {
                    # two plaintext elements to heal + one already-bound
                    "qwen": {"api_key": ["plain-a", "plain-b", bound_c]},
                },
            },
        },
        "user-3": {
            # no ai_providers at all → not touched, not even fetched as a row
            "ai_settings": {"whisper_provider": "local"},
        },
    }

    summary = await run_secrets_selfheal()

    assert summary["errors"] == 0
    # Counts rewritten FIELDS (matching _heal_platform_providers), not list
    # elements: user-1.openai.api_key (1; doubao.api_key already bound,
    # skipped) + user-2.qwen.api_key (1 field, even though 2 of its 3 list
    # elements needed healing).
    assert summary["user_settings_ai_providers"] == 2

    u1 = fake_db.user_settings["user-1"]
    assert u1["parse_mode"] == "auto"  # sibling top-level key untouched
    assert u1["ai_settings"]["whisper_provider"] == "openai"  # sibling untouched
    prov1 = u1["ai_settings"]["ai_providers"]
    assert prov1["openai"]["api_key"].startswith(MARKER)
    # healed value carries the owner-bound frame (user-1), not bare plaintext
    assert parse_byok_frame(reveal(prov1["openai"]["api_key"])) == (
        "user-1",
        "plain-openai",
    )
    assert prov1["openai"]["base_url"] == "https://x"
    assert prov1["doubao"]["api_key"] == already_bound  # untouched (already bound)
    assert prov1["doubao"]["enabled"] is True

    prov2 = fake_db.user_settings["user-2"]["ai_settings"]["ai_providers"]["qwen"][
        "api_key"
    ]
    assert parse_byok_frame(reveal(prov2[0])) == ("user-2", "plain-a")
    assert parse_byok_frame(reveal(prov2[1])) == ("user-2", "plain-b")
    assert prov2[2] == bound_c  # already bound, untouched

    assert fake_db.user_settings["user-3"] == {
        "ai_settings": {"whisper_provider": "local"}
    }


@pytest.mark.asyncio
async def test_user_settings_ai_providers_foreign_ciphertext_not_rebound(
    real_key, fake_db: _FakeDB
):
    """ATTACK/heal safety: a ciphertext that decrypts fine under the real key
    but is UNBOUND (platform format) or bound to ANOTHER user must NOT be
    rebound to this row's owner — that would launder a stolen ciphertext into
    a working key. It is left untouched (reveal still resolves it to '')."""
    platform_ct = encrypt_marked("stolen-platform-secret")  # unframed
    foreign_ct = encrypt_byok("victim-key", "some-other-user")  # bound to A
    fake_db.user_settings = {
        "user-1": {
            "ai_settings": {
                "ai_providers": {
                    "openai": {"api_key": platform_ct},
                    "doubao": {"api_key": foreign_ct},
                },
            },
        },
    }

    summary = await run_secrets_selfheal()

    # Both are left untouched (0 rewrites); errors counter unaffected — the
    # per-field ERROR logs are informational, the sweep itself does not fail.
    assert summary["user_settings_ai_providers"] == 0
    prov = fake_db.user_settings["user-1"]["ai_settings"]["ai_providers"]
    assert prov["openai"]["api_key"] == platform_ct  # NOT rebound
    assert prov["doubao"]["api_key"] == foreign_ct  # NOT rebound


@pytest.mark.asyncio
async def test_user_settings_ai_providers_second_pass_zero(real_key, fake_db: _FakeDB):
    fake_db.user_settings = {
        "user-1": {"ai_settings": {"ai_providers": {"openai": {"api_key": "plain"}}}},
    }
    first = await run_secrets_selfheal()
    assert first["user_settings_ai_providers"] == 1

    writes_after_first = fake_db.writes
    second = await run_secrets_selfheal()
    assert second["user_settings_ai_providers"] == 0
    assert fake_db.writes == writes_after_first


@pytest.mark.asyncio
async def test_user_settings_ai_providers_no_rows_is_zero(real_key, fake_db: _FakeDB):
    fake_db.user_settings = {}
    summary = await run_secrets_selfheal()
    assert summary["user_settings_ai_providers"] == 0
    assert summary["errors"] == 0


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
