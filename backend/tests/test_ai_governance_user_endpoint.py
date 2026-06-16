"""T5 — User-facing GET /ai/governance endpoint tests.

Tests for ``app.api.ai_settings_router.get_ai_governance``.

Tests that:
- Returns a dict keyed by all 6 governed module names.
- Values are booleans only (no api_key, no base_url, no model).
- Absent settings → all True (default-open).
- Locked module → False in response.
- Unknown modules are not included.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.governance.ai_governance import ALL_MODULES, AIModuleGovernance

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gov_allowed() -> AIModuleGovernance:
    return AIModuleGovernance(allowed=True)


def _gov_locked() -> AIModuleGovernance:
    return AIModuleGovernance(
        allowed=False,
        base_url="https://api.example.com/v1",
        model="qwen-max",
        api_key="sk-x",
    )


# ---------------------------------------------------------------------------
# T5-a: all modules allowed by default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_governance_endpoint_all_allowed():
    """When no admin settings exist, all modules are allowed=True."""
    from app.api.ai_settings_router import get_ai_governance

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=_gov_allowed()),
    ):
        from unittest.mock import MagicMock

        fake_auth = MagicMock()
        fake_auth.user_id = "user-1"
        result = await get_ai_governance(fake_auth)

    assert isinstance(result, dict)
    for module in ALL_MODULES:
        assert module in result, f"Module {module!r} missing from response"
        assert result[module] is True, f"Module {module!r} should be allowed"


# ---------------------------------------------------------------------------
# T5-b: locked module returns False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_governance_endpoint_locked_module():
    """A locked module returns False in the user response."""
    from app.api.ai_settings_router import get_ai_governance
    from app.services.ai.governance import ai_governance as gov_mod

    # Return locked governance only for 'translation'; all others allowed.
    async def _selective_governance(module: str) -> AIModuleGovernance:
        if module == "translation":
            return _gov_locked()
        return _gov_allowed()

    with patch.object(
        gov_mod, "get_module_governance", side_effect=_selective_governance
    ):
        # Patch the import used inside get_ai_governance
        with patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            side_effect=_selective_governance,
        ):
            from unittest.mock import MagicMock

            fake_auth = MagicMock()
            fake_auth.user_id = "user-1"
            result = await get_ai_governance(fake_auth)

    assert result["translation"] is False
    assert result["transcription"] is True
    assert result["chat"] is True


# ---------------------------------------------------------------------------
# T5-c: response contains exactly ALL_MODULES keys, no extras
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_governance_endpoint_exact_module_set():
    """Response keys must match ALL_MODULES exactly — no extras, no missing."""
    from app.api.ai_settings_router import get_ai_governance

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=_gov_allowed()),
    ):
        from unittest.mock import MagicMock

        fake_auth = MagicMock()
        result = await get_ai_governance(fake_auth)

    assert set(result.keys()) == ALL_MODULES


# ---------------------------------------------------------------------------
# T5-d: response values are booleans only — no key material leaked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_governance_endpoint_no_key_material():
    """Response values must be plain booleans — no api_key, base_url, model."""
    from app.api.ai_settings_router import get_ai_governance

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=_gov_locked()),
    ):
        from unittest.mock import MagicMock

        fake_auth = MagicMock()
        result = await get_ai_governance(fake_auth)

    for module, value in result.items():
        assert isinstance(
            value, bool
        ), f"Module {module!r}: expected bool, got {type(value).__name__!r} = {value!r}"
