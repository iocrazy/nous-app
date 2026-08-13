"""caption 接 fallback 链(spec 2026-08-12-batch-fallback-rollout §1-F1)。

Case groups:
  1. CaptionService:链替换裸 _build_adapter;fallback_models None/[] 仍走链。
  2. fake-adapter 真链集成测试(照 test_summarize_fallback.py §2b 结构):
     429→fallback 直通,断言 scoped config 形状(C1 回归钉)。
  3. RunRecorder 路径:LLM 类异常(AllModelsFailed/LLMCallError)propagate;
     AgentPausedError / error-dict 维持 None;bare(user_id=None)路径未改动。
  4. call_caption 装饰器 max_attempts == 1。
  5. resolve_caption_provider 直调 resolve_task_ai_config(task_key="caption"),
     返回 dict 带出 fallback_models。
  6. 两个 workflow(caption_asset / caption_slide)把 resolve 出的
     fallback_models 原样透传进 call_caption。
"""

from __future__ import annotations

import inspect
import re
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

import app.services.ai.llm.fallback_wiring as fw
from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.caption.caption_service import CaptionService
from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
from app.services.ai.llm.llm_retry_middleware import LLMCallError
from app.services.ai.providers.ai_provider_helpers import ResolvedAIConfig
from app.services.ai.runner.run_recorder import AgentPausedError

_MOD = "app.services.ai.caption.caption_service"
_ASSIGNED = "doubao-seed-1-6-250615"
_USER = "00000000-0000-0000-0000-000000000002"


def _composed(model: str = _ASSIGNED) -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="caption",
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
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=recorder)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# 1: build_fallback_llm wiring (bare path — adapter build is shared code
# before the user_id branch, so user_id=None is the simplest capture point).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caption_builds_fallback_chain_with_given_models(tmp_path) -> None:
    svc = CaptionService(
        provider_key="doubao",
        provider_config={"model": _ASSIGNED, "api_key": "k", "base_url": "http://h/v1"},
    )
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={"content": '{"prompt_en":"ok","prompt_zh":"zh"}', "raw": {}}
    )

    chain_sentinel = MagicMock(name="fallback_chain")
    build_mock = AsyncMock(return_value=chain_sentinel)

    captured: dict = {}

    def _capture_runner(**kwargs):
        captured["runner_kwargs"] = kwargs
        return runner

    fake_img = tmp_path / "x.jpg"
    fake_img.write_bytes(b"jpeg")

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", side_effect=_capture_runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            f"{_MOD}._encode_image_sync",
            return_value="data:image/jpeg;base64,ZmFrZQ==",
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=build_mock,
        ),
    ):
        await svc.caption(
            file_path=str(fake_img),
            user_id=None,
            resource_id="r1",
            fallback_models=["lite"],
        )

    build_mock.assert_awaited_once_with(
        primary_model=_ASSIGNED,
        fallback_models=["lite"],
        user_provider_config=svc._provider_config,
        provider_key=svc._provider_key,
        module="caption",
    )
    assert captured["runner_kwargs"]["adapter"] is chain_sentinel


@pytest.mark.asyncio
async def test_caption_fallback_models_none_or_empty_still_routes_through_chain(
    tmp_path,
) -> None:
    svc = CaptionService(
        provider_key="doubao",
        provider_config={"model": _ASSIGNED, "api_key": "k", "base_url": "http://h/v1"},
    )
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={"content": '{"prompt_en":"ok","prompt_zh":"zh"}', "raw": {}}
    )

    build_mock = AsyncMock(return_value=MagicMock())

    fake_img = tmp_path / "x.jpg"
    fake_img.write_bytes(b"jpeg")

    for fallback_models in (None, []):
        build_mock.reset_mock()
        with (
            patch(f"{_MOD}.PromptComposer", return_value=composer),
            patch(f"{_MOD}.AgentRunner", return_value=runner),
            patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
            patch(
                f"{_MOD}._encode_image_sync",
                return_value="data:image/jpeg;base64,ZmFrZQ==",
            ),
            patch(
                "app.services.ai.llm.fallback_wiring.build_fallback_llm",
                new=build_mock,
            ),
        ):
            result = await svc.caption(
                file_path=str(fake_img),
                user_id=None,
                resource_id="r1",
                fallback_models=fallback_models,
            )
        build_mock.assert_awaited_once_with(
            primary_model=_ASSIGNED,
            fallback_models=[],
            user_provider_config=svc._provider_config,
            provider_key=svc._provider_key,
            module="caption",
        )
        assert result is not None
        assert result["en"] == "ok"


