"""BYOK provider key encryption at rest (secret-at-rest Phase 2) — the
/ai/settings PUT/GET seam.

Covers:
- PUT encrypts a freshly-supplied api_key before it hits
  ``patch_settings_json`` (the stored value carries the ``enc:v1:`` marker).
- PUT's blank-means-keep path forwards the EXISTING ciphertext byte-for-byte
  (never re-encrypted / never decrypted).
- PUT/GET responses mask ``api_key`` — the client never sees plaintext OR
  ciphertext, only ``api_key_set`` / ``api_key_hint`` / ``api_key_count``.
- Multi-key list[str] rotation shape round-trips through the same seam.
- No real encryption key configured → PUT fails closed (500), never persists
  plaintext.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.core.secure_settings import MARKER
from app.schemas.ai import AISettingsUpdate


def _fake_auth(user_id="user-1"):
    auth = MagicMock()
    auth.user_id = user_id
    return auth


@pytest.fixture
def real_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    return key


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)


# ── PUT encrypts on write ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_encrypts_new_api_key(real_key):
    from app.api.ai_settings_router import save_ai_settings

    with patch("app.api.ai_settings_router.UserSettingsRepository") as repo_cls:
        repo_cls.return_value.get_by_user_id = AsyncMock(return_value=None)
        patch_mock = AsyncMock(return_value={})
        repo_cls.return_value.patch_settings_json = patch_mock

        body = AISettingsUpdate(ai_providers={"openai": {"api_key": "sk-live-123"}})
        resp = await save_ai_settings(body, _fake_auth("u1"))

    stored_patch = patch_mock.call_args.args[1]
    stored_key = stored_patch["ai_settings"]["ai_providers"]["openai"]["api_key"]
    assert stored_key.startswith(MARKER)
    assert "sk-live-123" not in stored_key

    # response never carries the raw key
    assert "api_key" not in resp.ai_providers["openai"]
    assert resp.ai_providers["openai"]["api_key_set"] is True
    assert resp.ai_providers["openai"]["api_key_hint"] == "-123"
    assert resp.ai_providers["openai"]["api_key_count"] == 1


@pytest.mark.asyncio
async def test_put_blank_keeps_existing_ciphertext_unchanged(real_key):
    from app.api.ai_settings_router import save_ai_settings
    from app.core.secure_settings import encrypt_marked

    stored_ciphertext = encrypt_marked("sk-already-encrypted")
    existing = {
        "settings_json": {
            "ai_settings": {
                "ai_providers": {
                    "openai": {"api_key": stored_ciphertext, "enabled": True}
                }
            }
        }
    }

    with patch("app.api.ai_settings_router.UserSettingsRepository") as repo_cls:
        repo_cls.return_value.get_by_user_id = AsyncMock(return_value=existing)
        patch_mock = AsyncMock(return_value={})
        repo_cls.return_value.patch_settings_json = patch_mock

        # blank api_key in the payload means "unchanged"
        body = AISettingsUpdate(
            ai_providers={"openai": {"api_key": "", "enabled": False}}
        )
        resp = await save_ai_settings(body, _fake_auth("u1"))

    stored_patch = patch_mock.call_args.args[1]
    stored_key = stored_patch["ai_settings"]["ai_providers"]["openai"]["api_key"]
    # byte-for-byte identical — not re-encrypted (no double marker / new IV)
    assert stored_key == stored_ciphertext
    assert stored_patch["ai_settings"]["ai_providers"]["openai"]["enabled"] is False

    assert resp.ai_providers["openai"]["api_key_set"] is True
    assert resp.ai_providers["openai"]["api_key_hint"] == "pted"  # last 4 of plaintext


@pytest.mark.asyncio
async def test_put_multi_key_list_encrypts_each_element(real_key):
    from app.api.ai_settings_router import save_ai_settings

    with patch("app.api.ai_settings_router.UserSettingsRepository") as repo_cls:
        repo_cls.return_value.get_by_user_id = AsyncMock(return_value=None)
        patch_mock = AsyncMock(return_value={})
        repo_cls.return_value.patch_settings_json = patch_mock

        body = AISettingsUpdate(
            ai_providers={"qwen": {"api_key": ["sk-a111", "sk-b222"]}}
        )
        resp = await save_ai_settings(body, _fake_auth("u1"))

    stored_patch = patch_mock.call_args.args[1]
    stored_keys = stored_patch["ai_settings"]["ai_providers"]["qwen"]["api_key"]
    assert isinstance(stored_keys, list)
    assert all(k.startswith(MARKER) for k in stored_keys)

    assert resp.ai_providers["qwen"]["api_key_set"] is True
    assert resp.ai_providers["qwen"]["api_key_count"] == 2
    assert resp.ai_providers["qwen"]["api_key_hint"] == "a111"


@pytest.mark.asyncio
async def test_put_no_real_key_fails_closed(no_key):
    from app.api.ai_settings_router import save_ai_settings

    with patch("app.api.ai_settings_router.UserSettingsRepository") as repo_cls:
        repo_cls.return_value.get_by_user_id = AsyncMock(return_value=None)
        patch_mock = AsyncMock(return_value={})
        repo_cls.return_value.patch_settings_json = patch_mock

        body = AISettingsUpdate(ai_providers={"openai": {"api_key": "sk-live"}})
        with pytest.raises(HTTPException) as exc_info:
            await save_ai_settings(body, _fake_auth("u1"))

    assert exc_info.value.status_code == 500
    patch_mock.assert_not_awaited()  # never reached the DB with plaintext


# ── GET masks stored keys ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_masks_encrypted_key(real_key):
    from app.api.ai_settings_router import get_ai_settings
    from app.core.secure_settings import encrypt_marked

    stored_ciphertext = encrypt_marked("sk-secret-9999")
    stored = {
        "settings_json": {
            "ai_settings": {"ai_providers": {"openai": {"api_key": stored_ciphertext}}}
        }
    }
    with patch("app.api.ai_settings_router.UserSettingsRepository") as repo_cls:
        repo_cls.return_value.get_by_user_id = AsyncMock(return_value=stored)
        resp = await get_ai_settings(_fake_auth("u1"))

    entry = resp.ai_providers["openai"]
    assert "api_key" not in entry
    assert entry["api_key_set"] is True
    assert entry["api_key_hint"] == "9999"
    assert entry["api_key_count"] == 1
    # ciphertext never leaks into the response in any field
    assert MARKER not in str(resp.ai_providers)
    assert stored_ciphertext not in str(resp.ai_providers)


@pytest.mark.asyncio
async def test_get_masks_legacy_plaintext_key(real_key):
    """Pre-Phase-2 rows may still carry a plaintext api_key — GET must mask
    it the same way (never expose it), same as encrypted rows."""
    from app.api.ai_settings_router import get_ai_settings

    stored = {
        "settings_json": {
            "ai_settings": {"ai_providers": {"openai": {"api_key": "sk-legacy-plain"}}}
        }
    }
    with patch("app.api.ai_settings_router.UserSettingsRepository") as repo_cls:
        repo_cls.return_value.get_by_user_id = AsyncMock(return_value=stored)
        resp = await get_ai_settings(_fake_auth("u1"))

    entry = resp.ai_providers["openai"]
    assert "api_key" not in entry
    assert entry["api_key_set"] is True
    assert entry["api_key_hint"] == "lain"


@pytest.mark.asyncio
async def test_get_unset_key_reports_not_set():
    from app.api.ai_settings_router import get_ai_settings

    stored = {
        "settings_json": {
            "ai_settings": {"ai_providers": {"ollama": {"base_url": "http://x"}}}
        }
    }
    with patch("app.api.ai_settings_router.UserSettingsRepository") as repo_cls:
        repo_cls.return_value.get_by_user_id = AsyncMock(return_value=stored)
        resp = await get_ai_settings(_fake_auth("u1"))

    entry = resp.ai_providers["ollama"]
    assert entry["api_key_set"] is False
    assert entry["api_key_hint"] == ""
    assert entry["api_key_count"] == 0
    assert entry["base_url"] == "http://x"  # non-secret field untouched
