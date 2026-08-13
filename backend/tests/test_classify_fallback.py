"""classify 接 fallback 链(spec 2026-08-12-batch-fallback-rollout §1-F2)。

与 caption 版(test_caption_fallback.py)逐字同构,差异仅:
  - module 门禁键是 "classification"(agent slug 是 "classify")。
  - workflow 尾新增 record_ai_error_code(照 test_caption_error_code.py 写法,
    caption 早就有,classify 是本次新加)。

Case groups:
  1. ClassifyService:链替换裸 _build_adapter;fallback_models None/[] 仍走链。
  2. fake-adapter 真链集成测试(照 test_summarize_fallback.py §2b 结构):
     429→fallback 直通,断言 scoped config 形状(C1 回归钉)。
  3. RunRecorder 路径:LLM 类异常(AllModelsFailed/LLMCallError)propagate;
     AgentPausedError / error-dict 维持 None;bare(user_id=None)路径未改动。
  4. call_classify 装饰器 max_attempts == 1。
  5. resolve_classify_provider 直调 resolve_task_ai_config(task_key="classification"),
     返回 dict 带出 fallback_models。
  6. classify_asset workflow 把 resolve 出的 fallback_models 原样透传进
     call_classify。
  7. workflow 尾 record_ai_error_code 被调(新增,caption 已有此纪律但
     classify 之前没有 —— 本次一并补上)。
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
from app.services.ai.classify.classify_service import ClassifyService
from app.services.ai.error_catalog import PROVIDER_AUTH
from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
from app.services.ai.llm.llm_retry_middleware import LLMCallError
from app.services.ai.providers.ai_provider_helpers import ResolvedAIConfig
from app.services.ai.runner.run_recorder import AgentPausedError

_MOD = "app.services.ai.classify.classify_service"
_ASSIGNED = "doubao-seed-1-6-250615"
_USER = "00000000-0000-0000-0000-000000000002"


def _composed(model: str = _ASSIGNED) -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="classify",
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


_CLASSIFY_JSON = '{"dimensions":{"style":[{"en":"anime","zh":"动漫"}]}}'


# ---------------------------------------------------------------------------
# 1: build_fallback_llm wiring (bare path — adapter build is shared code
# before the user_id branch, so user_id=None is the simplest capture point).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_builds_fallback_chain_with_given_models(tmp_path) -> None:
    svc = ClassifyService(
        provider_key="doubao",
        provider_config={"model": _ASSIGNED, "api_key": "k", "base_url": "http://h/v1"},
    )
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": _CLASSIFY_JSON, "raw": {}})

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
        await svc.classify(
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
        module="classification",
    )
    assert captured["runner_kwargs"]["adapter"] is chain_sentinel


@pytest.mark.asyncio
async def test_classify_fallback_models_none_or_empty_still_routes_through_chain(
    tmp_path,
) -> None:
    svc = ClassifyService(
        provider_key="doubao",
        provider_config={"model": _ASSIGNED, "api_key": "k", "base_url": "http://h/v1"},
    )
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": _CLASSIFY_JSON, "raw": {}})

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
            result = await svc.classify(
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
            module="classification",
        )
        assert result is not None
        assert result[0].en == "anime"


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
                    "message": {"content": _CLASSIFY_JSON},
                    "finish_reason": "stop",
                }
            ],
            "usage": {},
        }


@pytest.mark.asyncio
async def test_classify_falls_back_on_429_through_real_build_fallback_llm(
    tmp_path,
) -> None:
    primary = _ASSIGNED
    fallback = "doubao-lite"
    svc = ClassifyService(
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
        result = await svc.classify(
            file_path=str(fake_img),
            user_id=None,
            resource_id="r1",
            fallback_models=[fallback],
        )

    assert result is not None
    assert result[0].en == "anime"
    assert result[0].zh == "动漫"
    # Exactly one adapter build per model attempted (primary, then fallback)
    # — retries reuse the same adapter instance, they don't rebuild it.
    assert len(captured_scoped_configs) == 2


# ---------------------------------------------------------------------------
# 3: RunRecorder path — LLM-class exceptions PROPAGATE (no longer swallowed
# to None); AgentPausedError / error-dict stay None; bare path untouched.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_recorder_path_propagates_all_models_failed(tmp_path) -> None:
    svc = ClassifyService(provider_config={"model": _ASSIGNED})
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
            await svc.classify(
                file_path=str(fake_img),
                user_id=_USER,
                resource_id="r1",
            )


@pytest.mark.asyncio
async def test_classify_recorder_path_propagates_llm_call_error(tmp_path) -> None:
    svc = ClassifyService(provider_config={"model": _ASSIGNED})
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
            await svc.classify(
                file_path=str(fake_img),
                user_id=_USER,
                resource_id="r1",
            )


@pytest.mark.asyncio
async def test_classify_recorder_path_agent_paused_returns_none(tmp_path) -> None:
    svc = ClassifyService(provider_config={"model": _ASSIGNED})
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
        result = await svc.classify(
            file_path=str(fake_img),
            user_id=_USER,
            resource_id="r1",
        )
    assert result is None


@pytest.mark.asyncio
async def test_classify_recorder_path_error_dict_returns_none(tmp_path) -> None:
    svc = ClassifyService(provider_config={"model": _ASSIGNED})
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
        result = await svc.classify(
            file_path=str(fake_img),
            user_id=_USER,
            resource_id="r1",
        )
    assert result is None


@pytest.mark.asyncio
async def test_classify_bare_path_swallows_exception_untouched(tmp_path) -> None:
    """bare (user_id=None) path keeps its blanket except → None contract —
    only the recorder path's exception handling changed (spec §1-F2)."""
    svc = ClassifyService(provider_config={"model": _ASSIGNED})
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
        result = await svc.classify(
            file_path=str(fake_img), user_id=None, resource_id="r1"
        )
    assert result is None