# ---------------------------------------------------------------------------
# 2: fake-adapter integration test through the REAL build_fallback_llm — only
# resolve_mediahub_model (→ no platform-catalog hit) and the adapter factory
# seam (get_adapter_for_user) are mocked, so LLMFallbackChain +
# LLMRetryMiddleware run for real. A C1-shaped regression (build_fallback_llm
# handing the flat provider_config straight to get_adapter_for_user instead
# of wrapping it under a provider key) would have made get_adapter_for_user's
# ``.get(provider_key, {})`` always find empty credentials — this test would
# have caught that via the assertion on the config it actually receives.
# ---------------------------------------------------------------------------


class _RateLimited(Exception):
    status_code = 429


class _FakeChatAdapter:
    """Minimal OpenAI-chat-completions-shaped adapter stub."""

    def __init__(self, model: str, *, should_fail: bool) -> None:
        self.model = model
        self.should_fail = should_fail

    async def call(self, composed, messages: list[dict]) -> dict:
        if self.should_fail:
            raise _RateLimited("rate limited")
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"prompt_en":"ok caption","prompt_zh":"zh caption"}'
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {},
        }


@pytest.mark.asyncio
async def test_caption_falls_back_on_429_through_real_build_fallback_llm(
    tmp_path,
) -> None:
    primary = _ASSIGNED
    fallback = "doubao-lite"
    svc = CaptionService(
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

    fake_img = tmp_path / "x.jpg"
    fake_img.write_bytes(b"jpeg")

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch.object(fw, "resolve_mediahub_model", AsyncMock(return_value=None)),
        patch.object(
            fw, "get_adapter_for_user", side_effect=_fake_get_adapter_for_user
        ),
        patch("asyncio.sleep", AsyncMock(return_value=None)),
        patch(
            f"{_MOD}._encode_image_sync",
            return_value="data:image/jpeg;base64,ZmFrZQ==",
        ),
    ):
        result = await svc.caption(
            file_path=str(fake_img),
            user_id=None,
            resource_id="r1",
            fallback_models=[fallback],
        )

    assert result is not None
    assert result["en"] == "ok caption"
    assert result["zh"] == "zh caption"
    # Exactly one adapter build per model attempted (primary, then fallback)
    # — retries reuse the same adapter instance, they don't rebuild it.
    assert len(captured_scoped_configs) == 2


# ---------------------------------------------------------------------------
# 3: RunRecorder path — LLM-class exceptions PROPAGATE (no longer swallowed
# to None); AgentPausedError / error-dict stay None; bare path untouched.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caption_recorder_path_propagates_all_models_failed(tmp_path) -> None:
    svc = CaptionService(provider_config={"model": _ASSIGNED})
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        side_effect=AllModelsFailed("primary+fallbacks exhausted")
    )

    fake_img = tmp_path / "x.jpg"
    fake_img.write_bytes(b"jpeg")

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            f"{_MOD}._encode_image_sync",
            return_value="data:image/jpeg;base64,ZmFrZQ==",
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(f"{_MOD}.RunRecorder", return_value=_make_recorder_cm()),
    ):
        with pytest.raises(AllModelsFailed):
            await svc.caption(
                file_path=str(fake_img),
                user_id=_USER,
                resource_id="r1",
            )


