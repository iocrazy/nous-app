"""Secret-at-rest hardening — unit tests for app.core.secure_settings.

Covers the scout design's test strategy:
- roundtrip: conceal_for_key marks + encrypts a registered flat secret key;
  reveal returns the original plaintext.
- passthrough: non-secret keys / non-string values are byte-identical.
- dict-field: platform.ai_providers conceals only api_key/app_id inside
  each provider entry; base_url stays plaintext.
- no-key-write-raises: with no real env key configured, conceal_for_key of
  secret material RAISES (fail-closed write, never dev-key).
- no-key-read-fail-soft: reveal of a marked value with no/wrong key logs
  an ERROR and returns "" (fail-soft read).
- repo chokepoint: SystemSettingsRepository.update / upsert_setting bind
  ciphertext (marked) for registry keys and plaintext for everything else.
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects import postgresql

import app.repositories.admin.system_settings_repository as settings_mod
from app.core import secret_box, secure_settings
from app.core.secure_settings import (
    JSONB_SECRET_KEYS,
    MARKER,
    SECRET_SETTING_KEYS,
    conceal_for_key,
    is_secret_key,
    reveal,
)
from app.models import SystemSettings
from app.repositories.admin.system_settings_repository import (
    SystemSettingsRepository,
)


@pytest.fixture
def real_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure a real env encryption key for the test."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    return key


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)


# ── registry ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_registry_covers_expected_keys():
    assert "ai_module.transcription.api_key" in SECRET_SETTING_KEYS
    assert "ai_module.embedding.api_key" in SECRET_SETTING_KEYS
    assert "telemetry.langfuse.secret_key" in SECRET_SETTING_KEYS
    assert "telemetry.langfuse.public_key" in SECRET_SETTING_KEYS
    assert "graph_extractor_api_key" in SECRET_SETTING_KEYS
    assert "graph_embedder_api_key" in SECRET_SETTING_KEYS
    assert JSONB_SECRET_KEYS == {"platform.ai_providers": ("api_key", "app_id")}
    # NOT secret:
    assert "graph_extractor_base_url" not in SECRET_SETTING_KEYS
    assert "telemetry.langfuse.host" not in SECRET_SETTING_KEYS


@pytest.mark.unit
def test_is_secret_key():
    assert is_secret_key("ai_module.caption.api_key") is True
    assert is_secret_key("platform.ai_providers") is True
    assert is_secret_key("graph_falkordb_host") is False
    assert is_secret_key("transcode_enabled") is False


# ── conceal / reveal roundtrip ─────────────────────────────────────


@pytest.mark.unit
def test_flat_secret_roundtrip(real_key):
    ct = conceal_for_key("ai_module.transcription.api_key", "sk-secret-1")
    assert isinstance(ct, str)
    assert ct.startswith(MARKER)
    assert "sk-secret-1" not in ct
    assert reveal(ct) == "sk-secret-1"


@pytest.mark.unit
def test_conceal_is_idempotent(real_key):
    once = conceal_for_key("ai_module.translation.api_key", "sk-x")
    twice = conceal_for_key("ai_module.translation.api_key", once)
    assert twice == once  # marked value never double-encrypted
    assert reveal(twice) == "sk-x"


@pytest.mark.unit
def test_non_secret_key_passthrough(real_key):
    assert conceal_for_key("graph_falkordb_host", "10.0.0.9") == "10.0.0.9"
    assert conceal_for_key("nous.user_enabled", True) is True
    assert conceal_for_key("topics.scoring", {"w": 1}) == {"w": 1}


@pytest.mark.unit
def test_blank_and_none_pass_through(real_key):
    assert conceal_for_key("ai_module.caption.api_key", "") == ""
    assert conceal_for_key("ai_module.caption.api_key", "   ") == "   "
    assert conceal_for_key("ai_module.caption.api_key", None) is None


@pytest.mark.unit
def test_platform_providers_dict_fields(real_key):
    value = {
        "doubao": {"api_key": "ark-1", "app_id": "app-9", "base_url": "https://ark/v3"},
        "qwen": {"api_key": "q-1", "base_url": ""},
    }
    out = conceal_for_key("platform.ai_providers", value)
    # secret fields encrypted per provider
    assert out["doubao"]["api_key"].startswith(MARKER)
    assert out["doubao"]["app_id"].startswith(MARKER)
    assert out["qwen"]["api_key"].startswith(MARKER)
    # non-secret sibling fields stay plaintext
    assert out["doubao"]["base_url"] == "https://ark/v3"
    assert out["qwen"]["base_url"] == ""
    # original dict NOT mutated (immutability)
    assert value["doubao"]["api_key"] == "ark-1"
    # reveal recurses into the dict shape
    back = reveal(out)
    assert back["doubao"]["api_key"] == "ark-1"
    assert back["doubao"]["app_id"] == "app-9"
    assert back["qwen"]["api_key"] == "q-1"


@pytest.mark.unit
def test_reveal_passthrough_non_marked(real_key):
    assert reveal("plain") == "plain"
    assert reveal(True) is True
    assert reveal(42) == 42
    assert reveal(None) is None
    assert reveal({"a": ["x", 1]}) == {"a": ["x", 1]}


# ── fail-closed write / fail-soft read ─────────────────────────────


@pytest.mark.unit
def test_conceal_raises_without_real_key(no_key):
    """NO dev-key fallback: secret writes fail closed when the real env key
    is absent — never silently encrypted under the public committed key."""
    with pytest.raises(secret_box.SecretBoxNotConfigured):
        conceal_for_key("ai_module.transcription.api_key", "sk-secret")
    with pytest.raises(secret_box.SecretBoxNotConfigured):
        conceal_for_key("platform.ai_providers", {"doubao": {"api_key": "k"}})


@pytest.mark.unit
def test_conceal_never_uses_dev_key(no_key):
    """Even though secret_box's default path would fall back to
    DEV_TOKEN_ENCRYPTION_KEY, the strict conceal path must not."""
    with pytest.raises(secret_box.SecretBoxNotConfigured):
        secure_settings.encrypt_marked("sk-secret")


@pytest.mark.unit
def test_reveal_fail_soft_on_wrong_key(monkeypatch):
    key_a = Fernet.generate_key().decode()
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", key_a)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    ct = conceal_for_key("ai_module.caption.api_key", "sk-1")
    # rotate to an unrelated key without keeping _OLD → decrypt fails
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert reveal(ct) == ""  # ERROR-logged inside reveal; read never raises


@pytest.mark.unit
def test_reveal_fail_soft_when_no_key(no_key):
    marked = MARKER + "gAAAAA-not-actually-decryptable"
    assert reveal(marked) == ""


@pytest.mark.unit
def test_reveal_decrypts_via_old_key_rotation(monkeypatch):
    """MultiFernet incl. _OLD: after rotation, old-key ciphertext still reads."""
    old = Fernet.generate_key().decode()
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", old)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    ct = conceal_for_key("graph_extractor_api_key", "ms-key")
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", old)
    assert reveal(ct) == "ms-key"


# ── repo chokepoint (fake session, same pattern as the repo's own tests) ──


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows: list[Any] = []

    async def execute(self, stmt: Any) -> _FakeResult:
        compiled = stmt.compile(dialect=postgresql.dialect())
        self.calls.append((str(compiled), dict(compiled.params)))
        return _FakeResult(self.rows)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(settings_mod, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(settings_mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.mark.asyncio
async def test_repo_update_encrypts_secret_key(real_key, fake_session):
    fake_session.rows = [SystemSettings(key="ai_module.caption.api_key", value="x")]
    await SystemSettingsRepository().update(
        "ai_module.caption.api_key", "sk-choke", "admin-1"
    )
    _sql, binds = fake_session.calls[-1]
    bound = [v for v in binds.values() if isinstance(v, str)]
    assert "sk-choke" not in bound  # plaintext never bound
    assert any(v.startswith(MARKER) for v in bound)


@pytest.mark.asyncio
async def test_repo_upsert_encrypts_secret_key(real_key, fake_session):
    fake_session.rows = [SystemSettings(key="telemetry.langfuse.secret_key", value="x")]
    await SystemSettingsRepository().upsert_setting(
        "telemetry.langfuse.secret_key", "sk-lf-choke", "admin-1"
    )
    _sql, binds = fake_session.calls[-1]
    bound = [v for v in binds.values() if isinstance(v, str)]
    assert "sk-lf-choke" not in bound
    assert any(v.startswith(MARKER) for v in bound)


@pytest.mark.asyncio
async def test_repo_upsert_platform_providers_field_level(real_key, fake_session):
    fake_session.rows = []
    await SystemSettingsRepository().upsert_setting(
        "platform.ai_providers",
        {"doubao": {"api_key": "ark-raw", "base_url": "https://ark/v3"}},
        "admin-1",
    )
    _sql, binds = fake_session.calls[-1]
    stored = next(v for v in binds.values() if isinstance(v, dict))
    assert stored["doubao"]["api_key"].startswith(MARKER)
    assert stored["doubao"]["base_url"] == "https://ark/v3"


@pytest.mark.asyncio
async def test_repo_update_non_secret_key_untouched(real_key, fake_session):
    fake_session.rows = [SystemSettings(key="graph_falkordb_host", value="h")]
    await SystemSettingsRepository().update("graph_falkordb_host", "10.0.0.9", "admin")
    _sql, binds = fake_session.calls[-1]
    assert "10.0.0.9" in binds.values()  # exactly the plaintext, no marker


@pytest.mark.asyncio
async def test_repo_update_secret_raises_without_key(no_key, fake_session):
    with pytest.raises(secret_box.SecretBoxNotConfigured):
        await SystemSettingsRepository().update(
            "ai_module.caption.api_key", "sk-x", "admin"
        )
    assert fake_session.calls == []  # nothing reached the DB
