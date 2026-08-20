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

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.db import session as db_session
from app.services.ai.governance.ai_governance import AIModuleGovernance
from app.services.ai.providers import ai_provider_helpers as helpers


class _NoUserSettingsSession:
    """Fake ORM session whose scalar() reads always miss (no user_settings row)."""

    async def scalar(self, _stmt: Any) -> None:
        return None


class _NoUserSettingsScope:
    async def __aenter__(self) -> _NoUserSettingsSession:
        return _NoUserSettingsSession()

    async def __aexit__(self, *exc: Any) -> bool:
        return False


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
    from app.core.secure_settings import encrypt_byok

    # Owner-bound to "u" (the user the resolver is called for).
    ciphertext = encrypt_byok("user-doubao-plain", "u")
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
        patch.object(db_session, "read_scope", lambda: _NoUserSettingsScope()),
    ):
        with pytest.raises(RuntimeError, match="no user_settings"):
            await helpers.resolve_summarization_config("u", settings_json=None)


async def test_governance_short_circuits_before_user_settings():
    """Locked module must NOT consult user settings — read_scope() is never hit."""
    read_scope_mock = AsyncMock()
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            AsyncMock(return_value=_locked(model="qwen-max", api_key="admin-key")),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_platform_model",
            AsyncMock(return_value=None),
        ),
        patch.object(db_session, "read_scope", read_scope_mock),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=None)

    assert cfg.origin == "governance"
    read_scope_mock.assert_not_called()


# ── openai: the Whisper dropdown must not decide the summary model ─────────
#
# Ground truth (frontend/components/AISettings.tsx): the openai card is the
# only provider card with THREE model dropdowns, and they write three
# different keys —
#     Whisper Model  → selected_model
#     Summary Model  → summary_model
#     Analysis Model → analysis_model
# Reading selected_model for openai therefore summarised with whatever the
# user last picked for ASR (whisper-1 posted to /v1/chat/completions → every
# summary fails). Only the openai branch changes; every other provider keeps
# reading selected_model, which is the single chip list its card writes.


async def test_openai_summary_uses_summary_model_not_whisper_pick():
    """openai enabled+keyed with BOTH dropdowns set → the Summary Model wins.

    Mutation guard: read selected_model first for openai and this goes red
    with model == 'whisper-1'.
    """
    settings = _settings(
        {
            "default_summary_model": "gpt-4o-mini",
            "ai_providers": {
                "openai": {
                    "api_key": "sk-user",
                    "enabled": True,
                    # what the Whisper Model dropdown wrote
                    "selected_model": "whisper-1",
                    # what the Summary Model dropdown wrote
                    "summary_model": "gpt-4o",
                    "analysis_model": "gpt-4o",
                }
            },
        }
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=settings)

    assert cfg.provider_key == "openai"
    assert cfg.model == "gpt-4o"
    assert cfg.provider_config["model"] == "gpt-4o"
    assert cfg.origin == "byok"


async def test_openai_never_resolves_to_a_whisper_model():
    """The defect's exact shape: user touched ONLY the Whisper dropdown, so
    summary_model was never written. selected_model holds an ASR id, which is
    not a chat model — fall through to default_summary_model rather than
    posting whisper-1 to chat-completions."""
    settings = _settings(
        {
            "default_summary_model": "gpt-4o-mini",
            "ai_providers": {
                "openai": {
                    "api_key": "sk-user",
                    "enabled": True,
                    "selected_model": "whisper-1",
                }
            },
        }
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=settings)

    assert cfg.provider_key == "openai"
    assert not cfg.model.startswith("whisper")
    assert cfg.model == "gpt-4o-mini"


async def test_openai_legacy_row_without_summary_model_keeps_selected_model():
    """Rows written before the Summary Model dropdown existed carry only
    selected_model, and for those it IS the summary pick — the openai branch
    falls back to it rather than skipping straight to the global default."""
    settings = _settings(
        {
            "default_summary_model": "gpt-4o-mini",
            "ai_providers": {
                "openai": {
                    "api_key": "sk-user",
                    "enabled": True,
                    "selected_model": "gpt-4-turbo",  # legacy: chat model here
                }
            },
        }
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=settings)

    assert cfg.model == "gpt-4-turbo"


async def test_openai_blank_summary_model_falls_back_to_selected_model():
    """A blank string in summary_model must not swallow the legacy fallback
    (`or` semantics, not a `"summary_model" in cfg` presence check)."""
    settings = _settings(
        {
            "default_summary_model": "gpt-4o-mini",
            "ai_providers": {
                "openai": {
                    "api_key": "sk-user",
                    "enabled": True,
                    "selected_model": "gpt-4-turbo",
                    "summary_model": "",
                }
            },
        }
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=settings)

    assert cfg.model == "gpt-4-turbo"


@pytest.mark.parametrize(
    "provider_key,selected,expected",
    [
        ("doubao", "doubao-pro", "doubao-pro"),
        ("qwen", "qwen-plus", "qwen-plus"),
        ("deepseek", "deepseek-chat", "deepseek-chat"),
    ],
)
async def test_non_openai_providers_still_read_selected_model(
    provider_key: str, selected: str, expected: str
):
    """Behaviour conservation: only the openai branch changed. Every other
    provider keeps taking selected_model EVEN IF a stray summary_model is
    present in the row (their cards never write one — a value there would be
    hand-edited settings_json, and honouring it would be new behaviour)."""
    settings = _settings(
        {
            "default_summary_model": "fallback-model",
            "ai_providers": {
                provider_key: {
                    "api_key": "k",
                    "enabled": True,
                    "selected_model": selected,
                    "summary_model": "should-be-ignored",
                }
            },
        }
    )
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_unlocked()),
    ):
        cfg = await helpers.resolve_summarization_config("u", settings_json=settings)

    assert cfg.provider_key == provider_key
    assert cfg.model == expected


async def test_openai_summary_model_ignored_when_openai_is_not_chosen():
    """Priority is unchanged: doubao outranks openai, so openai's
    summary_model must not leak into a doubao run."""
    settings = _settings(
        {
            "ai_providers": {
                "doubao": {
                    "api_key": "db-key",
                    "enabled": True,
                    "selected_model": "doubao-pro",
                },
                "openai": {
                    "api_key": "sk-user",
                    "enabled": True,
                    "selected_model": "whisper-1",
                    "summary_model": "gpt-4o",
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
