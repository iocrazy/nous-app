"""summarize 接 fallback 链(spec §3):链替换裸 adapter、LLM 异常上抛、pause 维持 None。"""

from __future__ import annotations

import inspect
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

import app.services.ai.llm.fallback_wiring as fw
from app.db import session as db_session
from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
from app.services.ai.llm.llm_retry_middleware import LLMCallError
from app.services.ai.runner.run_recorder import AgentPausedError
from app.services.ai.summarize.summarize_service import SummarizeService

_MOD = "app.services.ai.summarize.summarize_service"
_ASSIGNED = "doubao-seed-1-6-250615"


def _composed(model: str = _ASSIGNED) -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="summarize",
        model=model,
        temperature=0.0,
        max_tokens=512,
        system_message="x",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="x",
    )


def _make_recorder_cm(recorder: MagicMock | None = None) -> MagicMock:
    """Async-context-manager stand-in for RunRecorder(...). __aexit__
    returns False so exceptions raised inside the ``async with`` block
    propagate out (mirrors the real RunRecorder.__aexit__ contract)."""
    recorder = recorder if recorder is not None else MagicMock()
    recorder.set_summaries = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=recorder)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# 1-2: build_fallback_llm wiring (bare path — adapter build is shared code
# before the user_id branch, so user_id=None is the simplest capture point).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_builds_fallback_chain_with_given_models() -> None:
    svc = SummarizeService(
        provider_key="doubao",
        provider_config={"model": _ASSIGNED, "api_key": "k", "base_url": "http://h/v1"},
    )
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={
            "content": '{"summary":"s","key_points":[],"topics":[]}',
            "raw": {},
        }
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
    ):
        await svc.summarize(
            transcript="hello world",
            user_id=None,
            parsed_media_id=1,
            title="T",
            fallback_models=["lite"],
        )

    build_mock.assert_awaited_once_with(
        primary_model=_ASSIGNED,
        fallback_models=["lite"],
        user_provider_config=svc._provider_config,
        provider_key=svc._provider_key,
        module="summarization",
    )
    assert captured["runner_kwargs"]["adapter"] is chain_sentinel


@pytest.mark.asyncio
async def test_summarize_fallback_models_none_or_empty_still_routes_through_chain() -> (
    None
):
    """fallback_models=None/[] → build_fallback_llm still called (with an
    empty fallback list) — behavior equivalent to today (no fallback swap
    possible, but the call path is unified)."""
    svc = SummarizeService(
        provider_key="doubao",
        provider_config={"model": _ASSIGNED, "api_key": "k", "base_url": "http://h/v1"},
    )
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={
            "content": '{"summary":"s","key_points":[],"topics":[]}',
            "raw": {},
        }
    )

    build_mock = AsyncMock(return_value=MagicMock())

    for fallback_models in (None, []):
        build_mock.reset_mock()
        with (
            patch(f"{_MOD}.PromptComposer", return_value=composer),
            patch(f"{_MOD}.AgentRunner", return_value=runner),
            patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
            patch(
                "app.services.ai.llm.fallback_wiring.build_fallback_llm",
                new=build_mock,
            ),
        ):
            result = await svc.summarize(
                transcript="hello world",
                user_id=None,
                parsed_media_id=1,
                title="T",
                fallback_models=fallback_models,
            )
        build_mock.assert_awaited_once_with(
            primary_model=_ASSIGNED,
            fallback_models=[],
            user_provider_config=svc._provider_config,
            provider_key=svc._provider_key,
            module="summarization",
        )
        assert result is not None
        assert result.summary == "s"


# ---------------------------------------------------------------------------
# 2b (final-review I3/⑤a): fake-adapter integration test through the REAL
# build_fallback_llm — only resolve_mediahub_model (→ no platform-catalog
# hit) and the adapter factory seam (get_adapter_for_user) are mocked, so
# LLMFallbackChain + LLMRetryMiddleware run for real. A C1-shaped regression
# (build_fallback_llm handing the flat provider_config straight to
# get_adapter_for_user instead of wrapping it under a provider key) would
# have made get_adapter_for_user's ``.get(provider_key, {})`` always find
# empty credentials — this test would have caught that via the assertion on
# the config it actually receives.
# ---------------------------------------------------------------------------


class _RateLimited(Exception):
    status_code = 429


class _FakeChatAdapter:
    """Minimal OpenAI-chat-completions-shaped adapter stub."""

    def __init__(self, model: str, *, should_fail: bool) -> None:
        self.model = model
        self.should_fail = should_fail

    async def call(self, composed: Any, messages: list[dict]) -> dict:
        if self.should_fail:
            raise _RateLimited("rate limited")
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"summary":"ok","key_points":[],"topics":[]}'
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {},
        }


