"""``_load_user_provider_config`` — the chat-turn BYOK loader in
``ai_library_chat_wiring.py`` reads ``user_settings.settings_json`` directly
via the supabase-py admin client (not via ``get_ai_settings``), so it needs
its own ``reveal_byok_providers`` chokepoint (secret-at-rest Phase 2).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from app.services.ai.chat.ai_library_chat_wiring import _load_user_provider_config

pytestmark = pytest.mark.asyncio


def _fake_client(settings_json: dict):
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.maybe_single.return_value = chain
    chain.execute = AsyncMock(
        return_value=MagicMock(data={"settings_json": settings_json})
    )
    client = MagicMock()
    client.table.return_value = chain
    return client


@pytest.fixture
def real_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    return key


async def test_reveals_encrypted_api_key(real_key):
    from app.core.secure_settings import encrypt_byok

    owner = uuid4()
    # Owner-bound to the same user the loader is called for.
    ciphertext = encrypt_byok("sk-chat-plain", str(owner))
    settings_json = {
        "ai_settings": {
            "ai_providers": {"openai": {"api_key": ciphertext, "base_url": "https://x"}}
        }
    }
    with patch(
        "app.db.supabase_client.get_async_supabase_admin",
        AsyncMock(return_value=_fake_client(settings_json)),
    ):
        providers = await _load_user_provider_config(owner)

    assert providers["openai"]["api_key"] == "sk-chat-plain"
    assert providers["openai"]["base_url"] == "https://x"


async def test_cross_user_replay_blocked(real_key):
    """A ciphertext bound to a DIFFERENT user, planted in this user's row,
    reveals to '' (ownership-binding anti-replay)."""
    from app.core.secure_settings import encrypt_byok

    victim_ct = encrypt_byok("sk-victim", "some-other-user")
    settings_json = {
        "ai_settings": {"ai_providers": {"openai": {"api_key": victim_ct}}}
    }
    with patch(
        "app.db.supabase_client.get_async_supabase_admin",
        AsyncMock(return_value=_fake_client(settings_json)),
    ):
        providers = await _load_user_provider_config(uuid4())

    assert providers["openai"]["api_key"] == ""


async def test_passthrough_legacy_plaintext(real_key):
    settings_json = {
        "ai_settings": {"ai_providers": {"openai": {"api_key": "sk-legacy-plain"}}}
    }
    with patch(
        "app.db.supabase_client.get_async_supabase_admin",
        AsyncMock(return_value=_fake_client(settings_json)),
    ):
        providers = await _load_user_provider_config(uuid4())

    assert providers["openai"]["api_key"] == "sk-legacy-plain"


async def test_empty_when_no_row():
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.maybe_single.return_value = chain
    chain.execute = AsyncMock(return_value=MagicMock(data=None))
    client = MagicMock()
    client.table.return_value = chain

    with patch(
        "app.db.supabase_client.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        providers = await _load_user_provider_config(uuid4())

    assert providers == {}
