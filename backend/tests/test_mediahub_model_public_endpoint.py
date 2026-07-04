# backend/tests/test_nous_public_endpoint.py
"""Public mediahub-models endpoint honors ?type=; governance exposes nous gates."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.governance.ai_governance import AIModuleGovernance


@pytest.mark.asyncio
async def test_list_mediahub_models_passes_type_filter():
    from app.api.ai_settings_router import list_mediahub_models

    repo = MagicMock()
    repo.list_enabled = AsyncMock(return_value=[{"name": "nous-llm", "type": "llm"}])
    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        result = await list_mediahub_models(type="llm")
    repo.list_enabled.assert_awaited_once_with("llm")
    assert result == {"models": [{"name": "nous-llm", "type": "llm"}]}


@pytest.mark.asyncio
async def test_governance_includes_nous_enabled_and_modules():
    from app.api.ai_settings_router import get_ai_governance

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=AIModuleGovernance(allowed=True)),
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_globally_enabled",
            new=AsyncMock(return_value=True),
        ):
            with patch(
                "app.services.ai.governance.ai_governance.is_nous_allowed",
                new=AsyncMock(return_value=True),
            ):
                fake_auth = MagicMock()
                result = await get_ai_governance(fake_auth)

    assert result["nous_enabled"] is True
    assert isinstance(result["nous_modules"], dict)
    assert result["nous_modules"]["transcription"] is True
