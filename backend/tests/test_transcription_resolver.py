"""A2 — resolve_transcription_config origin-tag parity.

Pins the four resolution branches of the transcription user-path resolver
(lifted out of ai_transcription.load_transcribe_inputs) and the ResolvedAIConfig
each produces. Mocks the SAME seams the workflow SQL tests mock:
``get_module_governance`` / ``resolve_platform_model`` (governance gate) and the
module-level ``resolve_mediahub_model`` (gated nous pick). No DB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.governance.ai_governance import AIModuleGovernance
from app.services.ai.providers import ai_provider_helpers as helpers

pytestmark = pytest.mark.asyncio


def _locked(model: str = "mediahub-volc-asr") -> AIModuleGovernance:
    # allowed=False → module is admin-locked (governance branch).
    return AIModuleGovernance(allowed=False, base_url="", model=model, api_key="")


def _unlocked() -> AIModuleGovernance:
    # allowed=True → not locked; resolve_locked_module_config returns None.
    return AIModuleGovernance(allowed=True)


def _settings(ai_settings: dict) -> dict:
    return {"ai_settings": ai_settings}


async def test_origin_governance_from_catalog():
    """Locked module resolving to a platform-catalog model → origin=governance;
    provider/config come from the catalog, task_assignment (model) is ''."""
    catalog = (
        "volcengine",
        {"api_key": "cat-key", "base_url": "", "model": "seed-asr", "app_id": "aid"},
        "seed-asr",
    )
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=_locked()),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_platform_model",
            AsyncMock(return_value=catalog),
        ),
    ):
        cfg = await helpers.resolve_transcription_config("u", settings_json=None)

    assert cfg.origin == "governance"
    assert cfg.provider_key == "volcengine"
    assert cfg.provider_config["api_key"] == "cat-key"
    assert cfg.provider_config["app_id"] == "aid"
    assert cfg.model == ""  # governance → task_assignment stays empty
    assert cfg.agent_slug == ""


async def test_origin_platform_from_nous_pick():
    """User picks nous:<model> → gated resolve_mediahub_model → origin=platform;
    model carries the normalized 'provider:model' descriptor."""
    settings = _settings(
        {
            "task_assignment": {"transcription": "nous:seed-asr"},
            "preferred_language": "en",
        }
    )
    nous = ("volcengine", {"api_key": "n-key", "model": "seed-asr-v2"}, "seed-asr-v2")
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=_unlocked()),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_mediahub_model",
            AsyncMock(return_value=nous),
        ),
    ):
        cfg = await helpers.resolve_transcription_config("u", settings_json=settings)

    assert cfg.origin == "platform"
    assert cfg.provider_key == "volcengine"
    assert cfg.provider_config["api_key"] == "n-key"
    assert cfg.model == "volcengine:seed-asr-v2"
    assert cfg.agent_slug == ""


async def test_origin_byok_when_api_key_present():
    """User whisper_provider with a non-empty api_key → origin=byok; model is
    the raw task_assignment picker string."""
    settings = _settings(
        {
            "whisper_provider": "openai",
            "ai_providers": {"openai": {"api_key": "sk-x", "model": "whisper-1"}},
            "task_assignment": {"transcription": "openai:whisper-1"},
        }
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_transcription_config("u", settings_json=settings)

    assert cfg.origin == "byok"
    assert cfg.provider_key == "openai"
    assert cfg.provider_config["api_key"] == "sk-x"
    assert cfg.model == "openai:whisper-1"


async def test_byok_reveals_encrypted_api_key(monkeypatch):
    """secret-at-rest Phase 2: an enc:v1: api_key stored in ai_providers is
    decrypted before it reaches provider_config — this resolver reads raw
    settings_json directly (not via get_ai_settings), so it needs its own
    reveal chokepoint."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    from app.core.secure_settings import encrypt_byok

    # Owner-bound to "u" (the user the resolver is called for).
    ciphertext = encrypt_byok("sk-plaintext-asr", "u")
    settings = _settings(
        {
            "whisper_provider": "openai",
            "ai_providers": {"openai": {"api_key": ciphertext, "model": "whisper-1"}},
            "task_assignment": {"transcription": "openai:whisper-1"},
        }
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_transcription_config("u", settings_json=settings)

    assert cfg.origin == "byok"
    assert cfg.provider_config["api_key"] == "sk-plaintext-asr"


async def test_origin_env_when_no_api_key():
    """User whisper_provider with no configured provider entry → empty config →
    origin=env (adapter factory falls back to env credentials)."""
    settings = _settings(
        {"whisper_provider": "openai", "task_assignment": {"transcription": ""}}
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_transcription_config("u", settings_json=settings)

    assert cfg.origin == "env"
    assert cfg.provider_key == "openai"
    assert cfg.provider_config == {}
    assert cfg.model == ""


async def test_raises_when_no_user_settings():
    """Not locked + no settings row anywhere → RuntimeError('no user_settings')."""
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=_unlocked()),
        ),
        patch("app.db.engine.fetch_one", AsyncMock(return_value=None)),
    ):
        with pytest.raises(RuntimeError, match="no user_settings"):
            await helpers.resolve_transcription_config("u", settings_json=None)


async def test_raises_on_unknown_mediahub_model():
    """nous:<model> that resolve_mediahub_model can't find → RuntimeError, never a
    silent BYOK fallback."""
    settings = _settings({"task_assignment": {"transcription": "nous:ghost"}})
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=_unlocked()),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_mediahub_model",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(RuntimeError, match="unknown platform model"):
            await helpers.resolve_transcription_config("u", settings_json=settings)