# ---------------------------------------------------------------------------
# 4: DBOS step retry collapsed to 1 — retry + fallback now live entirely in
# LLMFallbackChain; a step-level retry on top would multiply attempts.
# ---------------------------------------------------------------------------


def test_call_classify_step_max_attempts_is_one() -> None:
    import app.workflows.classify_asset as classify_asset_mod

    src = inspect.getsource(classify_asset_mod)
    idx = src.index("async def call_classify")
    before = src[:idx]
    decorator_line = before.strip().splitlines()[-1]
    assert re.search(r"max_attempts\s*=\s*1\b", decorator_line), decorator_line


# ---------------------------------------------------------------------------
# 5: resolve_classify_provider step surfaces fallback_models from cfg — direct
# call to resolve_task_ai_config(task_key="classification"), bypassing the
# resolve_task_provider_config tuple shim (which drops fallback_models).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_classify_provider_returns_fallback_models_from_cfg() -> None:
    import app.workflows.classify_asset as classify_asset_mod

    cfg = ResolvedAIConfig(
        provider_key="doubao",
        provider_config={"api_key": "k", "base_url": "u", "model": "m"},
        model="m",
        agent_slug="classify",
        origin="byok",
        fallback_models=("doubao-lite", "qwen-turbo"),
    )

    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_task_ai_config",
        new=AsyncMock(return_value=cfg),
    ) as resolve_mock:
        result = await classify_asset_mod.resolve_classify_provider("user-1")

    resolve_mock.assert_awaited_once_with("user-1", "classification", "classify")
    assert result["fallback_models"] == ["doubao-lite", "qwen-turbo"]


@pytest.mark.asyncio
async def test_resolve_classify_provider_fallback_models_empty_when_cfg_empty() -> None:
    import app.workflows.classify_asset as classify_asset_mod

    cfg = ResolvedAIConfig(
        provider_key="doubao",
        provider_config={"api_key": "k", "base_url": "u", "model": "m"},
        model="m",
        agent_slug="classify",
        origin="byok",
    )

    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_task_ai_config",
        new=AsyncMock(return_value=cfg),
    ):
        result = await classify_asset_mod.resolve_classify_provider("user-1")

    assert result["fallback_models"] == []


