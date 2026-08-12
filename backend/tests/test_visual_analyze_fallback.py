"""visual analyze 接 fallback 链(spec §4)。

Case groups:
  1. ResolvedAIConfig.fallback_models 默认 (),老式五参构造仍成立(向后兼容)。
  2. resolve_task_ai_config 在 agent 行分支把行上的 fallback_models 带出
     (行无该键/None → ())。非 agent 行分支(governance / nous: 直连 /
     resolve_summarization_config 的 byok 直连,均不读 agent 行) → ()。
  3. VisualAnalysisService:链替换裸 adapter;AllModelsFailed propagate;
     AgentPausedError → None 维持;error-dict → None 维持;bare
     user_id=None 路径未改动。
  4. analyze_l1.call_analyze_l1 装饰器 max_attempts == 1。
  5. resolve_analyze_provider 返回 dict 含 fallback_models(从 cfg 带出)。
"""

from __future__ import annotations

import inspect
import re
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.governance.ai_governance import AIModuleGovernance
from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
from app.services.ai.llm.llm_retry_middleware import LLMCallError
from app.services.ai.providers.ai_provider_helpers import ResolvedAIConfig
from app.services.ai.runner.run_recorder import AgentPausedError
from app.services.ai.visual.visual_analysis_service import VisualAnalysisService

_MOD = "app.services.ai.visual.visual_analysis_service"
_ASSIGNED = "doubao-seed-1-6-250615"


def _composed(model: str = _ASSIGNED) -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="analyze",
        model=model,
        temperature=0.0,
        max_tokens=512,
        system_message="x",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="x",
    )


def _make_recorder_cm(recorder: MagicMock | None = None) -> MagicMock:
    """Async-context-manager stand-in for RunRecorder(...). __aexit__ returns
    False so exceptions raised inside the ``async with`` block propagate out
    (mirrors the real RunRecorder.__aexit__ contract)."""
    recorder = recorder if recorder is not None else MagicMock()
    recorder.set_summaries = MagicMock()
    recorder.prompt_tokens = 0
    recorder.completion_tokens = 0
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=recorder)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _allowed_governance() -> AIModuleGovernance:
    return AIModuleGovernance(allowed=True)


def _locked_governance() -> AIModuleGovernance:
    return AIModuleGovernance(
        allowed=False,
        base_url="https://admin.example.com/v1",
        model="qwen-max",
        api_key="admin-key",
    )


# ---------------------------------------------------------------------------
# 1: ResolvedAIConfig backward compatibility.
# ---------------------------------------------------------------------------


def test_resolved_ai_config_fallback_models_default_empty_tuple() -> None:
    cfg = ResolvedAIConfig(
        provider_key="doubao",
        provider_config={"model": "x"},
        model="x",
        agent_slug="analyze",
        origin="byok",
    )
    assert cfg.fallback_models == ()


def test_resolved_ai_config_fallback_models_explicit() -> None:
    cfg = ResolvedAIConfig(
        provider_key="doubao",
        provider_config={"model": "x"},
        model="x",
        agent_slug="analyze",
        origin="byok",
        fallback_models=("a", "b"),
    )
    assert cfg.fallback_models == ("a", "b")


# ---------------------------------------------------------------------------
# 2: resolve_task_ai_config — agent-row branch carries fallback_models;
# non-agent-row branches stay at the default ().
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_task_ai_config_agent_branch_carries_fallback_models() -> None:
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    fake_agent = {
        "model": "qwen-max",
        "slug": "analyze",
        "fallback_models": ["doubao-lite", "qwen-turbo"],
    }
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
            helpers_mod, "resolve_mediahub_model", new=AsyncMock(return_value=None)
        ),
    ):
        cfg = await helpers_mod.resolve_task_ai_config(
            user_id="user-1", task_key="visual_analysis", default_slug="analyze"
        )

    assert cfg.fallback_models == ("doubao-lite", "qwen-turbo")


@pytest.mark.parametrize("row_extra", [{}, {"fallback_models": None}])
@pytest.mark.asyncio
async def test_resolve_task_ai_config_agent_branch_missing_or_none_is_empty(
    row_extra: dict,
) -> None:
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    fake_agent = {"model": "qwen-max", "slug": "analyze", **row_extra}
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
            helpers_mod, "resolve_mediahub_model", new=AsyncMock(return_value=None)
        ),
    ):
        cfg = await helpers_mod.resolve_task_ai_config(
            user_id="user-1", task_key="visual_analysis", default_slug="analyze"
        )

    assert cfg.fallback_models == ()


