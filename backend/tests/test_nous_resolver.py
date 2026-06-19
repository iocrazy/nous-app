# backend/tests/test_nous_resolver.py
"""Shared nous resolver: platform config on match, fail-closed otherwise."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.governance.ai_governance import AIModuleGovernance


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
    with patch(
        "app.repositories.nous_repository.get_nous_repository", return_value=repo
    ):
        result = await h.resolve_nous_model("gpt-4o", "visual_analysis")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_nous_returns_platform_config_when_enabled_and_allowed():
    from app.services.ai.providers import ai_provider_helpers as h

    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=_enabled_row())
    with patch(
        "app.repositories.nous_repository.get_nous_repository", return_value=repo
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=True),
        ):
            provider_key, cfg, model = await h.resolve_nous_model(
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
        "app.repositories.nous_repository.get_nous_repository", return_value=repo
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=True),
        ):
            with pytest.raises(RuntimeError, match="no longer available"):
                await h.resolve_nous_model("nous-llm", "visual_analysis")


@pytest.mark.asyncio
async def test_resolve_nous_fail_closed_when_gate_off():
    from app.services.ai.providers import ai_provider_helpers as h

    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=_enabled_row())
    with patch(
        "app.repositories.nous_repository.get_nous_repository", return_value=repo
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=False),
        ):
            with pytest.raises(RuntimeError, match="disabled for this feature"):
                await h.resolve_nous_model("nous-llm", "visual_analysis")


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
                    "resolve_nous_model",
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