# ---------------------------------------------------------------------------
# 6: classify_asset workflow threads the resolved fallback_models into
# call_classify.
# ---------------------------------------------------------------------------


def _make_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.update_progress = AsyncMock(return_value=None)
    return mgr


@pytest.mark.asyncio
async def test_classify_asset_workflow_threads_fallback_models_to_call_classify(
    tmp_path,
) -> None:
    from app.workflows import classify_asset as m

    local = tmp_path / "materialized.png"
    local.write_bytes(b"png-bytes")

    @asynccontextmanager
    async def _fake_materialize(file_path):
        yield local

    rid = "9000000000000000001"
    resource = {"id": rid, "file_path": "/data/x.png", "file_type": "image"}
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)

    tags_repo = MagicMock()
    tags_repo.get_or_create_group = AsyncMock(return_value={"id": "g1"})
    tags_repo.get_tag_by_name = AsyncMock(return_value={"id": "t1"})
    tags_repo.add_tag_to_resource = AsyncMock(return_value=None)

    call = AsyncMock(
        return_value=[
            {"dimension": "style", "group": "Style", "en": "anime", "zh": "动漫"}
        ]
    )

    with (
        patch.object(m, "materialize", _fake_materialize),
        patch.object(
            m,
            "resolve_classify_provider",
            AsyncMock(
                return_value={
                    "provider_key": "qwen",
                    "provider_config": {},
                    "agent_model": "m",
                    "agent_slug": "classify",
                    "fallback_models": ["doubao-lite"],
                }
            ),
        ),
        patch.object(m, "call_classify", call),
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.repositories.tags_repository.get_tags_repository",
            return_value=tags_repo,
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=_make_manager(),
        ),
    ):
        result = await inspect.unwrap(m.classify_asset_workflow)(
            resource_id=rid, user_id=_USER
        )

    assert result["status"] == "ok"
    assert call.await_args.kwargs["fallback_models"] == ["doubao-lite"]


# ---------------------------------------------------------------------------
# 7: workflow tail records an error_catalog code before re-raising — new for
# classify_asset (caption_asset already had this; classify didn't). Same
# structure as test_caption_error_code.py.
# ---------------------------------------------------------------------------


async def _run_failing_classify(step_error: Exception):
    from app.workflows import classify_asset as m

    manager = MagicMock()
    manager.update_progress = AsyncMock(return_value=None)
    manager.patch_metadata = AsyncMock(return_value=None)
    manager.fail = AsyncMock(return_value=None)

    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(
        return_value={"id": "9000000000000000001", "file_path": "/data/x.png"}
    )

    with (
        patch.object(m, "resolve_classify_provider", AsyncMock(side_effect=step_error)),
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=manager,
        ),
        patch.object(m.DBOS, "workflow_id", "wf-classify-1", create=True),
    ):
        with pytest.raises(Exception) as exc_info:
            await inspect.unwrap(m.classify_asset_workflow)(
                resource_id="9000000000000000001", user_id=_USER
            )
    return exc_info.value, manager


@pytest.mark.asyncio
async def test_classify_provider_401_writes_error_code():
    exc = Exception(
        "Error code: 401 - {'error': {'message': 'Incorrect API key "
        "provided', 'code': 'invalid_api_key'}}"
    )
    raised, manager = await _run_failing_classify(exc)

    assert raised is exc
    manager.patch_metadata.assert_awaited_once_with(
        "wf-classify-1", {"error_code": PROVIDER_AUTH}
    )
    patched_keys = set(manager.patch_metadata.await_args.args[1])
    assert patched_keys == {"error_code"}


@pytest.mark.asyncio
async def test_classify_unclassifiable_failure_writes_no_code():
    raised, manager = await _run_failing_classify(RuntimeError("something novel"))

    assert isinstance(raised, RuntimeError)
    manager.patch_metadata.assert_not_awaited()