@pytest.mark.asyncio
async def test_caption_recorder_path_propagates_llm_call_error(tmp_path) -> None:
    svc = CaptionService(provider_config={"model": _ASSIGNED})
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(side_effect=LLMCallError("non-retryable: 401"))

    fake_img = tmp_path / "x.jpg"
    fake_img.write_bytes(b"jpeg")

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            f"{_MOD}._encode_image_sync",
            return_value="data:image/jpeg;base64,ZmFrZQ==",
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(f"{_MOD}.RunRecorder", return_value=_make_recorder_cm()),
    ):
        with pytest.raises(LLMCallError):
            await svc.caption(
                file_path=str(fake_img),
                user_id=_USER,
                resource_id="r1",
            )


@pytest.mark.asyncio
async def test_caption_recorder_path_agent_paused_returns_none(tmp_path) -> None:
    svc = CaptionService(provider_config={"model": _ASSIGNED})
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(side_effect=AgentPausedError("agent paused"))

    fake_img = tmp_path / "x.jpg"
    fake_img.write_bytes(b"jpeg")

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            f"{_MOD}._encode_image_sync",
            return_value="data:image/jpeg;base64,ZmFrZQ==",
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(f"{_MOD}.RunRecorder", return_value=_make_recorder_cm()),
    ):
        result = await svc.caption(
            file_path=str(fake_img),
            user_id=_USER,
            resource_id="r1",
        )
    assert result is None


@pytest.mark.asyncio
async def test_caption_recorder_path_error_dict_returns_none(tmp_path) -> None:
    svc = CaptionService(provider_config={"model": _ASSIGNED})
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": "", "error": "boom"})

    fake_img = tmp_path / "x.jpg"
    fake_img.write_bytes(b"jpeg")

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            f"{_MOD}._encode_image_sync",
            return_value="data:image/jpeg;base64,ZmFrZQ==",
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(f"{_MOD}.RunRecorder", return_value=_make_recorder_cm()),
    ):
        result = await svc.caption(
            file_path=str(fake_img),
            user_id=_USER,
            resource_id="r1",
        )
    assert result is None


@pytest.mark.asyncio
async def test_caption_bare_path_swallows_exception_untouched(tmp_path) -> None:
    """bare (user_id=None) path keeps its blanket except → None contract —
    only the recorder path's exception handling changed (spec §1-F1)."""
    svc = CaptionService(provider_config={"model": _ASSIGNED})
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(side_effect=RuntimeError("boom"))

    fake_img = tmp_path / "x.jpg"
    fake_img.write_bytes(b"jpeg")

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            f"{_MOD}._encode_image_sync",
            return_value="data:image/jpeg;base64,ZmFrZQ==",
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
    ):
        result = await svc.caption(
            file_path=str(fake_img), user_id=None, resource_id="r1"
        )
    assert result is None


# ---------------------------------------------------------------------------
# 4: DBOS step retry collapsed to 1 — retry + fallback now live entirely in
# LLMFallbackChain; a step-level retry on top would multiply attempts.
# ---------------------------------------------------------------------------


def test_call_caption_step_max_attempts_is_one() -> None:
    import app.workflows.caption_asset as caption_asset_mod

    src = inspect.getsource(caption_asset_mod)
    idx = src.index("async def call_caption")
    before = src[:idx]
    decorator_line = before.strip().splitlines()[-1]
    assert re.search(r"max_attempts\s*=\s*1\b", decorator_line), decorator_line


# ---------------------------------------------------------------------------
# 5: resolve_caption_provider step surfaces fallback_models from cfg — direct
# call to resolve_task_ai_config(task_key="caption"), bypassing the
# resolve_task_provider_config tuple shim (which drops fallback_models).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_caption_provider_returns_fallback_models_from_cfg() -> None:
    import app.workflows.caption_asset as caption_asset_mod

    cfg = ResolvedAIConfig(
        provider_key="doubao",
        provider_config={"api_key": "k", "base_url": "u", "model": "m"},
        model="m",
        agent_slug="caption",
        origin="byok",
        fallback_models=("doubao-lite", "qwen-turbo"),
    )

    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_task_ai_config",
        new=AsyncMock(return_value=cfg),
    ) as resolve_mock:
        result = await caption_asset_mod.resolve_caption_provider("user-1")

    resolve_mock.assert_awaited_once_with("user-1", "caption", "caption")
    assert result["fallback_models"] == ["doubao-lite", "qwen-turbo"]