@pytest.mark.asyncio
async def test_summarize_falls_back_on_429_through_real_build_fallback_llm() -> None:
    primary = _ASSIGNED
    fallback = "doubao-lite"
    svc = SummarizeService(
        provider_key="doubao",
        provider_config={"model": primary, "api_key": "k1", "base_url": "http://h1/v1"},
    )
    composed = _composed(primary)
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    captured_scoped_configs: list[dict] = []

    def _fake_get_adapter_for_user(model, user_provider_config, _settings):
        captured_scoped_configs.append(dict(user_provider_config))
        # C1 regression guard: pre-fix, build_fallback_llm passed the FLAT
        # {"model","api_key","base_url"} config straight through — this
        # assertion would fail against that shape (no "doubao" key at all).
        assert "doubao" in user_provider_config
        assert user_provider_config["doubao"]["api_key"] == "k1"
        return _FakeChatAdapter(model, should_fail=(model == primary))

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=None)),
        patch.object(
            fw, "get_adapter_for_user", side_effect=_fake_get_adapter_for_user
        ),
        patch("asyncio.sleep", AsyncMock(return_value=None)),
    ):
        result = await svc.summarize(
            transcript="hello world",
            user_id=None,
            parsed_media_id=1,
            title="T",
            fallback_models=[fallback],
        )

    assert result is not None
    assert result.summary == "ok"
    # I3(c)/I2: the model that actually served the response is the
    # FALLBACK, not the primary that 429'd — run_summarize_agent threads
    # this into resource_summaries.llm_model instead of always the primary.
    assert result.llm_model == fallback
    # Exactly one adapter build per model attempted (primary, then fallback)
    # — retries reuse the same adapter instance, they don't rebuild it.
    assert len(captured_scoped_configs) == 2


# ---------------------------------------------------------------------------
# 3: LLM-class exceptions from the recorder path PROPAGATE (no longer
# swallowed to None) — record-then-raise (PR #1743) needs the real
# exception type to classify_ai_error correctly.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_recorder_path_propagates_all_models_failed() -> None:
    svc = SummarizeService(provider_config={"model": _ASSIGNED})
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
    ):
        with pytest.raises(AllModelsFailed):
            await svc.summarize(
                transcript="hello world",
                user_id="00000000-0000-0000-0000-000000000002",
                parsed_media_id=1,
                title="T",
            )


@pytest.mark.asyncio
async def test_summarize_recorder_path_propagates_llm_call_error() -> None:
    svc = SummarizeService(provider_config={"model": _ASSIGNED})
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
    ):
        with pytest.raises(LLMCallError):
            await svc.summarize(
                transcript="hello world",
                user_id="00000000-0000-0000-0000-000000000002",
                parsed_media_id=1,
                title="T",
            )


# ---------------------------------------------------------------------------
# 4-5: pre-existing None-return paths kept as-is.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_recorder_path_agent_paused_returns_none() -> None:
    svc = SummarizeService(provider_config={"model": _ASSIGNED})
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
    ):
        result = await svc.summarize(
            transcript="hello world",
            user_id="00000000-0000-0000-0000-000000000002",
            parsed_media_id=1,
            title="T",
        )
    assert result is None


@pytest.mark.asyncio
async def test_summarize_recorder_path_error_dict_returns_none() -> None:
    svc = SummarizeService(provider_config={"model": _ASSIGNED})
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
    ):
        result = await svc.summarize(
            transcript="hello world",
            user_id="00000000-0000-0000-0000-000000000002",
            parsed_media_id=1,
            title="T",
        )
    assert result is None


# ---------------------------------------------------------------------------
# 6: DBOS step retry collapsed to 1 — retry + fallback now live entirely in
# LLMFallbackChain; a step-level retry on top would multiply attempts.
# ---------------------------------------------------------------------------


def test_run_summarize_agent_step_max_attempts_is_one() -> None:
    import app.workflows.ai_summary as summary_mod

    src = inspect.getsource(summary_mod)
    idx = src.index("async def run_summarize_agent")
    before = src[:idx]
    decorator_line = before.strip().splitlines()[-1]
    assert re.search(r"max_attempts\s*=\s*1\b", decorator_line), decorator_line


# ---------------------------------------------------------------------------
# 7: load_summary_inputs surfaces fallback_models from the agent row the model
# was resolved FROM (spec §1 — platform-preset fallbacks, per-user primary).
# 2026-08-20 收口: the row is now read by resolve_task_ai_config itself, so the
# governance-locked branch — which bypasses the agent entirely — carries an
# EMPTY pool (same contract as caption / analyze / classify / translate).
# ---------------------------------------------------------------------------


