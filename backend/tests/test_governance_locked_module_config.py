"""Unit tests for ``resolve_locked_module_config`` — the shared AI-governance
lock gate extracted from the three call sites (resolve_task_provider_config,
ai_transcription.load_transcribe_inputs, ai_summary.load_summary_inputs).

Covers the full resolution matrix:
  - not-locked → None (caller proceeds down its own user path)
  - locked + catalog model → platform config (catalog carries its own creds)
  - locked + catalog-name not in catalog → manual admin config
  - locked + catalog model disabled + manual key → falls through to manual (warn)
  - locked + catalog model disabled + no key → re-raises "no longer available"
  - locked + no model + no key → fail-closed with the generic admin-locked message
  - manual derived-key ValueError → default_provider_key (both "" and "openai")

Mocks ``get_module_governance`` (same module) and ``resolve_platform_model``
(lazily imported by the helper from ai_provider_helpers) with monkeypatch, in
the style of tests/test_ai_governance_resolver.py — no live DB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.governance.ai_governance import (
    AIModuleGovernance,
    LockedModuleConfig,
    resolve_locked_module_config,
)

_GOV = "app.services.ai.governance.ai_governance.get_module_governance"
_PLATFORM = "app.services.ai.providers.ai_provider_helpers.resolve_platform_model"


def _locked(
    *, api_key: str = "", base_url: str = "", model: str = ""
) -> AIModuleGovernance:
    return AIModuleGovernance(
        allowed=False, base_url=base_url, model=model, api_key=api_key
    )


# ---------------------------------------------------------------------------
# not-locked → None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_not_locked_returns_none():
    """allowed module → None; the caller falls through to its user path."""
    with patch(_GOV, new=AsyncMock(return_value=AIModuleGovernance(allowed=True))):
        result = await resolve_locked_module_config("visual_analysis")
    assert result is None


# ---------------------------------------------------------------------------
# locked + catalog model → platform config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_catalog_model_returns_platform_config():
    """A catalog hit returns the platform config even when the manual api_key
    is blank — catalog models carry their own credentials (#857)."""
    platform_cfg = {
        "api_key": "cat-key",
        "base_url": "https://ark/v3",
        "model": "doubao-x",
        "app_id": "",
    }
    with (
        patch(_GOV, new=AsyncMock(return_value=_locked(model="mediahub-doubao-pro"))),
        patch(
            _PLATFORM,
            new=AsyncMock(return_value=("doubao", platform_cfg, "doubao-x")),
        ),
    ):
        result = await resolve_locked_module_config("visual_analysis")

    assert isinstance(result, LockedModuleConfig)
    assert result.provider_key == "doubao"
    assert result.provider_config == platform_cfg
    assert result.model == "doubao-x"


# ---------------------------------------------------------------------------
# locked + catalog-name not in catalog → manual admin config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_non_catalog_model_uses_manual_config():
    """resolve_platform_model returns None (ordinary manual model string) →
    the manual admin base_url / model / api_key is used, provider derived."""
    with (
        patch(
            _GOV,
            new=AsyncMock(
                return_value=_locked(
                    api_key="admin-key",
                    base_url="https://admin/v1",
                    model="qwen-max",
                )
            ),
        ),
        patch(_PLATFORM, new=AsyncMock(return_value=None)),
    ):
        result = await resolve_locked_module_config("translation")

    assert result is not None
    assert result.provider_key == "qwen"  # derived from "qwen-max"
    assert result.provider_config == {
        "api_key": "admin-key",
        "base_url": "https://admin/v1",
        "model": "qwen-max",
        "app_id": "",
    }
    assert result.model == "qwen-max"


# ---------------------------------------------------------------------------
# locked + catalog disabled + manual key → falls through to manual (warning)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_catalog_disabled_with_manual_key_falls_through():
    """Found-but-disabled catalog model, but the admin ALSO set a manual key →
    warn and use the manual config (the manual key still works)."""
    with (
        patch(
            _GOV,
            new=AsyncMock(
                return_value=_locked(
                    api_key="admin-key",
                    base_url="https://admin/v1",
                    model="doubao-pro-32k",
                )
            ),
        ),
        patch(
            _PLATFORM,
            new=AsyncMock(
                side_effect=RuntimeError(
                    "Platform model 'doubao-pro-32k' is no longer available."
                )
            ),
        ),
    ):
        result = await resolve_locked_module_config("caption")

    assert result is not None
    assert result.provider_key == "doubao"
    assert result.provider_config["api_key"] == "admin-key"
    assert result.model == "doubao-pro-32k"


# ---------------------------------------------------------------------------
# locked + catalog disabled + no manual key → re-raise "no longer available"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_catalog_disabled_no_key_reraises_informative_error():
    """Found-but-disabled catalog model with NO manual key → re-raise the
    informative "no longer available" error, not the generic no-key one."""
    with (
        patch(_GOV, new=AsyncMock(return_value=_locked(model="mediahub-gone"))),
        patch(
            _PLATFORM,
            new=AsyncMock(
                side_effect=RuntimeError(
                    "Platform model 'mediahub-gone' is no longer available."
                )
            ),
        ),
    ):
        with pytest.raises(RuntimeError, match="no longer available"):
            await resolve_locked_module_config("classification")


# ---------------------------------------------------------------------------
# locked + no model + no key → fail-closed generic message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_no_model_no_key_fails_closed_generic():
    """No model to look up and no admin api_key → fail-closed with the generic
    admin-locked message (unchanged wording)."""
    with patch(_GOV, new=AsyncMock(return_value=_locked(api_key=""))):
        with pytest.raises(RuntimeError, match="admin-locked but no admin API key"):
            await resolve_locked_module_config("translation")


@pytest.mark.asyncio
async def test_locked_non_catalog_model_no_key_fails_closed():
    """Manual (non-catalog) model but blank api_key → also fail-closed generic."""
    with (
        patch(_GOV, new=AsyncMock(return_value=_locked(model="gpt-4o", api_key=""))),
        patch(_PLATFORM, new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(RuntimeError, match="admin-locked but no admin API key"):
            await resolve_locked_module_config("summarization")


# ---------------------------------------------------------------------------
# derived-key ValueError → default_provider_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_derived_key_valueerror_falls_back_to_default_empty():
    """Unknown model prefix → provider_key defaults to default_provider_key
    (default "" — generic OpenAI-compatible adapter)."""
    with (
        patch(
            _GOV,
            new=AsyncMock(return_value=_locked(api_key="k", model="custom-llm-v1")),
        ),
        patch(_PLATFORM, new=AsyncMock(return_value=None)),
    ):
        result = await resolve_locked_module_config("visual_analysis")

    assert result is not None
    assert result.provider_key == ""
    assert result.model == "custom-llm-v1"


@pytest.mark.asyncio
async def test_derived_key_valueerror_falls_back_to_default_openai():
    """Unknown model prefix with default_provider_key="openai" (the whisper
    path) → provider_key defaults to "openai"."""
    with (
        patch(
            _GOV,
            new=AsyncMock(return_value=_locked(api_key="k", model="custom-asr-v1")),
        ),
        patch(_PLATFORM, new=AsyncMock(return_value=None)),
    ):
        result = await resolve_locked_module_config(
            "transcription", default_provider_key="openai"
        )

    assert result is not None
    assert result.provider_key == "openai"
    assert result.model == "custom-asr-v1"


@pytest.mark.asyncio
async def test_no_model_derives_default_provider_key():
    """No model at all → provider_key is default_provider_key directly (manual
    key present so it does not fail closed)."""
    with patch(
        _GOV, new=AsyncMock(return_value=_locked(api_key="k", base_url="https://x/v1"))
    ):
        result = await resolve_locked_module_config(
            "transcription", default_provider_key="openai"
        )

    assert result is not None
    assert result.provider_key == "openai"
    assert result.model == ""
    assert result.provider_config["app_id"] == ""
