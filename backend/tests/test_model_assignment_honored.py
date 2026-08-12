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


@pytest.mark.asyncio
async def test_summarize_honors_assigned_model() -> None:
    """Regression: summarization is NOT agent-driven — its model is resolved
    per-user (selected_model / default_summary_model) into
    provider_config["model"] -> self.model, while the composed ``summarize``
    agent row carries no per-user model so the composer falls back to its
    hardcoded "qwen-max" default. Pre-fix, ``composed.model or self.model``
    let "qwen-max" win and silently dropped the user's assigned model. The
    adapter (and the composed handed to the runner) must carry the assigned
    model instead.

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
async def test_visual_analysis_honors_assigned_model() -> None:
    """Guard: visual-analysis IS agent-driven — the caller resolves the user's
    assigned agent slug (task_assignment.visual_analysis) whose composed.model
    is the user's model. That composed model must drive the adapter, NOT the
    ``self.model = provider_config["model"] or "gpt-4o"`` cost-estimate field.
    Locks in the second-half fix of the historical visual-analysis bug.
    """
    svc = VisualAnalysisService(
        agent_slug="analyze",
        provider_config={
            "model": "gpt-4o",  # informational cost field — must NOT win
            "api_key": "k",
            "base_url": "http://host/v1",
        },
    )

    composed = MagicMock()
    composed.agent_id = "00000000-0000-0000-0000-000000000001"
    composed.agent_slug = "analyze"
    composed.model = _ASSIGNED  # the assigned agent's model
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={"content": '{"category":"Food"}', "raw": {}}
    )

    captured: dict = {}

    def _capture_build(model):
        captured["adapter_model"] = model
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
        patch.object(svc, "_build_adapter", side_effect=_capture_build),
    ):
        await svc.analyze_l1("https://example.com/cover.jpg")

    # The composed (assigned) model drives the adapter, not "gpt-4o".
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
            return {"content": "SUMMARY"}

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