@pytest.mark.asyncio
async def test_resolve_task_ai_config_governance_branch_fallback_models_empty() -> None:
    """Governance-locked path bypasses the agent-row read entirely."""
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    with patch(
        "app.services.ai.governance.ai_governance.get_module_governance",
        new=AsyncMock(return_value=_locked_governance()),
    ):
        cfg = await helpers_mod.resolve_task_ai_config(
            user_id="user-1", task_key="visual_analysis", default_slug="analyze"
        )

    assert cfg.origin == "governance"
    assert cfg.fallback_models == ()


@pytest.mark.asyncio
async def test_resolve_task_ai_config_nous_direct_pick_fallback_models_empty() -> None:
    """User ``nous:<model>`` direct pick returns BEFORE the agent row is
    fetched — no agent metadata to carry, so fallback_models stays ()."""
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
                    "task_assignment": {"visual_analysis": "nous:mediahub-doubao-pro"}
                }
            ),
        ),
        patch.object(
            helpers_mod,
            "resolve_mediahub_model",
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
            user_id="user-1", task_key="visual_analysis", default_slug="analyze"
        )

    assert cfg.origin == "platform"
    assert cfg.fallback_models == ()


@pytest.mark.asyncio
async def test_resolve_summarization_config_byok_direct_fallback_models_empty() -> None:
    """resolve_summarization_config never reads an agent row (hardcoded
    provider-priority scan) — its byok origin must stay at the default ()."""
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    settings_json = {
        "ai_settings": {
            "ai_providers": {"doubao": {"api_key": "k", "enabled": True}},
        }
    }

    with patch(
        "app.services.ai.governance.ai_governance.resolve_locked_module_config",
        new=AsyncMock(return_value=None),
    ):
        cfg = await helpers_mod.resolve_summarization_config(
            "user-1", settings_json=settings_json
        )

    assert cfg.origin == "byok"
    assert cfg.fallback_models == ()


# ---------------------------------------------------------------------------
# 3: VisualAnalysisService wiring.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_l1_builds_fallback_chain_with_given_models() -> None:
    svc = VisualAnalysisService(
        provider_key="doubao",
        provider_config={"model": _ASSIGNED, "api_key": "k", "base_url": "http://h/v1"},
    )
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={"content": '{"category":"Other"}', "raw": {}}
    )

    chain_sentinel = MagicMock(name="fallback_chain")
    build_mock = AsyncMock(return_value=chain_sentinel)

    captured: dict = {}

    def _capture_runner(**kwargs):
        captured["runner_kwargs"] = kwargs
        return runner

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", side_effect=_capture_runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=build_mock,
        ),
        patch.object(
            svc,
            "_encode_image_from_url",
            new=AsyncMock(return_value="ZmFrZQ=="),
        ),
    ):
        await svc.analyze_l1(
            "http://example.com/cover.jpg",
            user_id=None,
            fallback_models=["lite"],
        )

    build_mock.assert_awaited_once_with(
        primary_model=_ASSIGNED,
        fallback_models=["lite"],
        user_provider_config=svc._provider_config,
        provider_key=svc._provider_key,
        module="visual_analysis",
    )
    assert captured["runner_kwargs"]["adapter"] is chain_sentinel


@pytest.mark.asyncio
async def test_analyze_l1_fallback_models_none_still_routes_through_chain() -> None:
    svc = VisualAnalysisService(
        provider_key="doubao",
        provider_config={"model": _ASSIGNED, "api_key": "k", "base_url": "http://h/v1"},
    )
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={"content": '{"category":"Other"}', "raw": {}}
    )

    build_mock = AsyncMock(return_value=MagicMock())

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=build_mock,
        ),
        patch.object(
            svc,
            "_encode_image_from_url",
            new=AsyncMock(return_value="ZmFrZQ=="),
        ),
    ):
        result = await svc.analyze_l1(
            "http://example.com/cover.jpg", user_id=None, fallback_models=None
        )

    build_mock.assert_awaited_once_with(
        primary_model=_ASSIGNED,
        fallback_models=[],
        user_provider_config=svc._provider_config,
        provider_key=svc._provider_key,
        module="visual_analysis",
    )
    assert result is not None
    assert result.category == "Other"


@pytest.mark.asyncio
async def test_analyze_l1_recorder_path_propagates_all_models_failed() -> None:
    svc = VisualAnalysisService(provider_config={"model": _ASSIGNED})
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        side_effect=AllModelsFailed("primary+fallbacks exhausted")
    )

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(f"{_MOD}.RunRecorder", return_value=_make_recorder_cm()),
        patch.object(
            svc,
            "_encode_image_from_url",
            new=AsyncMock(return_value="ZmFrZQ=="),
        ),
    ):
        with pytest.raises(AllModelsFailed):
            await svc.analyze_l1(
                "http://example.com/cover.jpg",
                user_id="00000000-0000-0000-0000-000000000002",
            )


