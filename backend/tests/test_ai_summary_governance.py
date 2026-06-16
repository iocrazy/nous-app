"""T6 — Governance gate in the ai_summary workflow (load_summary_inputs).

Tests that:
- Summarization locked → load_summary_inputs returns admin config; user
  BYOK loop is NOT consulted.
- Locked + no admin api_key → fail-closed (RuntimeError matching
  "admin-locked").
- Summarization present in TASK_MODULES and ALL_MODULES.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.governance.ai_governance import (
    ALL_MODULES,
    TASK_MODULES,
    AIModuleGovernance,
)

# ---------------------------------------------------------------------------
# T6-a: "summarization" membership in module sets
# ---------------------------------------------------------------------------


def test_summarization_in_task_modules():
    """'summarization' must be in TASK_MODULES so the gate reader fetches
    base_url/model/api_key from system_settings (not chat-only path)."""
    assert "summarization" in TASK_MODULES


def test_summarization_in_all_modules():
    """ALL_MODULES = TASK_MODULES | {chat}; summarization must appear."""
    assert "summarization" in ALL_MODULES


# ---------------------------------------------------------------------------
# T6-b: locked → admin config returned, user BYOK loop bypassed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarization_locked_returns_admin_config():
    """load_summary_inputs returns admin config when summarization is locked.

    The user's provider loop (doubao/qwen/openai/deepseek) must NOT be
    executed — verified by NOT providing a valid settings_json in the DB
    mock yet still getting a successful result with the admin key.
    """
    import app.workflows.ai_summary as summary_mod

    governance = AIModuleGovernance(
        allowed=False,
        base_url="https://admin.example.com/v1",
        model="qwen-max",
        api_key="admin-sum-key",
    )

    fake_row = {
        "transcript": "Hello world transcript.",
        "pm_id": 1,
        "title": "Test Video",
        "resource_id": 99,
    }
    # settings_row should NOT be reached when locked — pass None to confirm.
    fake_settings_row = None

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        with patch(
            "app.db.engine.fetch_one",
            side_effect=[fake_row, fake_settings_row],
        ):
            result = await summary_mod.load_summary_inputs(1, "user-1")

    assert result["provider_config"]["api_key"] == "admin-sum-key"
    assert result["provider_config"]["base_url"] == "https://admin.example.com/v1"
    assert result["provider_config"]["model"] == "qwen-max"
    assert result["provider_key"] == "qwen"  # derived from "qwen-max" prefix
    assert result["transcript"] == "Hello world transcript."
    assert result["title"] == "Test Video"
    assert result["resource_id"] == "99"


@pytest.mark.asyncio
async def test_summarization_locked_provider_key_derived_from_model():
    """provider_key is derived from the admin model prefix (doubao/qwen/openai/…)."""
    import app.workflows.ai_summary as summary_mod

    governance = AIModuleGovernance(
        allowed=False,
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-pro-32k",
        api_key="doubao-admin-key",
    )

    fake_row = {
        "transcript": "Some transcript.",
        "pm_id": 2,
        "title": "Another Video",
        "resource_id": 100,
    }

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        with patch("app.db.engine.fetch_one", side_effect=[fake_row, None]):
            result = await summary_mod.load_summary_inputs(2, "user-2")

    assert result["provider_key"] == "doubao"
    assert result["provider_config"]["api_key"] == "doubao-admin-key"


@pytest.mark.asyncio
async def test_summarization_locked_unknown_model_prefix_falls_back():
    """Unknown model prefix → provider_key '' (generic OpenAI-compatible path)."""
    import app.workflows.ai_summary as summary_mod

    governance = AIModuleGovernance(
        allowed=False,
        base_url="https://custom.llm.com/v1",
        model="custom-llm-v99",
        api_key="custom-key",
    )

    fake_row = {
        "transcript": "Transcript here.",
        "pm_id": 3,
        "title": "Video 3",
        "resource_id": 101,
    }

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        with patch("app.db.engine.fetch_one", side_effect=[fake_row, None]):
            result = await summary_mod.load_summary_inputs(3, "user-3")

    assert result["provider_key"] == ""
    assert result["provider_config"]["model"] == "custom-llm-v99"


# ---------------------------------------------------------------------------
# T6-c: locked + no admin api_key → fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarization_locked_no_key_fails_closed():
    """load_summary_inputs raises when locked with no admin api_key."""
    import app.workflows.ai_summary as summary_mod

    governance = AIModuleGovernance(
        allowed=False,
        base_url="https://admin.example.com/v1",
        model="qwen-max",
        api_key="",  # no key
    )

    fake_row = {
        "transcript": "Transcript.",
        "pm_id": 1,
        "title": "T",
        "resource_id": 50,
    }

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        with patch("app.db.engine.fetch_one", side_effect=[fake_row, None]):
            with pytest.raises(RuntimeError, match="admin-locked"):
                await summary_mod.load_summary_inputs(1, "user-1")


@pytest.mark.asyncio
async def test_summarization_locked_blank_key_fails_closed():
    """Blank (whitespace-only) api_key is treated as absent → fail-closed."""
    import app.workflows.ai_summary as summary_mod

    governance = AIModuleGovernance(
        allowed=False,
        base_url="",
        model="qwen-max",
        api_key="   ",  # blank whitespace
    )

    fake_row = {
        "transcript": "Transcript.",
        "pm_id": 1,
        "title": "T",
        "resource_id": 50,
    }

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        with patch("app.db.engine.fetch_one", side_effect=[fake_row, None]):
            with pytest.raises(RuntimeError, match="admin-locked"):
                await summary_mod.load_summary_inputs(1, "user-1")


# ---------------------------------------------------------------------------
# T6-d: allowed → user BYOK path still executed (settings_row IS fetched)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarization_allowed_user_path_executes():
    """When allowed, load_summary_inputs fetches the user settings row normally."""
    import app.workflows.ai_summary as summary_mod

    governance = AIModuleGovernance(allowed=True)

    fake_row = {
        "transcript": "Transcript.",
        "pm_id": 1,
        "title": "T",
        "resource_id": 50,
    }
    fake_settings_row = {
        "settings_json": {
            "ai_settings": {
                "ai_providers": {
                    "qwen": {
                        "api_key": "user-qwen-key",
                        "enabled": True,
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "selected_model": "qwen-plus",
                    }
                }
            }
        }
    }

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        with patch(
            "app.db.engine.fetch_one",
            side_effect=[fake_row, fake_settings_row],
        ):
            result = await summary_mod.load_summary_inputs(1, "user-1")

    # User's provider must be used.
    assert result["provider_key"] == "qwen"
    assert result["provider_config"]["api_key"] == "user-qwen-key"