@pytest.mark.asyncio
async def test_resolve_caption_provider_fallback_models_empty_when_cfg_empty() -> None:
    import app.workflows.caption_asset as caption_asset_mod

    cfg = ResolvedAIConfig(
        provider_key="doubao",
        provider_config={"api_key": "k", "base_url": "u", "model": "m"},
        model="m",
        agent_slug="caption",
        origin="byok",
    )

    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_task_ai_config",
        new=AsyncMock(return_value=cfg),
    ):
        result = await caption_asset_mod.resolve_caption_provider("user-1")

    assert result["fallback_models"] == []


# ---------------------------------------------------------------------------
# 6: both workflows thread the resolved fallback_models into call_caption.
# ---------------------------------------------------------------------------


def _make_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.update_progress = AsyncMock(return_value=None)
    return mgr


@pytest.mark.asyncio
async def test_caption_asset_workflow_threads_fallback_models_to_call_caption(
    tmp_path,
) -> None:
    from app.workflows import caption_asset as m

    local = tmp_path / "materialized.png"
    local.write_bytes(b"png-bytes")

    @asynccontextmanager
    async def _fake_materialize(file_path):
        yield local

    rid = "9000000000000000001"
    resource = {"id": rid, "file_path": "/data/x.png", "file_type": "image"}
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)
    repo.update_resource = AsyncMock(return_value=dict(resource))

    call = AsyncMock(return_value={"en": "a prompt"})

    with (
        patch.object(m, "materialize", _fake_materialize),
        patch.object(
            m,
            "resolve_caption_provider",
            AsyncMock(
                return_value={
                    "provider_key": "qwen",
                    "provider_config": {},
                    "agent_model": "m",
                    "agent_slug": "caption",
                    "fallback_models": ["doubao-lite"],
                }
            ),
        ),
        patch.object(m, "call_caption", call),
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=_make_manager(),
        ),
    ):
        result = await inspect.unwrap(m.caption_asset_workflow)(
            resource_id=rid, user_id=_USER
        )

    assert result["status"] == "ok"
    assert call.await_args.kwargs["fallback_models"] == ["doubao-lite"]


@pytest.mark.asyncio
async def test_caption_slide_workflow_threads_fallback_models_to_call_caption(
    tmp_path,
) -> None:
    from app.workflows import caption_slide as m

    album = tmp_path / "web/douyin/123/slides"
    album.mkdir(parents=True)
    (album / "002.jpg").write_bytes(b"jpeg")

    rid = "9000000000000000001"
    mid = "8000000000000000001"
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(
        return_value={"id": rid, "media_id": mid, "mime_type": "image/jpeg"}
    )
    repo.merge_slide_prompt = AsyncMock(return_value={"en": "a prompt"})

    media_repo = MagicMock()
    media_repo.get_by_id = AsyncMock(return_value={"download_path": "web/douyin/123"})

    call = AsyncMock(return_value={"en": "a prompt"})

    with (
        patch(
            "app.services.media.slide_paths.Utils.get_download_base_path",
            return_value=str(tmp_path),
        ),
        patch.object(
            m,
            "resolve_caption_provider",
            AsyncMock(
                return_value={
                    "provider_key": "qwen",
                    "provider_config": {},
                    "agent_model": "m",
                    "agent_slug": "caption",
                    "fallback_models": ["doubao-lite"],
                }
            ),
        ),
        patch.object(m, "call_caption", call),
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.repositories.media_repository.MediaRepository",
            return_value=media_repo,
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=_make_manager(),
        ),
    ):
        result = await inspect.unwrap(m.caption_slide_workflow)(
            resource_id=rid, user_id=_USER, media_id=mid, slide_name="002.jpg"
        )

    assert result["status"] == "ok"
    assert call.await_args.kwargs["fallback_models"] == ["doubao-lite"]
