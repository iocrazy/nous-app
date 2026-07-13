"""ScriptAIService adapter dispatch honors the resolver's explicit
provider_key — never a prefix guess on the model name.

Same class as the 2026-07-13 chat outage (#1313): resolve_task_ai_config's
platform-catalog hit hands back ``provider_key = actual_provider`` and
``model = actual_model`` verbatim. ``_build_runner`` then called
``get_adapter_for_user(model, ...)``, which re-derives the provider from
the MODEL PREFIX and raises for catalog models like ``qwen3-6-35b`` —
with no fallback, unlike the sibling task services (caption / classify /
translate / visual all degrade to the OpenAI-compatible adapter).
"""

from __future__ import annotations

import pytest

from app.services.ai.adapters.openai import OpenAIAdapter
from app.services.ai.adapters.qwen import QwenAdapter
from app.services.storyboard.script.script_ai_service import ScriptAIService

_CFG = {"api_key": "platform-key", "base_url": "https://nous.example.com/v1"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_runner_dispatches_on_explicit_provider_key():
    """Known provider_key + unknown-prefix model → the named adapter,
    not a ValueError from the prefix rule."""
    svc = ScriptAIService(provider_key="openai", provider_config=dict(_CFG))
    runner = await svc._build_runner("qwen3-6-35b")
    assert isinstance(runner.adapter, OpenAIAdapter)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_runner_unknown_provider_label_degrades_to_openai_compatible():
    """Unknown label ('nous') + unknown prefix → OpenAI-compatible
    QwenAdapter with the supplied base_url (resolve_provider_key ladder)."""
    svc = ScriptAIService(provider_key="nous", provider_config=dict(_CFG))
    runner = await svc._build_runner("qwen3-6-35b")
    assert isinstance(runner.adapter, QwenAdapter)