class _FakeExecuteResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def mappings(self) -> "_FakeExecuteResult":
        return self

    def first(self) -> Any:
        return self._row


class _SettingsJsonSession:
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


def _governance_locked() -> Any:
    from app.services.ai.governance.ai_governance import AIModuleGovernance

    return AIModuleGovernance(
        allowed=False,
        base_url="https://admin.example.com/v1",
        model="qwen-max",
        api_key="admin-sum-key",
    )


@pytest.mark.asyncio
async def test_load_summary_inputs_returns_fallback_models_from_agent_row() -> None:
    """Unlocked (agent) path: the pool comes from the resolved agent row."""
    import app.workflows.ai_summary as summary_mod
    from app.services.ai.governance.ai_governance import AIModuleGovernance
    from app.services.ai.providers import ai_provider_helpers as helpers_mod

    fake_row = {
        "transcript": "Hello world transcript.",
        "pm_id": 1,
        "title": "Test Video",
        "resource_id": 99,
    }
    fake_agent_repo = MagicMock()
    fake_agent_repo.get_by_slug = AsyncMock(
        return_value={
            "slug": "summarize",
            "model": "doubao-seed-2-0-lite-260428",
            "fallback_models": ["doubao-lite"],
        }
    )

    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            new=AsyncMock(return_value=AIModuleGovernance(allowed=True)),
        ),
        patch.object(
            db_session, "read_scope", lambda: _SettingsJsonScope(execute_row=fake_row)
        ),
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=fake_agent_repo,
        ),
        patch.object(
            helpers_mod,
            "get_ai_settings",
            new=AsyncMock(return_value={"task_assignment": {}, "ai_providers": {}}),
        ),
        patch.object(
            helpers_mod, "resolve_mediahub_model", new=AsyncMock(return_value=None)
        ),
    ):
        result = await summary_mod.load_summary_inputs(1, "user-1")

    assert result["fallback_models"] == ["doubao-lite"]
    assert result["agent_slug"] == "summarize"


@pytest.mark.asyncio
async def test_load_summary_inputs_governance_lock_carries_empty_pool() -> None:
    """Locked branch never reads an agent row → empty pool, and the admin's
    model/key are what ride out (contract shared with every other module)."""
    import app.workflows.ai_summary as summary_mod

    fake_row = {
        "transcript": "Hello world transcript.",
        "pm_id": 1,
        "title": "Test Video",
        "resource_id": 99,
    }
    fake_agent_repo = MagicMock()
    fake_agent_repo.get_by_slug = AsyncMock(
        return_value={"fallback_models": ["doubao-lite"]}
    )

    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            new=AsyncMock(return_value=_governance_locked()),
        ),
        patch.object(
            db_session, "read_scope", lambda: _SettingsJsonScope(execute_row=fake_row)
        ),
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=fake_agent_repo,
        ),
    ):
        result = await summary_mod.load_summary_inputs(1, "user-1")

    assert result["fallback_models"] == []
    assert result["provider_config"]["api_key"] == "admin-sum-key"
    fake_agent_repo.get_by_slug.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_summary_inputs_fallback_models_missing_row_is_empty_list() -> None:
    import app.workflows.ai_summary as summary_mod

    fake_row = {
        "transcript": "Hello world transcript.",
        "pm_id": 1,
        "title": "Test Video",
        "resource_id": 99,
    }
    fake_agent_repo = MagicMock()
    fake_agent_repo.get_by_slug = AsyncMock(return_value=None)

    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            new=AsyncMock(return_value=_governance_locked()),
        ),
        patch.object(
            db_session, "read_scope", lambda: _SettingsJsonScope(execute_row=fake_row)
        ),
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=fake_agent_repo,
        ),
    ):
        result = await summary_mod.load_summary_inputs(1, "user-1")

    assert result["fallback_models"] == []


@pytest.mark.asyncio
async def test_load_summary_inputs_fallback_models_none_field_is_empty_list() -> None:
    import app.workflows.ai_summary as summary_mod

    fake_row = {
        "transcript": "Hello world transcript.",
        "pm_id": 1,
        "title": "Test Video",
        "resource_id": 99,
    }
    fake_agent_repo = MagicMock()
    fake_agent_repo.get_by_slug = AsyncMock(return_value={"fallback_models": None})

    with (
        patch(
            "app.services.ai.governance.ai_governance.get_module_governance",
            new=AsyncMock(return_value=_governance_locked()),
        ),
        patch.object(
            db_session, "read_scope", lambda: _SettingsJsonScope(execute_row=fake_row)
        ),
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=fake_agent_repo,
        ),
    ):
        result = await summary_mod.load_summary_inputs(1, "user-1")

    assert result["fallback_models"] == []
