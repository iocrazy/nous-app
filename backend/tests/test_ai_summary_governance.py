"""T6 — Governance gate in the ai_summary workflow (load_summary_inputs).

Tests that:
- Summarization locked → load_summary_inputs returns admin config; neither
  the user's BYOK cards nor the agent row are consulted.
- Locked + no admin api_key → fail-closed (RuntimeError matching
  "admin-locked").
- Summarization present in TASK_MODULES and ALL_MODULES.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db import session as db_session
from app.services.ai.governance.ai_governance import (
    ALL_MODULES,
    TASK_MODULES,
    AIModuleGovernance,
)


class _FakeExecuteResult:
    """Stand-in for the awaited ``session.execute(stmt)`` Result on the
    transcript+resource JOIN read path — only ``.mappings().first()`` is
    exercised (mirrors ``load_summary_inputs``'s real read)."""

    def __init__(self, row: Any) -> None:
        self._row = row

    def mappings(self) -> "_FakeExecuteResult":
        return self

    def first(self) -> Any:
        return self._row


class _SettingsJsonSession:
    """Fake ORM session. Supports BOTH ``execute()`` (the
    parsed_media+resources+resource_transcripts JOIN read — migrated off raw
    ``db_engine.fetch_one`` onto the ORM in Phase C task 1) and ``scalar()``
    (the user_settings.settings_json read — Phase B2 Task 1; since the
    2026-08-20 收口 that read belongs to ``resolve_task_ai_config`` →
    ``get_ai_settings``, which the user-path test patches directly) — both flow
    through the SAME patched ``read_scope`` seam."""

    def __init__(self, settings_json: Any = None, execute_row: Any = None) -> None:
        self._settings_json = settings_json
        self._execute_row = execute_row

    async def scalar(self, _stmt: Any) -> Any:
        return self._settings_json

    async def execute(self, _stmt: Any) -> _FakeExecuteResult:
        return _FakeExecuteResult(self._execute_row)


class _SettingsJsonScope:
    def __init__(self, settings_json: Any = None, execute_row: Any = None) -> None:
        self._settings_json = settings_json
        self._execute_row = execute_row

    async def __aenter__(self) -> _SettingsJsonSession:
        return _SettingsJsonSession(self._settings_json, execute_row=self._execute_row)

    async def __aexit__(self, *exc: Any) -> bool:
        return False


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
        with patch.object(
            db_session,
            "read_scope",
            lambda: _SettingsJsonScope(fake_settings_row, execute_row=fake_row),
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
        with patch.object(
            db_session, "read_scope", lambda: _SettingsJsonScope(execute_row=fake_row)
        ):
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
        with patch.object(
            db_session, "read_scope", lambda: _SettingsJsonScope(execute_row=fake_row)
        ):
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
        with patch.object(
            db_session, "read_scope", lambda: _SettingsJsonScope(execute_row=fake_row)
        ):
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
        with patch.object(
            db_session, "read_scope", lambda: _SettingsJsonScope(execute_row=fake_row)
        ):
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

    # 收口后 model 来自 summarize agent 行;用户的 provider 卡只提供该模型
    # 对应 provider 的 key(BYOK)。
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    fake_agent_repo = MagicMock()
    fake_agent_repo.get_by_slug = AsyncMock(
        return_value={"slug": "summarize", "model": "qwen-plus"}
    )

    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            new=AsyncMock(return_value=governance),
        ),
        patch.object(
            db_session,
            "read_scope",
            lambda: _SettingsJsonScope(
                fake_settings_row["settings_json"], execute_row=fake_row
            ),
        ),
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=fake_agent_repo,
        ),
        patch.object(
            helpers_mod,
            "get_ai_settings",
            new=AsyncMock(
                return_value=fake_settings_row["settings_json"]["ai_settings"]
            ),
        ),
        patch.object(
            helpers_mod, "resolve_mediahub_model", new=AsyncMock(return_value=None)
        ),
    ):
        result = await summary_mod.load_summary_inputs(1, "user-1")

    # User's provider must be used, with the AGENT's model.
    assert result["provider_key"] == "qwen"
    assert result["provider_config"]["api_key"] == "user-qwen-key"
    assert result["provider_config"]["model"] == "qwen-plus"


@pytest.mark.asyncio
async def test_summarization_locked_to_catalog_model_uses_platform_config():
    """Locked TO a platform-catalog model → base_url/key/model come from the
    catalog (ungated); the blank manual api_key must NOT fail-closed."""
    import app.workflows.ai_summary as summary_mod

    governance = AIModuleGovernance(
        allowed=False, base_url="", model="mediahub-doubao-pro", api_key=""
    )
    fake_row = {
        "transcript": "T",
        "pm_id": 1,
        "title": "V",
        "resource_id": 77,
    }
    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            new=AsyncMock(return_value=governance),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_platform_model",
            new=AsyncMock(
                return_value=(
                    "doubao",
                    {
                        "api_key": "cat-key",
                        "base_url": "https://ark/v3",
                        "model": "doubao-x",
                        "app_id": "",
                    },
                    "doubao-x",
                )
            ),
        ),
        patch.object(
            db_session, "read_scope", lambda: _SettingsJsonScope(execute_row=fake_row)
        ),
    ):
        result = await summary_mod.load_summary_inputs(1, "user-1")

    assert result["provider_key"] == "doubao"
    assert result["provider_config"]["api_key"] == "cat-key"
    assert result["provider_config"]["model"] == "doubao-x"
