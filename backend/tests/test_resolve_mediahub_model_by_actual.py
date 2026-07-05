# backend/tests/test_resolve_mediahub_model_by_actual.py
"""resolve_mediahub_model falls back to actual_model lookup.

Root cause (confirmed against prod DB): the 9 AI agents have
``ai_agents.model = 'doubao-seed-2-0-lite-260428'`` — the raw Volcengine Ark
model id — but the matching ``mediahub_models`` catalog row has
``name='mediahub-doubao-seed-2-0-lite'`` / ``actual_model=
'doubao-seed-2-0-lite-260428'``. ``get_by_name(model_name)`` therefore misses,
``resolve_mediahub_model`` returns ``None``, and the caller falls through to an
ordinary BYOK resolution with no env key configured — a 401 from Ark in prod.

This suite pins the fix: ``resolve_mediahub_model`` (and the admin/ungated
``resolve_platform_model``) try ``get_by_name`` first, then fall back to
``get_by_actual_model`` when the name lookup misses.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

RAW_MODEL_ID = "doubao-seed-2-0-lite-260428"


def _enabled_row_by_actual_model():
    """A catalog row keyed by the CATALOG name, matched via actual_model —
    mirrors the real prod row (name='mediahub-doubao-seed-2-0-lite',
    actual_model='doubao-seed-2-0-lite-260428')."""
    return {
        "name": "mediahub-doubao-seed-2-0-lite",
        "type": "llm",
        "is_enabled": True,
        "actual_provider": "doubao",
        "actual_model": RAW_MODEL_ID,
        "api_key": "PLAINTEXT-KEY",
        "base_url": "https://ark.example.com/v1",
        "app_id": None,
    }


@pytest.mark.asyncio
async def test_resolve_mediahub_model_falls_back_to_actual_model_when_name_misses():
    """The confirmed-bug case: an agent stores the raw Ark model id.
    get_by_name misses; get_by_actual_model must be consulted and its row used."""
    from app.services.ai.providers import ai_provider_helpers as h

    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=None)
    repo.get_by_actual_model = AsyncMock(return_value=_enabled_row_by_actual_model())

    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=True),
        ):
            result = await h.resolve_mediahub_model(RAW_MODEL_ID, "chat")

    assert result is not None
    provider_key, provider_config, model = result
    repo.get_by_name.assert_awaited_once_with(RAW_MODEL_ID)
    repo.get_by_actual_model.assert_awaited_once_with(RAW_MODEL_ID)
    assert provider_key == "doubao"
    assert provider_config["api_key"] == "PLAINTEXT-KEY"
    assert provider_config["model"] == RAW_MODEL_ID
    assert model == RAW_MODEL_ID


@pytest.mark.asyncio
async def test_resolve_mediahub_model_prefers_name_match_over_actual_model_fallback():
    """Regression guard: when get_by_name finds a row, the actual_model
    fallback must NOT be consulted at all (name-first order preserved)."""
    from app.services.ai.providers import ai_provider_helpers as h

    name_row = {
        "name": "mediahub-doubao-seed-2-0-lite",
        "type": "llm",
        "is_enabled": True,
        "actual_provider": "doubao",
        "actual_model": RAW_MODEL_ID,
        "api_key": "NAME-MATCH-KEY",
        "base_url": "https://ark.example.com/v1",
        "app_id": None,
    }
    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=name_row)
    repo.get_by_actual_model = AsyncMock(
        side_effect=AssertionError("actual_model fallback must not be consulted")
    )

    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        with patch(
            "app.services.ai.governance.ai_governance.is_nous_allowed",
            new=AsyncMock(return_value=True),
        ):
            provider_key, provider_config, model = await h.resolve_mediahub_model(
                "mediahub-doubao-seed-2-0-lite", "chat"
            )

    repo.get_by_actual_model.assert_not_awaited()
    assert provider_config["api_key"] == "NAME-MATCH-KEY"
    assert model == RAW_MODEL_ID


@pytest.mark.asyncio
async def test_resolve_mediahub_model_returns_none_when_both_lookups_miss():
    """Ordinary BYOK model name (e.g. 'gpt-4o') — neither lookup matches,
    the ordinary BYOK path stays intact."""
    from app.services.ai.providers import ai_provider_helpers as h

    repo = MagicMock()
    repo.get_by_name = AsyncMock(return_value=None)
    repo.get_by_actual_model = AsyncMock(return_value=None)

    with patch(
        "app.repositories.mediahub_model_repository.get_mediahub_model_repository",
        return_value=repo,
    ):
        result = await h.resolve_mediahub_model("gpt-4o", "chat")

    assert result is None
    repo.get_by_name.assert_awaited_once_with("gpt-4o")
    repo.get_by_actual_model.assert_awaited_once_with("gpt-4o")
