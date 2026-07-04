"""A1 — typed ``resolve_task_ai_config`` + tuple shim equivalence.

Covers:
  - Shim equivalence: the legacy ``resolve_task_provider_config`` tuple equals
    the corresponding fields of the new ``resolve_task_ai_config`` result, for
    a governance-locked case and a plain BYOK case.
  - ``origin`` per return branch: governance-locked → "governance"; user
    ``nous:<model>`` pick → "platform"; BYOK with api_key → "byok"; BYOK path
    with an empty api_key → "env".

Fixtures mirror tests/test_ai_governance_resolver.py: patch
``get_module_governance`` (lazily imported by the resolver) and the agent repo
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
# Shim equivalence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shim_equivalence_governance_locked():
    """For a governance-locked case, the tuple shim equals the typed fields."""
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
        cfg = await helpers_mod.resolve_task_ai_config(
            user_id="user-123",
            task_key="translation",
            default_slug="translate",
        )
        tup = await helpers_mod.resolve_task_provider_config(
            user_id="user-123",
            task_key="translation",
            default_slug="translate",
        )

    assert tup == (cfg.provider_key, cfg.provider_config, cfg.model, cfg.agent_slug)
    assert cfg.origin == "governance"


@pytest.mark.asyncio
async def test_shim_equivalence_plain_byok():
    """For a plain BYOK case, the tuple shim equals the typed fields."""
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    fake_agent = {"model": "qwen-max", "slug": "analyze"}
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=fake_agent)

    ai_settings = {
        "task_assignment": {},
        "ai_providers": {"qwen": {"api_key": "user-qwen-key"}},
    }

    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            new=AsyncMock(return_value=_allowed_governance()),
        ),
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=mock_repo,
        ),
        patch.object(
            helpers_mod, "get_ai_settings", new=AsyncMock(return_value=ai_settings)
        ),
        patch.object(
            helpers_mod, "resolve_nous_model", new=AsyncMock(return_value=None)
        ),
    ):
        cfg = await helpers_mod.resolve_task_ai_config(
            user_id="user-1",
            task_key="visual_analysis",
            default_slug="analyze",
        )
        tup = await helpers_mod.resolve_task_provider_config(
            user_id="user-1",
            task_key="visual_analysis",
            default_slug="analyze",
        )

    assert tup == (cfg.provider_key, cfg.provider_config, cfg.model, cfg.agent_slug)
    assert cfg.provider_config["api_key"] == "user-qwen-key"
    assert cfg.origin == "byok"


# ---------------------------------------------------------------------------
# origin per branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_origin_governance():
    """Admin-locked module → origin == 'governance'."""
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=_locked_governance()),
    ):
        cfg = await helpers_mod.resolve_task_ai_config(
            user_id="user-1", task_key="translation", default_slug="translate"
        )

    assert cfg.origin == "governance"


@pytest.mark.asyncio
async def test_origin_platform_user_nous_pick():
    """User assignment ``nous:<model>`` resolving via the gate → 'platform'."""
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            new=AsyncMock(return_value=_allowed_governance()),
        ),
        patch.object(
            helpers_mod,
            "get_ai_settings",
            new=AsyncMock(
                return_value={
                    "task_assignment": {"summarization": "nous:mediahub-doubao-pro"}
                }
            ),
        ),
        patch.object(
            helpers_mod,
            "resolve_nous_model",
            new=AsyncMock(
                return_value=(
                    "doubao",
                    {"api_key": "k", "base_url": "u", "model": "doubao-x"},
                    "doubao-x",
                )
            ),
        ),
    ):
        cfg = await helpers_mod.resolve_task_ai_config(
            user_id="u1", task_key="summarization", default_slug="summarize"
        )

    assert cfg.origin == "platform"
    assert cfg.agent_slug == "summarize"


@pytest.mark.asyncio
async def test_origin_byok_with_api_key():
    """BYOK provider config carrying a non-empty api_key → 'byok'."""
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    fake_agent = {"model": "qwen-max", "slug": "analyze"}
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=fake_agent)

    ai_settings = {
        "task_assignment": {},
        "ai_providers": {"qwen": {"api_key": "user-qwen-key"}},
    }

    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            new=AsyncMock(return_value=_allowed_governance()),
        ),
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=mock_repo,
        ),
        patch.object(
            helpers_mod, "get_ai_settings", new=AsyncMock(return_value=ai_settings)
        ),
        patch.object(
            helpers_mod, "resolve_nous_model", new=AsyncMock(return_value=None)
        ),
    ):
        cfg = await helpers_mod.resolve_task_ai_config(
            user_id="user-1", task_key="visual_analysis", default_slug="analyze"
        )

    assert cfg.origin == "byok"
    assert cfg.provider_config["api_key"] == "user-qwen-key"


@pytest.mark.asyncio
async def test_origin_env_byok_without_api_key():
    """BYOK path with no user provider entry (empty api_key) → 'env'.

    The user has a valid agent/model, provider prefix resolves, but there is
    no ``ai_providers`` entry for that provider — so the merged config carries
    no api_key and the adapter factory would fall back to env credentials.
    """
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    fake_agent = {"model": "qwen-max", "slug": "analyze"}
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=fake_agent)

    ai_settings = {"task_assignment": {}, "ai_providers": {}}

    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            new=AsyncMock(return_value=_allowed_governance()),
        ),
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=mock_repo,
        ),
        patch.object(
            helpers_mod, "get_ai_settings", new=AsyncMock(return_value=ai_settings)
        ),
        patch.object(
            helpers_mod, "resolve_nous_model", new=AsyncMock(return_value=None)
        ),
    ):
        cfg = await helpers_mod.resolve_task_ai_config(
            user_id="user-1", task_key="visual_analysis", default_slug="analyze"
        )

    assert cfg.origin == "env"
    assert not (cfg.provider_config.get("api_key") or "").strip()
