"""Request-capture guards: user/admin-assigned model must reach the wire.

The "last-mile model drop" bug class (whisper-1, visual-analysis hardcoded
slug, script-AI six-layer) keeps recurring: a resolver correctly picks the
user's / admin's assigned model, then a downstream layer hardcodes or
overrides it just before the LLM call. These tests pin the contract at the
last controllable seam — the model handed to ``_build_adapter`` (the wire
model) / to ``resolve_db_adapter`` — so a regression fails loudly.

Naming convention (grep-able, copy-able): ``test_*_honors_assigned_model``.
Add one per user/admin-assignable task type. Each asserts the request carries
the assigned model, never a composer/agent-row/hardcoded default.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.summarize.summarize_service import SummarizeService
from app.services.ai.visual.visual_analysis_service import VisualAnalysisService
from app.services.chat import conversation_memory_service as conv_mem

_ASSIGNED = "doubao-seed-1-6-250615"


def _composed(model: str, *, agent_slug: str = "analyze") -> ComposedSystemPrompt:
    """A REAL ``ComposedSystemPrompt``, not a MagicMock.

    The visual cases below used to fake it with a MagicMock. Since 2026-08-21
    the service pins the resolved model onto ``composed`` via ``model_copy``,
    which a MagicMock cannot carry: it answers with another MagicMock whose
    ``.model`` is itself a MagicMock — truthy, but equal to no string, so the
    dialed-model assertion fails every time. The real pydantic model restores
    the copy semantics AND keeps the assertion falsifiable.
    """
    return ComposedSystemPrompt(
        agent_id=UUID("00000000-0000-0000-0000-000000000001"),
        agent_slug=agent_slug,
        model=model,
        temperature=0.0,
        max_tokens=512,
        system_message="x",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="x",
    )


@pytest.mark.asyncio
async def test_summarize_honors_assigned_model() -> None:
    """Regression: the RESOLVED model (provider_config["model"] -> self.model)
    must reach the wire, not the composed agent row's own value. Since the
    2026-08-20 收口 both come from the same agent, but they still differ
    whenever the resolver mapped the row's catalog name down to the provider's
    ``actual_model`` (or governance / a ``nous:`` pick bypassed the row) — and
    only the resolved one matches the api_key/base_url alongside it. Pre-fix,
    ``composed.model or self.model`` let the row value win and silently dropped
    the resolved model; "qwen-max" below is the composer's hardcoded fallback,
    the shape that made the drop visible.

    Post-fallback-chain wiring (spec §3): the adapter is now built by
    ``build_fallback_llm`` rather than ``svc._build_adapter`` directly — the
    seam this test pins moves to ``build_fallback_llm``'s ``primary_model``
    kwarg, but the guarantee (assigned model reaches the wire, not the
    composer's "qwen-max" default) is unchanged.
    """
    svc = SummarizeService(
        provider_key="doubao",
        provider_config={
            "model": _ASSIGNED,
            "api_key": "k",
            "base_url": "http://host/v1",
        },
    )

    # Real ComposedSystemPrompt (not a MagicMock) so ``.model_copy`` behaves.
    # model="qwen-max" simulates the composer's agent-row fallback — the value
    # that must NOT win.
    composed = ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="summarize",
        model="qwen-max",
        temperature=0.0,
        max_tokens=512,
        system_message="x",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="x",
    )
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={
            "content": '{"summary":"s","key_points":[],"topics":[]}',
            "raw": {},
        }
    )

    captured: dict = {}

    async def _capture_build(*, primary_model, **_kw):
        captured["primary_model"] = primary_model
        return MagicMock()

    with (
        patch(
            "app.services.ai.summarize.summarize_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.summarize.summarize_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.summarize.summarize_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            side_effect=_capture_build,
        ),
    ):
        # user_id=None → bare path (no RunRecorder), cleanest capture point.
        await svc.summarize(
            transcript="hello world", user_id=None, parsed_media_id=1, title="T"
        )

    # The fallback chain was built for the user's assigned model, not "qwen-max".
    assert captured["primary_model"] == _ASSIGNED
    # And the composed handed to the runner carries the assigned wire model.
    ran_composed = runner.run_turn.await_args.args[0]
    assert ran_composed.model == _ASSIGNED


@pytest.mark.asyncio
async def test_summarize_composes_the_agent_it_was_given() -> None:
    """The prompt agent must be the one the resolver took the model FROM
    (#622/#623). ``load_summary_inputs`` threads ``cfg.agent_slug`` in; a
    user who assigned a custom summarization agent must get that agent's
    prompt, not the built-in ``summarize`` preset."""
    svc = SummarizeService(
        provider_key="qwen",
        provider_config={"model": "qwen-max", "api_key": "k"},
        agent_slug="my-summarizer",
    )
    assert svc.AGENT_SLUG == "my-summarizer"
    # Empty / unset falls back to the built-in preset (replay of pre-收口
    # cached DBOS step inputs).
    assert (
        SummarizeService(
            provider_key="qwen", provider_config={}, agent_slug=""
        ).AGENT_SLUG
        == "summarize"
    )
    assert SummarizeService().AGENT_SLUG == "summarize"

    composed = ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="my-summarizer",
        model="qwen-max",
        temperature=0.0,
        max_tokens=512,
        system_message="x",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="x",
    )
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)
    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={
            "content": '{"summary":"s","key_points":[],"topics":[]}',
            "raw": {},
        }
    )

    async def _build(*, primary_model, **_kw):
        return MagicMock()

    with (
        patch(
            "app.services.ai.summarize.summarize_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.summarize.summarize_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.summarize.summarize_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            side_effect=_build,
        ),
    ):
        await svc.summarize(
            transcript="hello world", user_id=None, parsed_media_id=1, title="T"
        )

    assert composer.compose.await_args.args[0].agent_slug == "my-summarizer"


@pytest.mark.asyncio
async def test_visual_analysis_honors_assigned_model() -> None:
    """Guard: the RESOLVED model reaches the wire, not the composer's own value.

    Locks in the second-half fix of the historical visual-analysis bug (the
    resolver picked the user's doubao model, this composed 'analyze' whose row
    said qwen-max, and the composed model won).

    ⚠️ The premise flipped on 2026-08-20 (#1945) and this test was rewritten to
    match. It used to feed ``provider_config["model"] = "gpt-4o"`` as an
    "informational cost field that must not win" and assert that
    ``composed.model`` won instead. That shape is no longer constructible:
    ``resolve_task_ai_config`` — the only producer of this service's
    ``provider_config`` — now writes the RESOLVED model into that key, next to
    the api_key/base_url resolved FOR it. So a non-empty
    ``provider_config["model"]`` is by definition the model to dial, and the
    "gpt-4o" string only survives as the ``self.model`` DEFAULT when the
    resolver produced nothing (covered by the companion test below).
    """
    svc = VisualAnalysisService(
        agent_slug="analyze",
        provider_config={
            # What resolve_task_ai_config hands over: the resolved model, and
            # the credentials that match THAT model.
            "model": _ASSIGNED,
            "api_key": "k",
            "base_url": "http://host/v1",
        },
    )

    # The composer's own value — a stand-in for its "qwen-max" agent-row
    # fallback. It must NOT win over the resolved model.
    composed = _composed("qwen-max")
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={"content": '{"category":"Food"}', "raw": {}}
    )

    captured: dict = {}

    async def _capture_build(*, primary_model, **_kw):
        captured["adapter_model"] = primary_model
        return MagicMock()

    with (
        patch.object(
            svc, "_encode_image_from_url", new=AsyncMock(return_value="B64DATA")
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            side_effect=_capture_build,
        ),
    ):
        await svc.analyze_l1("https://example.com/cover.jpg")

    # The resolved model drives the adapter, not the composer's "qwen-max".
    assert captured["adapter_model"] == _ASSIGNED
    # ...and the composed object handed to AgentRunner carries it too, so the
    # runner and the fallback chain cannot dial different models.
    assert runner.run_turn.await_args.args[0].model == _ASSIGNED


@pytest.mark.asyncio
async def test_visual_analysis_never_dials_the_gpt4o_cost_placeholder() -> None:
    """``VisualAnalysisService.self.model`` defaults to ``"gpt-4o"`` — a cost
    -estimate coefficient, not a model anyone configured. When the resolver
    produced NO model (empty provider_config), that placeholder must not be
    dialed; the composed agent row's model is the only real candidate left.

    This is the companion trap to the test above: the two together pin both
    directions of "which of the two strings is the real model".
    """
    svc = VisualAnalysisService(agent_slug="analyze", provider_config={})
    assert svc.model == "gpt-4o", "前提变了:这个占位默认没了,本用例要重写"

    composed = _composed(_ASSIGNED)
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={"content": '{"category":"Food"}', "raw": {}}
    )

    captured: dict = {}

    async def _capture_build(*, primary_model, **_kw):
        captured["adapter_model"] = primary_model
        return MagicMock()

    with (
        patch.object(
            svc, "_encode_image_from_url", new=AsyncMock(return_value="B64DATA")
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            side_effect=_capture_build,
        ),
    ):
        await svc.analyze_l1("https://example.com/cover.jpg")

    assert captured["adapter_model"] == _ASSIGNED


@pytest.mark.asyncio
async def test_conversation_summary_honors_assigned_model() -> None:
    """Regression: the group-chat rolling compactor is a maintenance-tier
    summarizer. It previously hardcoded ``QwenAdapter(model="qwen-turbo")``
    behind a DashScope-key check — silently no-op on non-Qwen deployments and
    blind to the admin's ``maintenance_llm_model``. It must now route the
    resolved maintenance model through ``resolve_db_adapter`` (module "chat").

    (The commitment-harvester summarizer in ai_library_chat_service shares
    this exact resolve chain — a nested closure covered by the same class.)
    """
    captured: dict = {}

    async def _fake_resolve(model, module, user_provider_config=None):
        captured["resolve_model"] = model
        captured["module"] = module

        fake_adapter = MagicMock()

        async def _call(cs, messages):
            captured["wire_model"] = cs.model
            # Real wire shape — see app/services/ai/adapters/response.py.
            return {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "SUMMARY"},
                        "finish_reason": "stop",
                    }
                ]
            }

        fake_adapter.call = _call
        return fake_adapter

    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
        side_effect=_fake_resolve,
    ):
        out = await conv_mem._summarize("prev", "transcript", _ASSIGNED)

    assert out == "SUMMARY"
    # The resolved maintenance model reached the adapter factory + the wire,
    # never a hardcoded "qwen-turbo".
    assert captured["resolve_model"] == _ASSIGNED
    assert captured["wire_model"] == _ASSIGNED
    assert captured["module"] == "chat"
