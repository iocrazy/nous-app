"""T2 — Governance enforcement in task-provider resolvers.

Tests that:
- Locked module → returns admin config; user BYOK/task_assignment NOT consulted.
- Allowed module → behavior unchanged (user path still executes).
- Locked + no admin api_key → fail-closed (RuntimeError).

The tests patch ``get_module_governance`` (in the governance module, which is
lazily imported by ``resolve_task_provider_config``) and the agent repository
to avoid live DB calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.governance.ai_governance import AIModuleGovernance


def _locked_governance(
    *,
    api_key: str = "admin-key-xyz",
    base_url: str = "https://admin.example.com/v1",
    model: str = "qwen-max",
) -> AIModuleGovernance:
    return AIModuleGovernance(
        allowed=False,
        base_url=base_url,
        model=model,
        api_key=api_key,
    )


def _allowed_governance() -> AIModuleGovernance:
    return AIModuleGovernance(allowed=True)


# ---------------------------------------------------------------------------
# T2-a: locked module → admin config returned, user path bypassed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_returns_admin_config():
    """When the module is locked, resolve_task_provider_config returns the
    admin-set config and never touches the agent repo or user settings."""
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    governance = _locked_governance(
        api_key="admin-secret",
        base_url="https://api.admin.com/v1",
        model="qwen-max",
    )

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        # Also patch the agent repo import to confirm it's never called.
        with patch(
            "app.repositories.agent_repository.get_agent_repository"
        ) as mock_repo_factory:
            result = await helpers_mod.resolve_task_provider_config(
                user_id="user-123",
                task_key="translation",
                default_slug="translate",
            )

    provider_key, provider_config, model, agent_slug = result

    # Admin config must appear in provider_config.
    assert provider_config["api_key"] == "admin-secret"
    assert provider_config["base_url"] == "https://api.admin.com/v1"
    assert model == "qwen-max"
    # default_slug is used so the caller composes the built-in agent prompt.
    assert agent_slug == "translate"
    # Agent repo must NOT have been called.
    mock_repo_factory.assert_not_called()


@pytest.mark.asyncio
async def test_locked_provider_key_derived_from_model():
    """provider_key is derived from the admin model prefix when possible."""
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    governance = _locked_governance(model="doubao-pro-32k", api_key="doubao-key")

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        provider_key, provider_config, model, _ = (
            await helpers_mod.resolve_task_provider_config(
                user_id="user-1",
                task_key="visual_analysis",
                default_slug="analyze",
            )
        )

    assert provider_key == "doubao"
    assert model == "doubao-pro-32k"
    assert provider_config["api_key"] == "doubao-key"


@pytest.mark.asyncio
async def test_locked_unknown_model_prefix_falls_back_to_generic():
    """Unknown model prefix → provider_key '' (generic OpenAI-compatible)."""
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    governance = _locked_governance(model="custom-llm-v1", api_key="custom-key")

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        provider_key, provider_config, model, slug = (
            await helpers_mod.resolve_task_provider_config(
                user_id="user-1",
                task_key="caption",
                default_slug="caption",
            )
        )

    assert provider_key == ""  # unknown prefix → generic
    assert model == "custom-llm-v1"
    assert provider_config["api_key"] == "custom-key"
    assert slug == "caption"


# ---------------------------------------------------------------------------
# T2-b: locked + no admin api_key → fail-closed (RuntimeError)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_no_api_key_fails_closed():
    """A locked module with no admin api_key MUST raise RuntimeError.

    WhisperService and LLMAnalysisService use AIProviderFactory.get_provider
    directly (no env fallback).  Silently running without a key would fail
    later with a cryptic auth error; we surface it early."""
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    governance = AIModuleGovernance(
        allowed=False,
        base_url="https://api.example.com/v1",
        model="gpt-4o",
        api_key="",  # no key configured
    )

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        with pytest.raises(RuntimeError, match="admin-locked"):
            await helpers_mod.resolve_task_provider_config(
                user_id="user-1",
                task_key="translation",
                default_slug="translate",
            )


@pytest.mark.asyncio
async def test_locked_blank_api_key_fails_closed():
    """Blank (whitespace-only) api_key is treated as absent → fail-closed."""
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    governance = AIModuleGovernance(
        allowed=False,
        base_url="",
        model="qwen-max",
        api_key="   ",  # blank
    )

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        with pytest.raises(RuntimeError, match="admin-locked"):
            await helpers_mod.resolve_task_provider_config(
                user_id="user-1",
                task_key="classification",
                default_slug="classify",
            )


# ---------------------------------------------------------------------------
# T2-c: allowed → unchanged behavior (user BYOK path still executes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowed_user_path_still_executes():
    """When the module is allowed, resolve_task_provider_config falls through
    to the normal user path (agent repo + user settings lookup)."""
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    governance = _allowed_governance()

    fake_agent = {"model": "qwen-max", "slug": "analyze"}
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=fake_agent)

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        with patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=mock_repo,
        ):
            with patch.object(
                helpers_mod, "get_ai_settings", new=AsyncMock(return_value={})
            ):
                provider_key, provider_config, model, slug = (
                    await helpers_mod.resolve_task_provider_config(
                        user_id="user-1",
                        task_key="visual_analysis",
                        default_slug="analyze",
                    )
                )

    # The agent repo was consulted (user path executed).
    mock_repo.get_by_slug.assert_called()
    # Model comes from the agent row.
    assert model == "qwen-max"


# ---------------------------------------------------------------------------
# T2-d: transcription governance via load_transcribe_inputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transcription_locked_returns_admin_config():
    """load_transcribe_inputs returns admin config when transcription is locked."""
    import app.workflows.ai_transcription as trans_mod

    governance = _locked_governance(
        api_key="whisper-admin-key",
        base_url="https://whisper.admin.com/v1",
        model="whisper-1",
    )

    fake_media_row = {
        "id": 1,
        "extract_audio_path": "/tmp/audio.mp3",
        "download_path": None,
        "platform_id": "abc123",
        "resource_id": 42,
    }

    fake_settings_row = {
        "settings_json": {
            "ai_settings": {
                "whisper_provider": "openai",
                "preferred_language": "en",
                "ai_providers": {"openai": {"api_key": "user-openai-key"}},
                "task_assignment": {},
            }
        }
    }

    # Patch DB reads and governance.
    # db_engine is a local import inside load_transcribe_inputs, so patch
    # the underlying engine method directly.
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        with patch(
            "app.db.engine.fetch_one",
            side_effect=[fake_media_row, fake_settings_row],
        ):
            result = await trans_mod.load_transcribe_inputs(1, "user-1")

    # Admin config must appear.
    assert result["provider_config"]["api_key"] == "whisper-admin-key"
    assert result["provider_config"]["base_url"] == "https://whisper.admin.com/v1"
    # User's whisper_provider key must NOT appear.
    assert result["provider_config"].get("api_key") != "user-openai-key"
    assert result["audio_path"] == "/tmp/audio.mp3"
    assert result["resource_id"] == "42"
    assert result["task_assignment"] == ""


@pytest.mark.asyncio
async def test_transcription_locked_no_key_fails_closed():
    """load_transcribe_inputs raises when locked with no admin api_key."""
    import app.workflows.ai_transcription as trans_mod

    governance = AIModuleGovernance(
        allowed=False, base_url="", model="whisper-1", api_key=""
    )

    fake_media_row = {
        "id": 1,
        "extract_audio_path": "/tmp/audio.mp3",
        "download_path": None,
        "platform_id": "abc",
        "resource_id": 10,
    }

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        with patch(
            "app.db.engine.fetch_one",
            side_effect=[fake_media_row, {}],
        ):
            with pytest.raises(RuntimeError, match="admin-locked"):
                await trans_mod.load_transcribe_inputs(1, "user-1")
