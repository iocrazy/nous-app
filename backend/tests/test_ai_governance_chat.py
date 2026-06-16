"""T3 — Chat governance gate in build_agent_runner_stack.

Tests that:
- Locked chat module → user_provider_config is set to {} (platform keys used)
- Allowed chat module → _load_user_provider_config is called normally

The chat module differs from task modules: get_adapter_for_user has an env
fallback, so no admin api_key is needed.  Locking simply suppresses the
user BYO key lookup.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.governance.ai_governance import AIModuleGovernance


def _locked_chat() -> AIModuleGovernance:
    return AIModuleGovernance(allowed=False)


def _allowed_chat() -> AIModuleGovernance:
    return AIModuleGovernance(allowed=True)


def _make_fake_agent(model: str = "qwen-max") -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "slug": "test-agent",
        "model": model,
        "fallback_models": [],
        "budget_per_run_cents": None,
        "capability_profile": {},
    }


def _make_minimal_settings() -> MagicMock:
    s = MagicMock()
    return s


# ---------------------------------------------------------------------------
# T3-a: locked → user BYO key lookup skipped, empty dict used instead
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_chat_skips_user_provider_lookup():
    """When chat is locked, _load_user_provider_config must NOT be called."""
    import app.services.ai.chat.ai_library_chat_wiring as wiring_mod

    governance = _locked_chat()

    fake_adapter = MagicMock()
    fake_chain = MagicMock()
    fake_chain.primary_model = "qwen-max"

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        with patch.object(
            wiring_mod,
            "_load_user_provider_config",
            new=AsyncMock(return_value={"openai": {"api_key": "user-key"}}),
        ) as mock_load:
            with patch(
                "app.services.ai.adapters.factory.get_adapter_for_user",
                return_value=fake_adapter,
            ):
                with patch(
                    "app.services.ai.llm.llm_fallback_chain.LLMFallbackChain",
                    return_value=fake_chain,
                ):
                    with patch.object(
                        wiring_mod,
                        "_safe_recall_memories",
                        new=AsyncMock(return_value=[]),
                    ):
                        with patch.object(
                            wiring_mod,
                            "_safe_recall_graph_facts",
                            new=AsyncMock(return_value=[]),
                        ):
                            with patch.object(
                                wiring_mod,
                                "_safe_recall_honcho_context",
                                new=AsyncMock(return_value=None),
                            ):
                                with patch(
                                    "app.services.ai.runner.agent_runner.AgentRunner",
                                    return_value=MagicMock(),
                                ):
                                    with patch(
                                        "app.services.workforce.delegate_tool.DelegateToolService",
                                        return_value=MagicMock(),
                                    ):
                                        from uuid import UUID

                                        await wiring_mod.build_agent_runner_stack(
                                            agent=_make_fake_agent(),
                                            skill_repo=MagicMock(),
                                            user_id=UUID(
                                                "00000000-0000-0000-0000-000000000099"
                                            ),
                                            session_id="1",
                                            user_query="hello",
                                            settings=_make_minimal_settings(),
                                        )

    # _load_user_provider_config must NOT have been called when locked.
    mock_load.assert_not_called()


@pytest.mark.asyncio
async def test_allowed_chat_calls_user_provider_lookup():
    """When chat is allowed, _load_user_provider_config IS called."""
    import app.services.ai.chat.ai_library_chat_wiring as wiring_mod

    governance = _allowed_chat()

    fake_adapter = MagicMock()
    fake_chain = MagicMock()
    fake_chain.primary_model = "qwen-max"

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=governance),
    ):
        with patch.object(
            wiring_mod,
            "_load_user_provider_config",
            new=AsyncMock(return_value={}),
        ) as mock_load:
            with patch(
                "app.services.ai.adapters.factory.get_adapter_for_user",
                return_value=fake_adapter,
            ):
                with patch(
                    "app.services.ai.llm.llm_fallback_chain.LLMFallbackChain",
                    return_value=fake_chain,
                ):
                    with patch.object(
                        wiring_mod,
                        "_safe_recall_memories",
                        new=AsyncMock(return_value=[]),
                    ):
                        with patch.object(
                            wiring_mod,
                            "_safe_recall_graph_facts",
                            new=AsyncMock(return_value=[]),
                        ):
                            with patch.object(
                                wiring_mod,
                                "_safe_recall_honcho_context",
                                new=AsyncMock(return_value=None),
                            ):
                                with patch(
                                    "app.services.ai.runner.agent_runner.AgentRunner",
                                    return_value=MagicMock(),
                                ):
                                    with patch(
                                        "app.services.workforce.delegate_tool.DelegateToolService",
                                        return_value=MagicMock(),
                                    ):
                                        from uuid import UUID

                                        await wiring_mod.build_agent_runner_stack(
                                            agent=_make_fake_agent(),
                                            skill_repo=MagicMock(),
                                            user_id=UUID(
                                                "00000000-0000-0000-0000-000000000099"
                                            ),
                                            session_id="1",
                                            user_query="hello",
                                            settings=_make_minimal_settings(),
                                        )

    # _load_user_provider_config MUST have been called when allowed.
    mock_load.assert_called_once()
