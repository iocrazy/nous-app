"""T4 — Admin AI-governance endpoint tests.

Tests for ``GET /admin/settings/ai-governance`` and
``PUT /admin/settings/ai-governance`` in
``app.api.admin.settings_router``.

Tests that:
- GET returns AIGovernanceResponse with masked booleans (no raw keys).
- GET user_allowed defaults to True when key is absent.
- GET user_allowed is False when stored as Python bool False.
- PUT writes user_allowed as a NATIVE bool (JSONB), not a string.
- PUT writes base_url/model for task modules.
- PUT skips writing api_key when blank.
- PUT writes api_key when non-blank.
- GET api_key_set is True iff api_key row is non-empty.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo_with_rows(rows: list[dict]) -> Any:
    """Fake SystemSettingsRepository whose list_non_transcode returns rows."""
    from unittest.mock import MagicMock

    repo = MagicMock()
    repo.list_non_transcode = AsyncMock(return_value=rows)
    repo.upsert_setting = AsyncMock(
        side_effect=lambda key, value, updated_by: {"key": key, "value": value}
    )
    return repo


def _make_rows(*pairs) -> list[dict]:
    """Build minimal system_settings row dicts from (key, value) pairs."""
    return [
        {"key": k, "value": v, "updated_at": "2024-01-01T00:00:00"} for k, v in pairs
    ]


# ---------------------------------------------------------------------------
# T4-a: GET — defaults to all allowed when no rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_governance_defaults_to_all_allowed():
    """Absent keys → all modules allowed, no api_key_set."""
    from app.api.admin.settings_router import _read_governance_settings

    repo = _make_repo_with_rows([])
    with patch(
        "app.api.admin.settings_router.get_system_settings_repository",
        return_value=repo,
    ):
        result = await _read_governance_settings()

    assert result.chat.user_allowed is True
    assert result.transcription.user_allowed is True
    assert result.transcription.api_key_set is False
    assert result.translation.user_allowed is True
    assert result.visual_analysis.user_allowed is True
    assert result.caption.user_allowed is True
    assert result.classification.user_allowed is True


# ---------------------------------------------------------------------------
# T4-b: GET — JSONB bool False → locked; api_key_set from api_key row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_governance_locked_module_with_api_key():
    """Native bool False → user_allowed=False; non-empty api_key → api_key_set=True."""
    from app.api.admin.settings_router import _read_governance_settings

    rows = _make_rows(
        ("ai_module.translation.user_allowed", False),  # Python bool False
        ("ai_module.translation.base_url", "https://api.admin.com/v1"),
        ("ai_module.translation.model", "qwen-max"),
        ("ai_module.translation.api_key", "sk-admin-secret"),
    )
    repo = _make_repo_with_rows(rows)

    with patch(
        "app.api.admin.settings_router.get_system_settings_repository",
        return_value=repo,
    ):
        result = await _read_governance_settings()

    assert result.translation.user_allowed is False
    assert result.translation.base_url == "https://api.admin.com/v1"
    assert result.translation.model == "qwen-max"
    # Key must be MASKED — only boolean indicator returned.
    assert result.translation.api_key_set is True
    # Other modules stay allowed.
    assert result.chat.user_allowed is True


# ---------------------------------------------------------------------------
# T4-c: GET — api_key_set False when api_key row is empty string
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_governance_empty_api_key_not_set():
    """Empty-string api_key → api_key_set=False."""
    from app.api.admin.settings_router import _read_governance_settings

    rows = _make_rows(
        ("ai_module.transcription.user_allowed", False),
        ("ai_module.transcription.api_key", ""),
    )
    repo = _make_repo_with_rows(rows)

    with patch(
        "app.api.admin.settings_router.get_system_settings_repository",
        return_value=repo,
    ):
        result = await _read_governance_settings()

    assert result.transcription.user_allowed is False
    assert result.transcription.api_key_set is False


# ---------------------------------------------------------------------------
# T4-d: PUT — writes user_allowed as NATIVE bool (critical JSONB invariant)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_governance_writes_bool_not_string():
    """upsert_setting must be called with a Python bool, not a string."""
    from app.api.admin.settings_router import update_ai_governance_settings
    from app.schemas.admin import AIGovernanceUpdate, ChatModuleGovernanceUpdate

    repo = _make_repo_with_rows([])
    audit_calls: list = []

    async def _fake_audit(**kwargs):
        audit_calls.append(kwargs)

    with patch(
        "app.api.admin.settings_router.get_system_settings_repository",
        return_value=repo,
    ):
        with patch(
            "app.api.admin.settings_router.create_audit_log",
            side_effect=_fake_audit,
        ):
            from unittest.mock import MagicMock

            fake_request = MagicMock()
            fake_request.client.host = "127.0.0.1"
            fake_auth = MagicMock()
            fake_auth.user_id = "admin-1"

            update = AIGovernanceUpdate(
                chat=ChatModuleGovernanceUpdate(user_allowed=False)
            )
            await update_ai_governance_settings(update, fake_auth, fake_request)

    # upsert_setting must have been called with a Python bool, not "false".
    repo.upsert_setting.assert_called_once()
    call_args = repo.upsert_setting.call_args
    key, value, _admin = call_args.args
    assert key == "ai_module.chat.user_allowed"
    assert (
        value is False
    ), f"Expected Python bool False, got {value!r} ({type(value).__name__})"


# ---------------------------------------------------------------------------
# T4-e: PUT — writes base_url/model for task modules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_governance_writes_task_module_fields():
    """PUT writes base_url and model for task modules."""
    from app.api.admin.settings_router import update_ai_governance_settings
    from app.schemas.admin import AIGovernanceUpdate, TaskModuleGovernanceUpdate

    repo = _make_repo_with_rows([])

    async def _fake_audit(**kwargs):
        pass

    with patch(
        "app.api.admin.settings_router.get_system_settings_repository",
        return_value=repo,
    ):
        with patch(
            "app.api.admin.settings_router.create_audit_log", side_effect=_fake_audit
        ):
            from unittest.mock import MagicMock

            fake_request = MagicMock()
            fake_request.client.host = "127.0.0.1"
            fake_auth = MagicMock()
            fake_auth.user_id = "admin-1"

            update = AIGovernanceUpdate(
                transcription=TaskModuleGovernanceUpdate(
                    user_allowed=False,
                    base_url="https://whisper.admin.com/v1",
                    model="whisper-1",
                )
            )
            await update_ai_governance_settings(update, fake_auth, fake_request)

    # Collect all upsert calls.
    calls = {call.args[0]: call.args[1] for call in repo.upsert_setting.call_args_list}
    assert calls.get("ai_module.transcription.user_allowed") is False
    assert (
        calls.get("ai_module.transcription.base_url") == "https://whisper.admin.com/v1"
    )
    assert calls.get("ai_module.transcription.model") == "whisper-1"
    # api_key not in update → must NOT have been written.
    assert "ai_module.transcription.api_key" not in calls


# ---------------------------------------------------------------------------
# T4-f: PUT — blank api_key not written; non-blank api_key is written
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_governance_blank_api_key_not_written():
    """Blank api_key in payload → upsert_setting NOT called for api_key key."""
    from app.api.admin.settings_router import update_ai_governance_settings
    from app.schemas.admin import AIGovernanceUpdate, TaskModuleGovernanceUpdate

    repo = _make_repo_with_rows([])

    async def _fake_audit(**kwargs):
        pass

    with patch(
        "app.api.admin.settings_router.get_system_settings_repository",
        return_value=repo,
    ):
        with patch(
            "app.api.admin.settings_router.create_audit_log", side_effect=_fake_audit
        ):
            from unittest.mock import MagicMock

            fake_request = MagicMock()
            fake_request.client.host = "127.0.0.1"
            fake_auth = MagicMock()
            fake_auth.user_id = "admin-1"

            update = AIGovernanceUpdate(
                translation=TaskModuleGovernanceUpdate(
                    user_allowed=False,
                    api_key="   ",  # blank → keep existing
                )
            )
            await update_ai_governance_settings(update, fake_auth, fake_request)

    keys_written = {call.args[0] for call in repo.upsert_setting.call_args_list}
    assert "ai_module.translation.api_key" not in keys_written


@pytest.mark.asyncio
async def test_put_governance_nonblank_api_key_written():
    """Non-blank api_key in payload → upsert_setting called for api_key key."""
    from app.api.admin.settings_router import update_ai_governance_settings
    from app.schemas.admin import AIGovernanceUpdate, TaskModuleGovernanceUpdate

    repo = _make_repo_with_rows([])

    async def _fake_audit(**kwargs):
        pass

    with patch(
        "app.api.admin.settings_router.get_system_settings_repository",
        return_value=repo,
    ):
        with patch(
            "app.api.admin.settings_router.create_audit_log", side_effect=_fake_audit
        ):
            from unittest.mock import MagicMock

            fake_request = MagicMock()
            fake_request.client.host = "127.0.0.1"
            fake_auth = MagicMock()
            fake_auth.user_id = "admin-1"

            update = AIGovernanceUpdate(
                caption=TaskModuleGovernanceUpdate(
                    user_allowed=False,
                    api_key="sk-real-admin-key",
                )
            )
            await update_ai_governance_settings(update, fake_auth, fake_request)

    calls = {call.args[0]: call.args[1] for call in repo.upsert_setting.call_args_list}
    assert calls.get("ai_module.caption.api_key") == "sk-real-admin-key"


# ---------------------------------------------------------------------------
# T4-g: GET — raw api_key NEVER in response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_governance_never_returns_raw_api_key():
    """The response model must not expose any api_key field — only api_key_set."""
    from app.api.admin.settings_router import _read_governance_settings

    rows = _make_rows(
        ("ai_module.visual_analysis.user_allowed", False),
        ("ai_module.visual_analysis.api_key", "should-never-appear"),
    )
    repo = _make_repo_with_rows(rows)

    with patch(
        "app.api.admin.settings_router.get_system_settings_repository",
        return_value=repo,
    ):
        result = await _read_governance_settings()

    result_dict = result.model_dump()

    # Recursively confirm no "api_key" key with a real value leaks out.
    def _find_api_key(d, path=""):
        for k, v in d.items():
            if k == "api_key":
                raise AssertionError(
                    f"Raw api_key leaked into response at {path}.{k} = {v!r}"
                )
            if isinstance(v, dict):
                _find_api_key(v, path=f"{path}.{k}")

    _find_api_key(result_dict)
    # api_key_set should be True since the key is non-empty.
    assert result.visual_analysis.api_key_set is True
