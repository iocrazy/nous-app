"""translate 接 fallback 链（spec 2026-08-12-batch-fallback-rollout §1-F3）。

与 caption/classify 的关键差异：translate 是**同步 API**，没有 DBOS workflow。
所以类型化 provider 异常的落点不是 workflow tail-catch 的 record_ai_error_code，
而是 `resources_ai_router.translate_gen_prompt` 的 catch-all 必须**放行**
AllModelsFailed / LLMCallError，交给 `core/provider_errors.py` 注册的全局
handler 产出 503 provider_rate_limit / 502 provider_auth —— 否则 P1 建起来的
类型化回显会被裸 500 "Translation failed" 整片遮蔽。

分组：
  1  build_fallback_llm 接线（module="translation"、fallback_models 透传）
  2  fake-adapter 真链 429→fallback 直通（照 test_summarize_fallback.py §2b）
  3  RunRecorder 路径的 LLM 类异常上抛 / pause 与 error-dict 维持 None /
     bare 路径 blanket except 未动
  4  router：provider 解析直调 resolve_task_ai_config + catch-all 放行
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

import app.services.ai.llm.fallback_wiring as fw
from app.core.deps import AuthContext, get_auth
from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
from app.services.ai.llm.llm_retry_middleware import LLMCallError
from app.services.ai.providers.ai_provider_helpers import ResolvedAIConfig
from app.services.ai.runner.run_recorder import AgentPausedError
from app.services.ai.translate.translate_service import TranslateService

_MOD = "app.services.ai.translate.translate_service"
_ROUTER = "app.api.resources_ai_router"
_ASSIGNED = "doubao-seed-1-6-250615"


def _composed(model: str = _ASSIGNED) -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="translate",
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
# 1: build_fallback_llm wiring (bare path — the adapter build is shared code
# before the user_id branch, so user_id=None is the simplest capture point).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translate_builds_fallback_chain_with_given_models() -> None:
    svc = TranslateService(
        provider_key="doubao",
        provider_config={"model": _ASSIGNED, "api_key": "k", "base_url": "http://h/v1"},
    )
    composed = _composed()
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": "一只猫", "raw": {}})

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
        out = await svc.translate(
            text="a cat",
            target_lang="zh",
            user_id=None,
            fallback_models=["lite"],
        )

    assert out == "一只猫"
    build_mock.assert_awaited_once_with(
        primary_model=_ASSIGNED,
        fallback_models=["lite"],
        user_provider_config=svc._provider_config,
        provider_key=svc._provider_key,
        module="translation",
    )
    assert captured["runner_kwargs"]["adapter"] is chain_sentinel


@pytest.mark.asyncio
async def test_translate_fallback_models_none_still_routes_through_chain() -> None:
    """fallback_models 不传 → 仍走 build_fallback_llm（空池），调用路径统一。"""
    svc = TranslateService(
        provider_key="doubao",
        provider_config={"model": _ASSIGNED, "api_key": "k", "base_url": "http://h/v1"},
    )
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=_composed())

    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": "一只猫", "raw": {}})

    build_mock = AsyncMock(return_value=MagicMock())

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=build_mock,
        ),
    ):
        await svc.translate(text="a cat", target_lang="zh", user_id=None)

    assert build_mock.await_args.kwargs["fallback_models"] == []
    assert build_mock.await_args.kwargs["module"] == "translation"


# ---------------------------------------------------------------------------
# 2: fake-adapter integration test through the REAL build_fallback_llm — only
# PromptComposer, resolve_mediahub_model (→ no platform-catalog hit), the
# adapter factory seam (get_adapter_for_user) and asyncio.sleep are mocked, so
# LLMFallbackChain + LLMRetryMiddleware run for real. A C1-shaped regression
# (build_fallback_llm handing the flat provider_config straight to
# get_adapter_for_user instead of wrapping it under a provider key) would have
# made get_adapter_for_user's ``.get(provider_key, {})`` always find empty
# credentials — the assertion inside the fake factory catches exactly that.
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
                {"message": {"content": "一只猫"}, "finish_reason": "stop"},
            ],
            "usage": {},
        }


@pytest.mark.asyncio
async def test_translate_falls_back_on_429_through_real_build_fallback_llm() -> None:
    primary = _ASSIGNED
    fallback = "doubao-lite"
    svc = TranslateService(
        provider_key="doubao",
        provider_config={"model": primary, "api_key": "k1", "base_url": "http://h1/v1"},
    )
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=_composed(primary))

    captured_scoped_configs: list[dict] = []

    def _fake_get_adapter_for_user(model, user_provider_config, _settings):
        captured_scoped_configs.append(dict(user_provider_config))
        # C1 regression guard: pre-fix, build_fallback_llm passed the FLAT
        # {"model","api_key","base_url"} config straight through — this
        # assertion fails against that shape (no "doubao" key at all).
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
        out = await svc.translate(
            text="a cat",
            target_lang="zh",
            user_id=None,
            resource_id="r1",
            fallback_models=[fallback],
        )

    assert out == "一只猫"
    # Exactly one adapter build per model attempted (primary, then fallback)
    # — retries reuse the same adapter instance, they don't rebuild it. This
    # distinguishes "really failed over" from "primary retried and won".
    assert len(captured_scoped_configs) == 2


# ---------------------------------------------------------------------------
# 3: RunRecorder path — LLM-class exceptions PROPAGATE (no longer swallowed to
# None); AgentPausedError / error-dict stay None; bare path untouched.
# ---------------------------------------------------------------------------


def _recorder_path_patches(runner: MagicMock, composer: MagicMock):
    return (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(f"{_MOD}.RunRecorder", return_value=_make_recorder_cm()),
    )


@contextmanager
def _recorder_path(runner: MagicMock, composer: MagicMock):
    patches = _recorder_path_patches(runner, composer)
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_translate_recorder_path_propagates_all_models_failed() -> None:
    svc = TranslateService(provider_config={"model": _ASSIGNED})
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=_composed())
    runner = MagicMock()
    runner.run_turn = AsyncMock(side_effect=AllModelsFailed("exhausted"))

    with _recorder_path(runner, composer):
        with pytest.raises(AllModelsFailed):
            await svc.translate(
                text="a cat", target_lang="zh", user_id=UUID(int=7), resource_id="r1"
            )


@pytest.mark.asyncio
async def test_translate_recorder_path_propagates_llm_call_error() -> None:
    svc = TranslateService(provider_config={"model": _ASSIGNED})
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=_composed())
    runner = MagicMock()
    runner.run_turn = AsyncMock(side_effect=LLMCallError("401 from provider"))

    with _recorder_path(runner, composer):
        with pytest.raises(LLMCallError):
            await svc.translate(
                text="a cat", target_lang="zh", user_id=UUID(int=7), resource_id="r1"
            )


@pytest.mark.asyncio
async def test_translate_recorder_path_agent_paused_returns_none() -> None:
    """Pause 是前置状态而非 provider 故障，维持吞成 None 的既有口径。"""
    svc = TranslateService(provider_config={"model": _ASSIGNED})
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=_composed())
    runner = MagicMock()
    runner.run_turn = AsyncMock(side_effect=AgentPausedError("paused"))

    with _recorder_path(runner, composer):
        out = await svc.translate(
            text="a cat", target_lang="zh", user_id=UUID(int=7), resource_id="r1"
        )
    assert out is None


@pytest.mark.asyncio
async def test_translate_recorder_path_error_dict_returns_none() -> None:
    svc = TranslateService(provider_config={"model": _ASSIGNED})
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=_composed())
    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"error": "boom", "content": ""})

    with _recorder_path(runner, composer):
        out = await svc.translate(
            text="a cat", target_lang="zh", user_id=UUID(int=7), resource_id="r1"
        )
    assert out is None


@pytest.mark.asyncio
async def test_translate_bare_path_swallows_exception_untouched() -> None:
    """user_id=None 分支的 blanket except 不在本次改动范围（照 caption/classify）。"""
    svc = TranslateService(provider_config={"model": _ASSIGNED})
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=_composed())
    runner = MagicMock()
    runner.run_turn = AsyncMock(side_effect=AllModelsFailed("exhausted"))

    with (
        patch(f"{_MOD}.PromptComposer", return_value=composer),
        patch(f"{_MOD}.AgentRunner", return_value=runner),
        patch(f"{_MOD}.SkillToolService", return_value=MagicMock()),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
    ):
        out = await svc.translate(text="a cat", target_lang="zh", user_id=None)
    assert out is None


# ---------------------------------------------------------------------------
# 4: router — provider 解析直调 resolve_task_ai_config（带出 fallback_models）,
# 且 catch-all 放行 AllModelsFailed / LLMCallError 到全局类型化错误面。
# ---------------------------------------------------------------------------

_URL = "/api/v1/resources/77/gen-prompt/translate"


def _resolved(fallback_models: tuple[str, ...] = ("doubao-lite",)) -> ResolvedAIConfig:
    return ResolvedAIConfig(
        provider_key="doubao",
        provider_config={"model": _ASSIGNED, "api_key": "k", "base_url": "http://h/v1"},
        model=_ASSIGNED,
        agent_slug="translate",
        origin="byok",
        fallback_models=fallback_models,
    )


@pytest.fixture
def client():
    """TestClient on the REAL app (so the production exception handlers, not a
    hand-rolled minimal app, decide the status code) with auth overridden."""
    from app.main import app

    app.dependency_overrides[get_auth] = lambda: AuthContext(
        user_id="user-a", auth_type="jwt"
    )
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_auth, None)


@contextmanager
def _router_env(
    translate_results: Any,
    resolved: ResolvedAIConfig | None = None,
    resource: dict | None = None,
):
    """Patch the endpoint's collaborators: resource read, access check,
    provider resolution and TranslateService. ``translate_results`` is either
    one value/exception for every call, or a list consumed one per call.
    Yields the captured repo + TranslateService ctor / translate() kwargs."""
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(
        return_value=resource or {"id": "77", "gen_prompt": "a cat"}
    )
    repo.update_resource = AsyncMock(return_value={"gen_prompt_zh": "一只猫"})

    captured: dict = {"repo": repo}
    pending = list(translate_results) if isinstance(translate_results, list) else None

    class _FakeService:
        def __init__(self, **kwargs):
            captured["ctor"] = kwargs

        async def translate(self, **kwargs):
            captured.setdefault("calls", []).append(kwargs)
            outcome = pending.pop(0) if pending is not None else translate_results
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    resolve_mock = AsyncMock(return_value=resolved or _resolved())
    captured["resolve_mock"] = resolve_mock

    with (
        patch(f"{_ROUTER}.ResourcesRepository", return_value=repo),
        patch(
            "app.api.media_permissions.check_media_access",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_task_ai_config",
            new=resolve_mock,
        ),
        patch("app.services.ai.translate.TranslateService", new=_FakeService),
    ):
        yield captured


def test_router_resolves_translation_config_and_threads_fallback_models(client) -> None:
    with _router_env("一只猫") as captured:
        resp = client.post(_URL, json={"target_lang": "zh"})

    assert resp.status_code == 200, resp.text
    captured["resolve_mock"].assert_awaited_once_with(
        "user-a", "translation", "translate"
    )
    assert captured["ctor"]["provider_key"] == "doubao"
    assert captured["ctor"]["agent_slug"] == "translate"
    # fallback_models must survive the resolve → service hop; the tuple shim
    # resolve_translate_provider_config drops it, which is why the endpoint
    # calls resolve_task_ai_config directly (spec §1-F3).
    assert captured["calls"][0]["fallback_models"] == ["doubao-lite"]


def test_router_lets_all_models_failed_reach_the_typed_error_surface(client) -> None:
    """catch-all 不得把 AllModelsFailed 吞成裸 500 —— 429 应产出 503
    provider_rate_limit + Retry-After（core/provider_errors.py 的映射）。"""
    request = httpx.Request("POST", "https://ark.example/api")
    inner = httpx.HTTPStatusError(
        "429", request=request, response=httpx.Response(429, request=request)
    )
    exc = AllModelsFailed("primary + 1 fallback(s) exhausted")
    exc.__cause__ = inner

    with _router_env(exc):
        resp = client.post(_URL, json={"target_lang": "zh"})

    assert resp.status_code == 503, resp.text
    assert resp.json()["code"] == "provider_rate_limit"
    assert resp.headers.get("retry-after") == "60"


def test_router_lets_llm_call_error_reach_the_typed_error_surface(client) -> None:
    request = httpx.Request("POST", "https://ark.example/api")
    inner = httpx.HTTPStatusError(
        "401", request=request, response=httpx.Response(401, request=request)
    )
    exc = LLMCallError("auth failed")
    exc.__cause__ = inner

    with _router_env(exc):
        resp = client.post(_URL, json={"target_lang": "zh"})

    assert resp.status_code == 502, resp.text
    assert resp.json()["code"] == "provider_auth"


def test_router_still_maps_non_provider_failures_to_500(client) -> None:
    """放行只针对两类 provider 异常，其余仍走原来的 500 口径。"""
    with _router_env(RuntimeError("boom")):
        resp = client.post(_URL, json={"target_lang": "zh"})

    # 5xx HTTPException bodies are masked by core/exceptions.py (detail only
    # reaches the logs), so the code — not the text — is the contract here.
    assert resp.status_code == 500
    assert resp.json()["code"] == "http_500"


def test_router_aborts_the_whole_update_when_a_later_field_fails(client) -> None:
    """两段 prompt、第二段 429：整个请求以 503 结束，且**不落任何部分更新**。

    这是异常上抛带来的口径变化（改前第二段被吞成 None，第一段照样写库）。
    取上抛：源字段从不被修改、端点可重跑，一次干净的失败比一半成功一半沉默
    更可诊断——「触发路径必须类型化失败回显」。
    """
    request = httpx.Request("POST", "https://ark.example/api")
    exc = AllModelsFailed("exhausted")
    exc.__cause__ = httpx.HTTPStatusError(
        "429", request=request, response=httpx.Response(429, request=request)
    )

    with _router_env(
        ["一只猫", exc],
        resource={"id": "77", "gen_prompt": "a cat", "gen_prompt_negative": "lowres"},
    ) as captured:
        resp = client.post(_URL, json={"target_lang": "zh"})

    assert resp.status_code == 503
    assert len(captured["calls"]) == 2
    captured["repo"].update_resource.assert_not_awaited()


def test_router_still_502s_when_translation_produced_nothing(client) -> None:
    """None（pause / 空产出）不是 provider 异常，维持既有 502 引导文案。"""
    with _router_env(None):
        resp = client.post(_URL, json={"target_lang": "zh"})

    assert resp.status_code == 502
    assert resp.json()["code"] == "http_502"
