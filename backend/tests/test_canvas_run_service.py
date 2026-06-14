"""Tests for the smart-canvas prompt-run service (Phase 2 Day 6-8)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from app.services.canvas.canvas_run_service import (
    DEFAULT_MODEL,
    CanvasRunService,
    _resolve_model,
)


class FakeAdapter:
    """Mimics the AIAdapter.call contract: returns an OpenAI-compatible
    response dict."""

    def __init__(self, *, reply: str = "ok", raise_with: Exception | None = None):
        self.reply = reply
        self.raise_with = raise_with
        self.calls: List[Dict[str, Any]] = []

    async def call(self, composed, messages):
        self.calls.append({"composed": composed, "messages": messages})
        if self.raise_with:
            raise self.raise_with
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": self.reply},
                    "finish_reason": "stop",
                }
            ]
        }


def make_service(adapter: FakeAdapter) -> CanvasRunService:
    svc = CanvasRunService(settings=SimpleNamespace())
    svc._get_adapter = AsyncMock(return_value=adapter)  # type: ignore[assignment]
    return svc


# ============================================================
# _resolve_model
# ============================================================


class TestResolveModel:
    def test_none_falls_back_to_default(self):
        assert _resolve_model(None) == DEFAULT_MODEL

    def test_empty_string_falls_back_to_default(self):
        assert _resolve_model("") == DEFAULT_MODEL

    def test_bare_model_id_passes_through(self):
        assert _resolve_model("claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_slash_form_strips_provider_prefix(self):
        assert _resolve_model("qwen/qwen-plus") == "qwen-plus"
        assert _resolve_model("anthropic/claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_slash_with_empty_suffix_falls_back_to_default(self):
        assert _resolve_model("qwen/") == DEFAULT_MODEL


# ============================================================
# run_prompt
# ============================================================


class TestRunPrompt:
    @pytest.mark.asyncio
    async def test_happy_path_returns_text(self):
        adapter = FakeAdapter(reply="rendered output")
        svc = make_service(adapter)
        result = await svc.run_prompt(body="describe a robot")
        assert result.ok is True
        assert result.text == "rendered output"
        assert result.error is None
        # The adapter was handed one user message + composed prompt.
        assert adapter.calls[0]["messages"] == [
            {"role": "user", "content": "describe a robot"}
        ]

    @pytest.mark.asyncio
    async def test_empty_body_rejected_without_calling_adapter(self):
        adapter = FakeAdapter()
        svc = make_service(adapter)
        result = await svc.run_prompt(body="   ")
        assert result.ok is False
        assert "empty" in (result.error or "").lower()
        assert adapter.calls == []

    @pytest.mark.asyncio
    async def test_adapter_exception_returns_in_band_failure(self):
        adapter = FakeAdapter(raise_with=RuntimeError("rate limited"))
        svc = make_service(adapter)
        result = await svc.run_prompt(body="anything")
        assert result.ok is False
        assert result.text == ""
        assert "rate limited" in (result.error or "")

    @pytest.mark.asyncio
    async def test_missing_choices_returns_empty_text_but_ok_true(self):
        """Malformed adapter response → text='' but ok stays true
        because the adapter call DID succeed (don't double-classify)."""
        adapter = FakeAdapter()

        async def call_returning_empty(composed, messages):
            return {}

        adapter.call = call_returning_empty  # type: ignore[method-assign]
        svc = make_service(adapter)
        result = await svc.run_prompt(body="hi")
        assert result.ok is True
        assert result.text == ""

    @pytest.mark.asyncio
    async def test_provider_slug_picks_model(self):
        adapter = FakeAdapter()
        svc = make_service(adapter)
        svc._get_adapter = AsyncMock(return_value=adapter)  # type: ignore[assignment]
        await svc.run_prompt(body="hi", provider_slug="anthropic/claude-sonnet-4-6")
        svc._get_adapter.assert_called_once_with("claude-sonnet-4-6")

    @pytest.mark.asyncio
    async def test_no_provider_slug_uses_default_model(self):
        adapter = FakeAdapter()
        svc = make_service(adapter)
        svc._get_adapter = AsyncMock(return_value=adapter)  # type: ignore[assignment]
        await svc.run_prompt(body="hi")
        svc._get_adapter.assert_called_once_with(DEFAULT_MODEL)

    @pytest.mark.asyncio
    async def test_agent_id_is_appended_to_system_message(self):
        adapter = FakeAdapter()
        svc = make_service(adapter)
        await svc.run_prompt(body="hi", agent_id="abc-123")
        composed = adapter.calls[0]["composed"]
        assert "Acting under agent abc-123" in composed.system_message


class TestNousProviderRouting:
    @pytest.mark.asyncio
    async def test_nous_slash_workflow_routes_via_nous_runner(self, monkeypatch):
        captured = {}

        async def fake_run_nous_workflow(
            *, settings, workflow_slug, prompt, agent_id=None, **_
        ):
            captured["workflow_slug"] = workflow_slug
            captured["prompt"] = prompt
            captured["agent_id"] = agent_id
            from app.schemas.canvas_run import CanvasPromptRunResult

            return CanvasPromptRunResult(ok=True, text="from nous", error=None)

        monkeypatch.setattr(
            "app.services.canvas.nous_center_runner.run_nous_workflow",
            fake_run_nous_workflow,
        )

        adapter = FakeAdapter()
        svc = make_service(adapter)
        result = await svc.run_prompt(
            body="story please",
            provider_slug="nous/storyboard",
        )
        assert result.ok is True
        assert result.text == "from nous"
        assert captured["workflow_slug"] == "storyboard"
        assert captured["prompt"] == "story please"
        # And the LLM adapter was NOT used.
        assert adapter.calls == []

    @pytest.mark.asyncio
    async def test_nous_path_raises_deadline_for_slow_workflows(self, monkeypatch):
        # Everything routed to nous is a workflow node (comfy/image/video) that
        # can run minutes — the nous path must lift the poll ceiling well above
        # the default chat deadline (Phase 5a Lane C).
        captured = {}

        async def fake_run_nous_workflow(
            *,
            settings,
            workflow_slug,
            prompt,
            agent_id=None,
            max_wait_s_override=None,
            **_,
        ):
            captured["max_wait_s_override"] = max_wait_s_override
            from app.schemas.canvas_run import CanvasPromptRunResult

            return CanvasPromptRunResult(ok=True, text="ok", error=None)

        monkeypatch.setattr(
            "app.services.canvas.nous_center_runner.run_nous_workflow",
            fake_run_nous_workflow,
        )
        svc = make_service(FakeAdapter())
        await svc.run_prompt(body="x", provider_slug="nous/comfy-render")
        assert captured["max_wait_s_override"] is not None
        assert captured["max_wait_s_override"] >= 300.0

    @pytest.mark.asyncio
    async def test_nous_workflow_ceiling_setting_overrides_default(self, monkeypatch):
        captured = {}

        async def fake_run_nous_workflow(*, max_wait_s_override=None, **_):
            captured["override"] = max_wait_s_override
            from app.schemas.canvas_run import CanvasPromptRunResult

            return CanvasPromptRunResult(ok=True, text="ok", error=None)

        monkeypatch.setattr(
            "app.services.canvas.nous_center_runner.run_nous_workflow",
            fake_run_nous_workflow,
        )
        svc = CanvasRunService(
            settings=SimpleNamespace(NOUS_CENTER_MAX_WAIT_S_WORKFLOW=900.0)
        )
        svc._get_adapter = AsyncMock(return_value=FakeAdapter())  # type: ignore[assignment]
        await svc.run_prompt(body="x", provider_slug="nous/comfy-render")
        assert captured["override"] == 900.0

    @pytest.mark.asyncio
    async def test_nous_slash_missing_workflow_returns_error(self):
        adapter = FakeAdapter()
        svc = make_service(adapter)
        result = await svc.run_prompt(body="x", provider_slug="nous/")
        assert result.ok is False
        assert "missing workflow" in (result.error or "")

    @pytest.mark.asyncio
    async def test_nous_unconfigured_falls_through_as_in_band_error(self, monkeypatch):
        from app.services.canvas.nous_center_runner import NousCenterNotConfigured

        async def raises(**_kwargs):
            raise NousCenterNotConfigured("not configured")

        monkeypatch.setattr(
            "app.services.canvas.nous_center_runner.run_nous_workflow", raises
        )

        adapter = FakeAdapter()
        svc = make_service(adapter)
        result = await svc.run_prompt(body="x", provider_slug="nous/anything")
        assert result.ok is False
        assert "not configured" in (result.error or "")


class TestRunClassicNode:
    """ClassicMode node → provider_slug dispatch (Phase 5a C2). A comfy node
    resolves to ``nous/<workflow_slug>`` from its data and reuses the existing
    nous/ route; an llm node resolves to its model and reuses the adapter
    path; an unknown type fails in-band without dispatching."""

    @pytest.mark.asyncio
    async def test_comfy_node_routes_via_nous_with_data_workflow(self, monkeypatch):
        captured = {}

        async def fake_run_nous_workflow(
            *, settings, workflow_slug, prompt, agent_id=None, **_
        ):
            captured["workflow_slug"] = workflow_slug
            captured["prompt"] = prompt
            from app.schemas.canvas_run import CanvasPromptRunResult

            return CanvasPromptRunResult(ok=True, text="rendered", error=None)

        monkeypatch.setattr(
            "app.services.canvas.nous_center_runner.run_nous_workflow",
            fake_run_nous_workflow,
        )

        adapter = FakeAdapter()
        svc = make_service(adapter)
        result = await svc.run_classic_node(
            node_type="comfy",
            node={"data": {"workflow_slug": "render-xl"}},
            body="a robot",
        )
        assert result.ok is True
        assert result.text == "rendered"
        # The workflow slug came from the node data, not a hardcode.
        assert captured["workflow_slug"] == "render-xl"
        assert captured["prompt"] == "a robot"
        # And the bare LLM adapter was NOT used.
        assert adapter.calls == []

    @pytest.mark.asyncio
    async def test_llm_node_routes_via_adapter_with_resolved_model(self):
        adapter = FakeAdapter(reply="hi there")
        svc = make_service(adapter)
        svc._get_adapter = AsyncMock(return_value=adapter)  # type: ignore[assignment]
        result = await svc.run_classic_node(
            node_type="llm",
            node={"data": {"provider_slug": "anthropic/claude-sonnet-4-6"}},
            body="say hi",
        )
        assert result.ok is True
        assert result.text == "hi there"
        svc._get_adapter.assert_called_once_with("claude-sonnet-4-6")

    @pytest.mark.asyncio
    async def test_unknown_node_type_fails_in_band_without_dispatch(self):
        adapter = FakeAdapter()
        svc = make_service(adapter)
        result = await svc.run_classic_node(node_type="frobnicate", node={}, body="x")
        assert result.ok is False
        assert "frobnicate" in (result.error or "")
        # No silent route to a wrong provider.
        assert adapter.calls == []


class TestRunClassicImageGen:
    """image_gen op (Phase 5a path B): a classic image_gen node calls
    StoryboardAIService.generate_image directly (NOT a provider_slug) and
    normalises the dataclass-dict result into ok/text/result.image_url."""

    @staticmethod
    def _service_with_image(image_service):
        svc = make_service(FakeAdapter())
        svc._storyboard_ai_service = lambda: image_service  # type: ignore[assignment]
        return svc

    @pytest.mark.asyncio
    async def test_image_gen_calls_generate_image_with_node_data_params(self):
        gen = AsyncMock(
            return_value={
                "image_url": "https://cdn/out.png",
                "width": 1024,
                "height": 576,
                "provider": "doubao",
                "model": "seedream",
            }
        )
        image_service = SimpleNamespace(generate_image=gen)
        svc = self._service_with_image(image_service)

        result = await svc.run_classic_node(
            node_type="image_gen",
            node={
                "data": {
                    "prompt": "a neon city",
                    "model": "seedream",
                    "provider_name": "doubao",
                    "aspect_ratio": "16:9",
                    "reference_image_url": "https://cdn/ref.png",
                    "character_ids": ["c1", "c2"],
                }
            },
            body="ignored upstream body",
            node_id="node-42",
            project_id="proj-7",
        )

        assert result.ok is True
        assert result.result is not None
        assert result.result["image_url"] == "https://cdn/out.png"
        # text mirrors the url so plain-text cascade consumers still get content.
        assert result.text == "https://cdn/out.png"
        assert result.error is None

        gen.assert_awaited_once()
        kwargs = gen.await_args.kwargs
        assert kwargs["prompt"] == "a neon city"  # from node data, not body
        assert kwargs["model"] == "seedream"
        assert kwargs["provider_name"] == "doubao"
        assert kwargs["aspect_ratio"] == "16:9"
        assert kwargs["reference_image_url"] == "https://cdn/ref.png"
        assert kwargs["character_ids"] == ["c1", "c2"]
        assert kwargs["node_id"] == "node-42"
        assert kwargs["project_id"] == "proj-7"

    @pytest.mark.asyncio
    async def test_image_gen_prompt_falls_back_to_body(self):
        gen = AsyncMock(return_value={"image_url": "https://cdn/x.png"})
        svc = self._service_with_image(SimpleNamespace(generate_image=gen))

        result = await svc.run_classic_node(
            node_type="image_gen",
            node={"data": {"provider_name": "doubao", "model": "m"}},
            body="prompt from upstream",
        )
        assert result.ok is True
        assert gen.await_args.kwargs["prompt"] == "prompt from upstream"
        # default aspect ratio applied when node data omits it
        assert gen.await_args.kwargs["aspect_ratio"] == "16:9"

    @pytest.mark.asyncio
    async def test_image_gen_missing_prompt_fails_without_calling_service(self):
        gen = AsyncMock()
        svc = self._service_with_image(SimpleNamespace(generate_image=gen))

        result = await svc.run_classic_node(
            node_type="image_gen",
            node={"data": {"provider_name": "doubao", "model": "m"}},
            body="   ",
        )
        assert result.ok is False
        assert "prompt" in (result.error or "").lower()
        gen.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_image_gen_provider_raises_returns_in_band_error(self):
        gen = AsyncMock(side_effect=RuntimeError("provider exploded"))
        svc = self._service_with_image(SimpleNamespace(generate_image=gen))

        result = await svc.run_classic_node(
            node_type="image_gen",
            node={"data": {"prompt": "x", "provider_name": "doubao", "model": "m"}},
            body="x",
        )
        assert result.ok is False
        assert result.text == ""
        assert "provider exploded" in (result.error or "")
        assert result.result is None

    @pytest.mark.asyncio
    async def test_image_gen_empty_url_result_fails_in_band(self):
        gen = AsyncMock(return_value={"image_url": "", "width": 10})
        svc = self._service_with_image(SimpleNamespace(generate_image=gen))

        result = await svc.run_classic_node(
            node_type="image_gen",
            node={"data": {"prompt": "x", "provider_name": "doubao", "model": "m"}},
            body="x",
        )
        assert result.ok is False
        assert "image_url" in (result.error or "")


class TestRunClassicVideoGen:
    """video_gen op (Phase 5a path B): a classic video_gen node calls
    StoryboardAIService.generate_video directly (NOT a provider_slug, uses the
    distinct video provider registry) and normalises the dataclass-dict result
    into ok/text/result.video_url. The source image comes from the node data
    (in a real graph it arrives from an upstream image node)."""

    @staticmethod
    def _service_with_video(video_service):
        svc = make_service(FakeAdapter())
        svc._storyboard_ai_service = lambda: video_service  # type: ignore[assignment]
        return svc

    @pytest.mark.asyncio
    async def test_video_gen_calls_generate_video_with_node_data_params(self):
        gen = AsyncMock(
            return_value={
                "video_url": "https://cdn/out.mp4",
                "video_path": "/tmp/out.mp4",
                "duration_seconds": 5.0,
                "width": 1024,
                "height": 576,
                "thumbnail_url": "https://cdn/thumb.png",
                "provider": "doubao",
                "model": "seedance",
            }
        )
        video_service = SimpleNamespace(generate_video=gen)
        svc = self._service_with_video(video_service)

        result = await svc.run_classic_node(
            node_type="video_gen",
            node={
                "data": {
                    "source_image_url": "https://cdn/src.png",
                    "prompt": "slow pan over the city",
                    "model": "seedance",
                    "provider_name": "doubao",
                    "duration_seconds": 8.0,
                    "motion_intensity": "high",
                }
            },
            body="ignored upstream body",
            node_id="node-42",
            project_id="proj-7",
        )

        assert result.ok is True
        assert result.result is not None
        assert result.result["video_url"] == "https://cdn/out.mp4"
        assert result.result["thumbnail_url"] == "https://cdn/thumb.png"
        # text mirrors the url so plain-text cascade consumers still get content.
        assert result.text == "https://cdn/out.mp4"
        assert result.error is None

        gen.assert_awaited_once()
        kwargs = gen.await_args.kwargs
        assert kwargs["source_image_url"] == "https://cdn/src.png"
        assert kwargs["prompt"] == "slow pan over the city"
        assert kwargs["model"] == "seedance"
        assert kwargs["provider_name"] == "doubao"
        assert kwargs["duration_seconds"] == 8.0
        assert kwargs["motion_intensity"] == "high"
        assert kwargs["node_id"] == "node-42"
        assert kwargs["project_id"] == "proj-7"

    @pytest.mark.asyncio
    async def test_video_gen_applies_param_defaults(self):
        gen = AsyncMock(return_value={"video_url": "https://cdn/x.mp4"})
        svc = self._service_with_video(SimpleNamespace(generate_video=gen))

        result = await svc.run_classic_node(
            node_type="video_gen",
            node={
                "data": {
                    "source_image_url": "https://cdn/src.png",
                    "provider_name": "doubao",
                    "model": "m",
                }
            },
            body="",
        )
        assert result.ok is True
        kwargs = gen.await_args.kwargs
        assert kwargs["duration_seconds"] == 5.0
        assert kwargs["motion_intensity"] == "medium"
        assert kwargs["prompt"] == ""

    @pytest.mark.asyncio
    async def test_video_gen_reads_camel_case_source_image(self):
        gen = AsyncMock(return_value={"video_url": "https://cdn/x.mp4"})
        svc = self._service_with_video(SimpleNamespace(generate_video=gen))

        result = await svc.run_classic_node(
            node_type="video_gen",
            node={
                "data": {
                    "sourceImageUrl": "https://cdn/camel.png",
                    "providerName": "doubao",
                    "durationSeconds": 6.0,
                    "motionIntensity": "low",
                    "prompt": "motion prompt",
                }
            },
            body="ignored upstream body",
        )
        assert result.ok is True
        kwargs = gen.await_args.kwargs
        assert kwargs["source_image_url"] == "https://cdn/camel.png"
        assert kwargs["provider_name"] == "doubao"
        assert kwargs["duration_seconds"] == 6.0
        assert kwargs["motion_intensity"] == "low"
        assert kwargs["prompt"] == "motion prompt"

    @pytest.mark.asyncio
    async def test_video_gen_missing_source_image_fails_without_calling_service(self):
        gen = AsyncMock()
        svc = self._service_with_video(SimpleNamespace(generate_video=gen))

        result = await svc.run_classic_node(
            node_type="video_gen",
            node={"data": {"prompt": "x", "provider_name": "doubao", "model": "m"}},
            body="x",
        )
        assert result.ok is False
        assert "source image" in (result.error or "").lower()
        gen.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_video_gen_provider_raises_returns_in_band_error(self):
        gen = AsyncMock(side_effect=RuntimeError("provider exploded"))
        svc = self._service_with_video(SimpleNamespace(generate_video=gen))

        result = await svc.run_classic_node(
            node_type="video_gen",
            node={
                "data": {
                    "source_image_url": "https://cdn/src.png",
                    "provider_name": "doubao",
                    "model": "m",
                }
            },
            body="x",
        )
        assert result.ok is False
        assert result.text == ""
        assert "provider exploded" in (result.error or "")
        assert "video generation failed" in (result.error or "").lower()
        assert result.result is None

    @pytest.mark.asyncio
    async def test_video_gen_empty_url_result_fails_in_band(self):
        gen = AsyncMock(return_value={"video_url": "", "width": 10})
        svc = self._service_with_video(SimpleNamespace(generate_video=gen))

        result = await svc.run_classic_node(
            node_type="video_gen",
            node={
                "data": {
                    "source_image_url": "https://cdn/src.png",
                    "provider_name": "doubao",
                    "model": "m",
                }
            },
            body="x",
        )
        assert result.ok is False
        assert "video_url" in (result.error or "")
