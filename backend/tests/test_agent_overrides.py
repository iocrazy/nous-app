"""Agent overrides (mig 341): per-user/per-team customization of system
presets. Effective agent = user ?? team ?? system; reset = delete the row."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.repositories.agent_repository import (
    AGENT_OVERRIDE_FIELDS,
    AgentRepository,
)

AGENT_ID = "22222222-2222-2222-2222-222222222222"
USER_ID = UUID("11111111-1111-1111-1111-111111111111")
TEAM_ID = 42

BASE_AGENT = {
    "id": AGENT_ID,
    "slug": "analyze",
    "name": "Analyze",
    "is_system_preset": True,
    "identity_md": "system identity",
    "soul_md": "system soul",
    "agent_md": "system agent",
    "model": "qwen-max",
    "temperature": 0.7,
    "max_tokens": 4096,
    "fallback_models": [],
}


def _repo_with_overrides(overrides_by_scope):
    """AgentRepository with get_override stubbed per scope key."""
    repo = AgentRepository()

    async def fake_get_override(agent_id, *, user_id=None, team_id=None):
        if user_id is not None:
            return overrides_by_scope.get("user")
        if team_id is not None:
            return overrides_by_scope.get("team")
        return None

    repo.get_override = fake_get_override  # type: ignore[method-assign]
    return repo


@pytest.mark.asyncio
async def test_merge_user_beats_team_beats_system():
    repo = _repo_with_overrides(
        {
            "team": {"model": "team-model", "soul_md": "team soul"},
            "user": {"model": "user-model"},
        }
    )
    merged = await repo._apply_overrides(
        dict(BASE_AGENT), user_id=USER_ID, team_id=TEAM_ID
    )
    assert merged["model"] == "user-model"  # user layer wins
    assert merged["soul_md"] == "team soul"  # team layer fills the rest
    assert merged["identity_md"] == "system identity"  # untouched inherits
    assert merged["override_scope"] == "user"
    assert set(merged["override_fields"]) == {"model", "soul_md"}


@pytest.mark.asyncio
async def test_merge_skipped_without_caller_context():
    """Background pipelines (no user/team) always get the pristine row."""
    repo = _repo_with_overrides({"user": {"model": "user-model"}})
    merged = await repo._apply_overrides(dict(BASE_AGENT), user_id=None, team_id=None)
    assert merged["model"] == "qwen-max"
    assert "override_scope" not in merged


@pytest.mark.asyncio
async def test_merge_skipped_for_non_preset():
    """User-owned agents are edited directly — overrides never apply."""
    repo = _repo_with_overrides({"user": {"model": "user-model"}})
    agent = {**BASE_AGENT, "is_system_preset": False}
    merged = await repo._apply_overrides(agent, user_id=USER_ID, team_id=None)
    assert merged["model"] == "qwen-max"


@pytest.mark.asyncio
async def test_merge_failure_degrades_to_base():
    repo = AgentRepository()

    async def boom(*a, **k):
        raise RuntimeError("db down")

    repo.get_override = boom  # type: ignore[method-assign]
    merged = await repo._apply_overrides(
        dict(BASE_AGENT), user_id=USER_ID, team_id=None
    )
    assert merged["model"] == "qwen-max"  # never breaks agent resolution


def test_override_fields_exclude_catalog_identity():
    """name/description/icon/budgets stay admin-owned — not overridable."""
    for banned in ("name", "description", "icon", "monthly_token_budget"):
        assert banned not in AGENT_OVERRIDE_FIELDS


@pytest.mark.asyncio
async def test_patch_preset_content_writes_override_not_base():
    """PATCH on a system preset routes content into upsert_override; the base
    row writer (update_fields_versioned) is NOT called."""
    from app.api.ai_library_router import update_agent
    from app.schemas.ai_library import AgentUpdate

    auth = MagicMock()
    auth.user_id = str(USER_ID)

    repo = MagicMock()
    repo.get_by_slug = AsyncMock(return_value=dict(BASE_AGENT))
    repo.get_skill_ids = AsyncMock(return_value=[])
    repo.upsert_override = AsyncMock(return_value=True)
    repo.update_fields_versioned = AsyncMock()
    repo.update_skill_bindings = AsyncMock()

    with (
        patch("app.api.ai_library_router._repos", return_value=(repo, MagicMock())),
        patch(
            "app.api.ai_library_router._enrich_agents_with_scope_names",
            new=AsyncMock(side_effect=lambda rows: rows),
        ),
    ):
        result = await update_agent(
            "analyze", AgentUpdate(soul_md="my custom soul"), auth
        )

    repo.upsert_override.assert_awaited_once()
    call = repo.upsert_override.await_args
    assert call.args[1] == {"soul_md": "my custom soul"}
    assert call.kwargs["user_id"] == USER_ID
    repo.update_fields_versioned.assert_not_awaited()
    assert result is not None


@pytest.mark.asyncio
async def test_patch_preset_catalog_fields_still_403():
    """Renaming a system preset stays forbidden (catalog identity)."""
    from fastapi import HTTPException

    from app.api.ai_library_router import update_agent
    from app.schemas.ai_library import AgentUpdate

    auth = MagicMock()
    auth.user_id = str(USER_ID)
    repo = MagicMock()
    repo.get_by_slug = AsyncMock(return_value=dict(BASE_AGENT))

    with patch("app.api.ai_library_router._repos", return_value=(repo, MagicMock())):
        with pytest.raises(HTTPException) as exc:
            await update_agent("analyze", AgentUpdate(name="Renamed"), auth)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_override_resets_to_defaults():
    from app.api.ai_library_router import delete_agent_override

    auth = MagicMock()
    auth.user_id = str(USER_ID)
    repo = MagicMock()
    repo.get_by_slug = AsyncMock(return_value=dict(BASE_AGENT))
    repo.get_skill_ids = AsyncMock(return_value=[])
    repo.delete_override = AsyncMock(return_value=True)

    with (
        patch("app.api.ai_library_router._repos", return_value=(repo, MagicMock())),
        patch(
            "app.api.ai_library_router._enrich_agents_with_scope_names",
            new=AsyncMock(side_effect=lambda rows: rows),
        ),
    ):
        await delete_agent_override("analyze", auth, scope="user")

    repo.delete_override.assert_awaited_once()
    assert repo.delete_override.await_args.kwargs["user_id"] == USER_ID


@pytest.mark.asyncio
async def test_admin_catalog_update_writes_base_row_versioned():
    """Admin catalog PUT edits the BASE preset row via the versioned path —
    user overrides are a separate table and stay untouched."""
    from app.api.admin.agents_router import AdminAgentUpdate, update_catalog_agent

    auth = MagicMock()
    auth.user_id = str(USER_ID)

    repo = MagicMock()
    repo.get_by_slug = AsyncMock(return_value=dict(BASE_AGENT))
    repo.update_fields_versioned = AsyncMock()
    repo.count_overrides_by_agent = AsyncMock(return_value={})

    with patch("app.api.admin.agents_router.get_agent_repository", return_value=repo):
        await update_catalog_agent(
            "analyze", AdminAgentUpdate(soul_md="new base soul"), auth
        )

    repo.update_fields_versioned.assert_awaited_once()
    args = repo.update_fields_versioned.await_args
    assert args.args[1] == {"soul_md": "new base soul"}


@pytest.mark.asyncio
async def test_admin_catalog_update_404_for_non_preset():
    from fastapi import HTTPException

    from app.api.admin.agents_router import AdminAgentUpdate, update_catalog_agent

    auth = MagicMock()
    auth.user_id = str(USER_ID)
    repo = MagicMock()
    repo.get_by_slug = AsyncMock(return_value={**BASE_AGENT, "is_system_preset": False})

    with patch("app.api.admin.agents_router.get_agent_repository", return_value=repo):
        with pytest.raises(HTTPException) as exc:
            await update_catalog_agent("analyze", AdminAgentUpdate(name="x"), auth)
    assert exc.value.status_code == 404
