# backend/tests/test_nous_resolver.py
"""Shared nous resolver: platform config on match, fail-closed otherwise."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.governance.ai_governance import AIModuleGovernance


class _FakeScopeSession:
    """Stand-in for the ORM AsyncSession — only ``scalar()`` is exercised by
    ``load_transcribe_inputs``'s user_settings.settings_json read (Phase B2
    Task 2 ORM rewrite; see ``tests/test_ai_transcription_sql.py``)."""

    def __init__(self, scalar_value):
        self._scalar_value = scalar_value

    async def scalar(self, stmt):
        return self._scalar_value


def _fake_read_scope(scalar_value):
    @asynccontextmanager
    async def _read_scope():
        yield _FakeScopeSession(scalar_value)

    return _read_scope


def _enabled_row():
    return {
        "name": "nous-llm",
        "type": "llm",
        "is_enabled": True,
        "actual_provider": "doubao",
        "actual_model": "doubao-pro-32k",
        "api_key": "platform-key",
        "base_url": "https://ark.example.com/v1",
        "app_id": None,
    }


@pytest.mark.asyncio
async def test_resolve_nous_returns_none_for_non_nous_name():
    from app.services.ai.providers import ai_provider_helpers as h

    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=None)
    repo.get_by_actual_model = AsyncMock(return_value=None)
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        result = await h.resolve_mediahub_model("gpt-4o", "visual_analysis")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_nous_returns_platform_config_when_enabled_and_allowed():
    from app.services.ai.providers import ai_provider_helpers as h

    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=_enabled_row())
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=True),
        ):
            provider_key, cfg, model = await h.resolve_mediahub_model(
                "nous-llm", "visual_analysis"
            )
    assert provider_key == "doubao"
    assert cfg["api_key"] == "platform-key"
    assert cfg["base_url"] == "https://ark.example.com/v1"
    assert cfg["model"] == "doubao-pro-32k"
    assert model == "doubao-pro-32k"


@pytest.mark.asyncio
async def test_resolve_nous_fail_closed_when_disabled():
    from app.services.ai.providers import ai_provider_helpers as h

    row = _enabled_row()
    row["is_enabled"] = False
    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=row)
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=True),
        ):
            with pytest.raises(RuntimeError, match="no longer available"):
                await h.resolve_mediahub_model("nous-llm", "visual_analysis")


@pytest.mark.asyncio
async def test_resolve_nous_fail_closed_when_gate_off():
    from app.services.ai.providers import ai_provider_helpers as h

    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=_enabled_row())
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=False),
        ):
            with pytest.raises(RuntimeError, match="disabled for this feature"):
                await h.resolve_mediahub_model("nous-llm", "visual_analysis")


@pytest.mark.asyncio
async def test_agent_path_uses_nous_platform_config_and_keeps_slug():
    """When an agent's model is an enabled nous model, resolve_task_provider_config
    returns the platform config but KEEPS the resolved agent slug (prompt preserved)."""
    from app.services.ai.providers import ai_provider_helpers as h

    fake_agent = {"model": "nous-llm", "slug": "my-analyze"}
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=fake_agent)

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=AIModuleGovernance(allowed=True)),
    ):
        with patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=mock_repo,
        ):
            with patch.object(
                h,
                "get_ai_settings",
                new=AsyncMock(
                    return_value={"task_assignment": {"visual_analysis": "my-analyze"}}
                ),
            ):
                with patch.object(
                    h,
                    "resolve_mediahub_model",
                    new=AsyncMock(
                        return_value=(
                            "doubao",
                            {"model": "doubao-pro-32k"},
                            "doubao-pro-32k",
                        )
                    ),
                ):
                    pk, cfg, model, slug = await h.resolve_task_provider_config(
                        user_id="u1",
                        task_key="visual_analysis",
                        default_slug="analyze",
                    )

    assert pk == "doubao"
    assert model == "doubao-pro-32k"
    assert slug == "my-analyze"


@pytest.mark.asyncio
async def test_transcription_nous_ref_routes_to_platform_config():
    """task_assignment.transcription = 'nous:<name>' → platform ASR config,
    provider_key = actual_provider (so a volcengine nous model still routes
    through the volcengine ASR branch)."""
    import app.workflows.ai_transcription as trans_mod
    from app.services.ai.governance.ai_governance import AIModuleGovernance

    fake_media_row = {
        "id": 1,
        "extract_audio_path": "/tmp/a.mp3",
        "download_path": None,
        "platform_id": "p1",
        "resource_id": 7,
    }
    fake_settings_row = {
        "settings_json": {
            "ai_settings": {
                "whisper_provider": "openai",
                "preferred_language": "en",
                "ai_providers": {},
                "task_assignment": {"transcription": "nous:nous-asr"},
            }
        }
    }

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=AIModuleGovernance(allowed=True)),
    ):
        with (
            patch("app.db.engine.fetch_one", side_effect=[fake_media_row]),
            patch(
                "app.db.session.read_scope",
                new=_fake_read_scope(fake_settings_row["settings_json"]),
            ),
        ):
            with patch(
                "app.services.ai.providers.ai_provider_helpers.resolve_mediahub_model",
                new=AsyncMock(
                    return_value=(
                        "volcengine",
                        {
                            "api_key": "plat-key",
                            "base_url": "",
                            "model": "seed-asr",
                            "app_id": "app-1",
                        },
                        "seed-asr",
                    )
                ),
            ):
                result = await trans_mod.load_transcribe_inputs(1, "u1")

    assert result["provider_key"] == "volcengine"
    assert result["provider_config"]["api_key"] == "plat-key"
    assert result["provider_config"]["app_id"] == "app-1"
    # task_assignment is normalized to provider:model so the volcengine branch
    # picks the right ASR resource from the model part.
    assert result["task_assignment"] == "volcengine:seed-asr"


# ── resolve_platform_model (ungated admin lookup) ──────────────────────────


@pytest.mark.asyncio
async def test_resolve_platform_model_ignores_nous_gate():
    """Ungated: returns the catalog config without consulting is_nous_allowed."""
    from app.services.ai.providers import ai_provider_helpers as h

    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=_enabled_row())
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        # is_nous_allowed patched to False — must NOT matter for the admin path.
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=False),
        ):
            out = await h.resolve_platform_model("nous-llm")
    provider, cfg, model = out
    assert provider == "doubao"
    assert cfg["api_key"] == "platform-key"
    assert model == "doubao-pro-32k"


@pytest.mark.asyncio
async def test_resolve_platform_model_none_for_unknown_name():
    from app.services.ai.providers import ai_provider_helpers as h

    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=None)
    repo.get_by_actual_model = AsyncMock(return_value=None)
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        assert await h.resolve_platform_model("gpt-4o") is None


@pytest.mark.asyncio
async def test_resolve_platform_model_raises_when_disabled():
    from app.services.ai.providers import ai_provider_helpers as h

    row = {**_enabled_row(), "is_enabled": False}
    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=row)
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        with pytest.raises(RuntimeError):
            await h.resolve_platform_model("nous-llm")
