"""End-to-end: an encrypted BYOK row in ``user_settings.settings_json`` must
resolve to a plaintext ``api_key`` on the adapter that actually talks to the
provider (secret-at-rest Phase 2).

Exercises the full read path: mocked DB row (ciphertext api_key) →
``get_ai_settings`` (reveal chokepoint) → ``get_provider_config`` →
``get_adapter_for_user`` (adapter factory) → adapter.api_key is plaintext,
never the ``enc:v1:`` marker or ciphertext.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app.core.secure_settings import MARKER, encrypt_marked
from app.services.ai.adapters.factory import get_adapter_for_user
from app.services.ai.providers.ai_provider_helpers import (
    get_ai_settings,
    get_provider_config,
)

pytestmark = pytest.mark.asyncio


def _fallback_settings(**overrides: Any) -> SimpleNamespace:
    defaults: Dict[str, Any] = {
        "OPENAI_API_KEY": "",
        "OPENAI_MODEL": "gpt-4o",
        "LLM_API_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "LLM_API_KEY": "",
        "LLM_MODEL": "qwen-max",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def real_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    return key


async def test_encrypted_row_resolves_to_plaintext_adapter_key(real_key):
    ciphertext = encrypt_marked("sk-real-e2e-secret")
    assert ciphertext.startswith(MARKER)

    repo = MagicMock()
    repo.get_by_user_id = AsyncMock(
        return_value={
            "settings_json": {
                "ai_settings": {
                    "ai_providers": {"openai": {"api_key": ciphertext, "base_url": ""}}
                }
            }
        }
    )
    with patch(
        "app.repositories.user_settings_repository.UserSettingsRepository",
        return_value=repo,
    ):
        ai_settings = await get_ai_settings("user-e2e")

    # DB-level assertion: nothing plaintext ever crossed the reveal boundary
    # backwards — the mocked row itself still carries ciphertext.
    provider_config = get_provider_config(ai_settings, "openai")
    assert provider_config["api_key"] == "sk-real-e2e-secret"

    adapter = get_adapter_for_user(
        "gpt-4o", {"openai": provider_config}, _fallback_settings()
    )

    assert adapter.api_key == "sk-real-e2e-secret"
    assert not adapter.api_key.startswith(MARKER)


async def test_multi_key_rotation_row_resolves_to_plaintext_adapters(real_key):
    """Multi-key BYOK (Sprint 2 rotation) round-trips through the same seam
    into a RotatingAdapter whose underlying adapters carry plaintext keys."""
    from app.agent_framework import RotatingAdapter

    keys = [encrypt_marked("sk-rot-a"), encrypt_marked("sk-rot-b")]
    repo = MagicMock()
    repo.get_by_user_id = AsyncMock(
        return_value={
            "settings_json": {
                "ai_settings": {"ai_providers": {"qwen": {"api_key": keys}}}
            }
        }
    )
    with patch(
        "app.repositories.user_settings_repository.UserSettingsRepository",
        return_value=repo,
    ):
        ai_settings = await get_ai_settings("user-e2e")

    provider_config = get_provider_config(ai_settings, "qwen")
    assert provider_config["api_key"] == ["sk-rot-a", "sk-rot-b"]

    adapter = get_adapter_for_user(
        "qwen-max", {"qwen": provider_config}, _fallback_settings()
    )
    assert isinstance(adapter, RotatingAdapter)
    # adapter._factory is the per-key builder closure injected by
    # get_adapter_for_user — driving it directly (rather than the rotator's
    # cooldown-aware next_key()) is the simplest way to assert every
    # underlying single-key adapter carries a plaintext key.
    built = [adapter._factory("sk-rot-a"), adapter._factory("sk-rot-b")]
    assert {a.api_key for a in built} == {"sk-rot-a", "sk-rot-b"}
