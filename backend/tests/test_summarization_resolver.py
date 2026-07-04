"""A3 — resolve_summarization_config origin-tag + provider-priority parity.

Pins the resolution branches of the summarization user-path resolver (lifted
out of ai_summary.load_summary_inputs) and the ResolvedAIConfig each produces.
Mocks the SAME governance seam the workflow SQL tests mock
(``get_module_governance`` / ``resolve_platform_model``). No DB.

Summarization is unlike the agent-driven resolvers: NO agent slug, NO user
nous-pick — its user path scans a HARDCODED provider priority
(doubao → qwen → openai → deepseek) and uses default_summary_model.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.governance.ai_governance import AIModuleGovernance
from app.services.ai.providers import ai_provider_helpers as helpers

pytestmark = pytest.mark.asyncio


def _locked(model: str = "mediahub-summary", api_key: str = "") -> AIModuleGovernance:
    # allowed=False → module is admin-locked (governance branch).
    return AIModuleGovernance(
        allowed=False, base_url="https://admin/v1", model=model, api_key=api_key
    )


def _unlocked() -> AIModuleGovernance:
    # allowed=True → not locked; resolve_locked_module_config returns None.
    return AIModuleGovernance(allowed=True)


def _settings(ai_settings: dict) -> dict:
    return {"ai_settings": ai_settings}


async def test_origin_governance_from_catalog():
    """Locked module resolving to a platform-catalog model → origin=governance;
    provider/config/model come from the catalog, agent_slug is ''."""
    catalog = (
        "doubao",
        {
            "api_key": "cat-key",
            "base_url": "https://ark/v3",
            "model": "doubao-x",
            "app_id": "",
        },
        "doubao-x",
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
        cfg = await helpers.resolve_summarization_config("u", settings_json=None)

    assert cfg.origin == "governance"
    assert cfg.provider_key == "doubao"
    assert cfg.provider_config["api_key"] == "cat-key"
    assert cfg.model == "doubao-x"
    assert cfg.agent_slug == ""


async def test_origin_governance_from_manual_admin_config():
    """Locked with a manual admin key (not a catalog model) → origin=governance;
    provider_key derived from the model prefix, provider_config keeps app_id=''."""
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=_locked(model="qwen-max", api_key="admin-key")),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_platform_model",
            AsyncMock(return_value=None),  # not a catalog model → manual path
        ),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=None)

    assert cfg.origin == "governance"
    assert cfg.provider_key == "qwen"  # derived from "qwen-max" prefix
    assert cfg.provider_config["api_key"] == "admin-key"
    assert cfg.provider_config["app_id"] == ""
    assert cfg.model == "qwen-max"
    assert cfg.agent_slug == ""


async def test_origin_byok_when_provider_enabled_and_keyed():
    """User has an enabled+keyed provider → origin=byok; selected_model used."""
    settings = _settings(
        {
            "ai_providers": {
                "doubao": {
                    "api_key": "user-doubao",
                    "enabled": True,
                    "base_url": "https://ark/v3",
                    "selected_model": "doubao-pro",
                }
            }
        }
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=settings)

    assert cfg.origin == "byok"
    assert cfg.provider_key == "doubao"
    assert cfg.provider_config["api_key"] == "user-doubao"
    assert cfg.model == "doubao-pro"
    assert cfg.provider_config["model"] == "doubao-pro"
    assert cfg.agent_slug == ""


async def test_byok_reveals_encrypted_api_key(monkeypatch):
    """secret-at-rest Phase 2: an enc:v1: api_key stored in ai_providers is
    decrypted before it reaches provider_config — this resolver reads raw
    settings_json directly (not via get_ai_settings), so it needs its own
    reveal chokepoint."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    from app.core.secure_settings import encrypt_marked

    ciphertext = encrypt_marked("user-doubao-plain")
    settings = _settings(
        {
            "ai_providers": {
                "doubao": {
                    "api_key": ciphertext,
                    "enabled": True,
                    "selected_model": "doubao-pro",
                }
            }
        }
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=settings)

    assert cfg.origin == "byok"
    assert cfg.provider_config["api_key"] == "user-doubao-plain"


async def test_provider_priority_doubao_wins_over_deepseek():
    """Both doubao and deepseek enabled+keyed → doubao wins (priority order)."""
    settings = _settings(
        {
            "ai_providers": {
                "deepseek": {
                    "api_key": "ds-key",
                    "enabled": True,
                    "selected_model": "deepseek-chat",
                },
                "doubao": {
                    "api_key": "db-key",
                    "enabled": True,
                    "selected_model": "doubao-pro",
                },
            }
        }
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=settings)

    assert cfg.provider_key == "doubao"
    assert cfg.model == "doubao-pro"


async def test_provider_priority_skips_disabled_provider():
    """A higher-priority provider that is keyed but DISABLED is skipped; the
    next enabled+keyed provider (qwen) is chosen."""
    settings = _settings(
        {
            "ai_providers": {
                "doubao": {
                    "api_key": "db-key",
                    "enabled": False,  # keyed but disabled → skipped
                    "selected_model": "doubao-pro",
                },
                "qwen": {
                    "api_key": "qw-key",
                    "enabled": True,
                    "selected_model": "qwen-plus",
                },
            }
        }
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=settings)

    assert cfg.provider_key == "qwen"
    assert cfg.model == "qwen-plus"


async def test_default_summary_model_used_when_no_selected_model():
    """Chosen provider without selected_model → default_summary_model is used."""
    settings = _settings(
        {
            "default_summary_model": "doubao-lite",
            "ai_providers": {
                "doubao": {"api_key": "db-key", "enabled": True},  # no selected_model
            },
        }
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=settings)

    assert cfg.provider_key == "doubao"
    assert cfg.model == "doubao-lite"
    assert cfg.provider_config["model"] == "doubao-lite"


async def test_no_provider_enabled_preserves_empty_fallthrough():
    """No enabled+keyed provider → provider_key='' with a keyless config and
    origin=env — the exact non-raising fall-through the workflow preserved."""
    settings = _settings(
        {
            "default_summary_model": "some-model",
            "ai_providers": {
                # keyed but disabled → not chosen; no enabled provider at all
                "openai": {"api_key": "sk", "enabled": False},
            },
        }
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=settings)

    assert cfg.origin == "env"
    assert cfg.provider_key == ""
    assert cfg.provider_config["api_key"] == ""
    # default_summary_model still surfaces even with no provider chosen.
    assert cfg.model == "some-model"


async def test_raises_when_no_user_settings():
    """Not locked + no settings row anywhere → RuntimeError('no user_settings'),
    preserving the workflow's raise."""
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=_unlocked()),
        ),
        patch("app.db.engine.fetch_one", AsyncMock(return_value=None)),
    ):
        with pytest.raises(RuntimeError, match="no user_settings"):
            await helpers.resolve_summarization_config("u", settings_json=None)


async def test_governance_short_circuits_before_user_settings():
    """Locked module must NOT consult user settings — fetch_one is never hit."""
    fetch = AsyncMock(return_value=None)
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=_locked(model="qwen-max", api_key="admin-key")),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_platform_model",
            AsyncMock(return_value=None),
        ),
        patch("app.db.engine.fetch_one", fetch),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=None)

    assert cfg.origin == "governance"
    fetch.assert_not_awaited()
