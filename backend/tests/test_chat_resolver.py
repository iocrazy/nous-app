"""A4 — resolve_chat_config origin/branch parity.

Pins the three resolution branches of the chat resolver (lifted out of
ai_library_chat_wiring.build_agent_runner_stack) and the ResolvedAIConfig each
produces. Mocks the SAME seams the existing chat governance tests mock:
``get_module_governance`` (the toggle — chat has no admin model/key path) and
the injected ``load_user_config`` awaitable (stands in for the wiring's
``_load_user_provider_config``). No DB.

Key chat-specific invariants under test:
- Chat governance is a pure toggle; locked → origin='governance', empty dict.
- The user's FULL ai_providers dict flows through as provider_config (not a
  single-provider narrowing) — get_adapter_for_user narrows per-model itself.
- The agent owns the model → ResolvedAIConfig.model is always ''.
- The load is skipped entirely when locked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.governance.ai_governance import AIModuleGovernance
from app.services.ai.providers import ai_provider_helpers as helpers

pytestmark = pytest.mark.asyncio


def _locked() -> AIModuleGovernance:
    return AIModuleGovernance(allowed=False)


def _allowed() -> AIModuleGovernance:
    return AIModuleGovernance(allowed=True)


async def test_origin_governance_when_locked_skips_load():
    """Locked chat → origin=governance, provider_config={}, model='' and the
    injected loader is NEVER awaited (locking must skip the user BYOK read)."""
    loader = AsyncMock(return_value={"qwen": {"api_key": "user-key"}})
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_locked()),
    ):
        cfg = await helpers.resolve_chat_config(
            "u", model="qwen-max", load_user_config=loader, agent_slug="script_ai"
        )

    assert cfg.origin == "governance"
    assert cfg.provider_config == {}
    assert cfg.model == ""
    assert cfg.agent_slug == "script_ai"
    assert cfg.provider_key == "qwen"  # derived from the agent's model
    loader.assert_not_awaited()


async def test_origin_byok_when_model_provider_has_key():
    """Allowed + the model's provider entry carries an api_key → origin=byok;
    the FULL ai_providers dict flows through untouched."""
    providers = {
        "qwen": {"api_key": "sk-qwen", "base_url": "https://x"},
        "openai": {"api_key": "sk-openai"},
    }
    loader = AsyncMock(return_value=providers)
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_allowed()),
    ):
        cfg = await helpers.resolve_chat_config(
            "u", model="qwen-max", load_user_config=loader
        )

    assert cfg.origin == "byok"
    assert cfg.provider_key == "qwen"
    assert cfg.provider_config is providers  # full dict, byte-identical object
    assert cfg.model == ""
    loader.assert_awaited_once()


async def test_origin_env_when_model_provider_has_no_key():
    """Allowed but the model's provider has no BYOK key (only another provider
    is configured) → origin=env; the full dict is still returned unnarrowed."""
    providers = {"openai": {"api_key": "sk-openai"}}  # model is qwen-max → qwen
    loader = AsyncMock(return_value=providers)
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_allowed()),
    ):
        cfg = await helpers.resolve_chat_config(
            "u", model="qwen-max", load_user_config=loader
        )

    assert cfg.origin == "env"
    assert cfg.provider_key == "qwen"
    assert cfg.provider_config is providers
    assert cfg.model == ""


async def test_origin_env_when_no_providers_configured():
    """Allowed + empty ai_providers dict → origin=env (adapter factory falls
    back to env credentials)."""
    loader = AsyncMock(return_value={})
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_allowed()),
    ):
        cfg = await helpers.resolve_chat_config(
            "u", model="qwen-max", load_user_config=loader
        )

    assert cfg.origin == "env"
    assert cfg.provider_config == {}


async def test_empty_key_string_is_env_not_byok():
    """A configured provider entry with a blank api_key resolves to env, mirroring
    _byok_origin's whitespace-strip semantics used by the sibling resolvers."""
    providers = {"qwen": {"api_key": "   "}}
    loader = AsyncMock(return_value=providers)
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_allowed()),
    ):
        cfg = await helpers.resolve_chat_config(
            "u", model="qwen-max", load_user_config=loader
        )

    assert cfg.origin == "env"


async def test_unknown_model_prefix_degrades_provider_key_to_env():
    """A model with no known provider prefix → provider_key='' (no ValueError
    escapes) → origin=env even though some other provider has a key."""
    providers = {"qwen": {"api_key": "sk-qwen"}}
    loader = AsyncMock(return_value=providers)
    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        AsyncMock(return_value=_allowed()),
    ):
        cfg = await helpers.resolve_chat_config(
            "u", model="totally-unknown-9000", load_user_config=loader
        )

    assert cfg.provider_key == ""
    assert cfg.origin == "env"
    assert cfg.provider_config is providers