@pytest.mark.asyncio
async def test_analyze_l1_recorder_path_propagates_llm_call_error() -> None:
    svc = VisualAnalysisService(provider_config={"model": _ASSIGNED})
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(side_effect=LLMCallError("non-retryable: 401"))

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(f"{_MOD}.RunRecorder", return_value=_make_recorder_cm()),
        patch.object(
            svc,
            "_encode_image_from_url",
            new=AsyncMock(return_value="ZmFrZQ=="),
        ),
    ):
        with pytest.raises(LLMCallError):
            await svc.analyze_l1(
                "http://example.com/cover.jpg",
                user_id="00000000-0000-0000-0000-000000000002",
            )


@pytest.mark.asyncio
async def test_analyze_l1_recorder_path_agent_paused_returns_none() -> None:
    svc = VisualAnalysisService(provider_config={"model": _ASSIGNED})
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(side_effect=AgentPausedError("agent paused"))

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(f"{_MOD}.RunRecorder", return_value=_make_recorder_cm()),
        patch.object(
            svc,
            "_encode_image_from_url",
            new=AsyncMock(return_value="ZmFrZQ=="),
        ),
    ):
        result = await svc.analyze_l1(
            "http://example.com/cover.jpg",
            user_id="00000000-0000-0000-0000-000000000002",
        )
    assert result is None


@pytest.mark.asyncio
async def test_analyze_l1_recorder_path_error_dict_returns_none() -> None:
    svc = VisualAnalysisService(provider_config={"model": _ASSIGNED})
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={"content": "", "error": "boom", "raw": {}}
    )

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(f"{_MOD}.RunRecorder", return_value=_make_recorder_cm()),
        patch.object(
            svc,
            "_encode_image_from_url",
            new=AsyncMock(return_value="ZmFrZQ=="),
        ),
    ):
        result = await svc.analyze_l1(
            "http://example.com/cover.jpg",
            user_id="00000000-0000-0000-0000-000000000002",
        )
    assert result is None


@pytest.mark.asyncio
async def test_analyze_l1_bare_path_swallows_exception_untouched() -> None:
    """bare (user_id=None) path keeps its blanket except → None contract —
    only the recorder path's exception handling changed (spec §3/§4)."""
    svc = VisualAnalysisService(provider_config={"model": _ASSIGNED})
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch.object(
            svc,
            "_encode_image_from_url",
            new=AsyncMock(return_value="ZmFrZQ=="),
        ),
    ):
        result = await svc.analyze_l1("http://example.com/cover.jpg", user_id=None)
    assert result is None


# ---------------------------------------------------------------------------
# 4: DBOS step retry collapsed to 1.
# ---------------------------------------------------------------------------


def test_call_analyze_l1_step_max_attempts_is_one() -> None:
    import app.workflows.analyze_l1 as analyze_l1_mod

    src = inspect.getsource(analyze_l1_mod)
    idx = src.index("async def call_analyze_l1")
    before = src[:idx]
    decorator_line = before.strip().splitlines()[-1]
    assert re.search(r"max_attempts\s*=\s*1\b", decorator_line), decorator_line


# ---------------------------------------------------------------------------
# 5: resolve_analyze_provider step surfaces fallback_models from cfg.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_analyze_provider_returns_fallback_models_from_cfg() -> None:
    import app.workflows.analyze_l1 as analyze_l1_mod

    cfg = ResolvedAIConfig(
        provider_key="doubao",
        provider_config={"api_key": "k", "base_url": "u", "model": "m"},
        model="m",
        agent_slug="analyze",
        origin="byok",
        fallback_models=("doubao-lite", "qwen-turbo"),
    )

    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_task_ai_config",
        new=AsyncMock(return_value=cfg),
    ):
        result = await analyze_l1_mod.resolve_analyze_provider("user-1")

    assert result["fallback_models"] == ["doubao-lite", "qwen-turbo"]


@pytest.mark.asyncio
async def test_resolve_analyze_provider_fallback_models_empty_when_cfg_empty() -> None:
    import app.workflows.analyze_l1 as analyze_l1_mod

    cfg = ResolvedAIConfig(
        provider_key="doubao",
        provider_config={"api_key": "k", "base_url": "u", "model": "m"},
        model="m",
        agent_slug="analyze",
        origin="byok",
    )

    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_task_ai_config",
        new=AsyncMock(return_value=cfg),
    ):
        result = await analyze_l1_mod.resolve_analyze_provider("user-1")

    assert result["fallback_models"] == []
