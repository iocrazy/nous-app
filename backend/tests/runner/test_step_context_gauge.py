"""Every step_end feeds the context gauge from this call's prompt tokens
(local fold, no event row) — without it view.context stays null on turns
that never compact (found on the real stack 2026-09-05)."""

from __future__ import annotations

import pytest

from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.runner.step_hooks import StepHookChain


class _Rec:
    def __init__(self):
        self.measured = []
        self.events = []

    async def record_event(self, *a, **k):
        self.events.append(a)

    def record_usage(self, **kw):
        pass

    def cost_of(self, p, c, cached=0):
        return None

    def measure_context(self, used, window):
        self.measured.append((used, window))


class _Composed:
    model = "gpt-4o-2024-08-06"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_step_end_measures_context_against_the_model_window(monkeypatch):
    import app.agent_framework.context_window as cw

    monkeypatch.setattr(cw, "resolve_model_window", lambda model: (128_000, True))
    rec = _Rec()
    runner = AgentRunner(
        adapter=object(), skill_tool=object(), step_hooks=StepHookChain([])
    )
    await runner._step_ended(
        rec,
        _Composed(),
        1,
        0.0,
        {"prompt_tokens": 3067, "completion_tokens": 10},
        "stop",
    )
    assert rec.measured == [(3067, 128_000)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_window_or_zero_prompt_measures_nothing(monkeypatch):
    import app.agent_framework.context_window as cw

    monkeypatch.setattr(cw, "resolve_model_window", lambda model: (0, False))
    rec = _Rec()
    runner = AgentRunner(
        adapter=object(), skill_tool=object(), step_hooks=StepHookChain([])
    )
    await runner._step_ended(rec, _Composed(), 1, 0.0, {"prompt_tokens": 100}, "stop")
    await runner._step_ended(rec, _Composed(), 2, 0.0, {"prompt_tokens": 0}, "stop")
    assert rec.measured == []
