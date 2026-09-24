"""Every step_end feeds the context gauge from this call's prompt tokens
(local fold, no event row) — without it view.context stays null on turns
that never compact (found on the real stack 2026-09-05).

Window source (framework hardening batch 2, T6): the gauge is written for
every model, including one with no configured window. A fallback window is a
guess, so the measurement carries ``window_source`` and the UI marks it —
silently writing nothing (the old ``if known`` gate) left the gauge blank on
exactly the models whose windows needed configuring.
"""

from __future__ import annotations

import logging

import pytest

from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.runner.step_hooks import StepHookChain


class _Rec:
    def __init__(self, boom: bool = False):
        self.measured = []
        self.events = []
        self.boom = boom

    async def record_event(self, *a, **k):
        self.events.append(a)

    def record_usage(self, **kw):
        pass

    def cost_of(self, p, c, cached=0):
        return None

    def measure_context(self, used, window, window_source=None):
        if self.boom:
            raise RuntimeError("fold down")
        self.measured.append((used, window, window_source))


class _Composed:
    model = "gpt-4o-2024-08-06"


def _runner() -> AgentRunner:
    return AgentRunner(
        adapter=object(), skill_tool=object(), step_hooks=StepHookChain([])
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["catalog", "builtin"])
async def test_step_end_measures_context_against_the_model_window(monkeypatch, source):
    import app.agent_framework.context_window as cw

    monkeypatch.setattr(cw, "resolve_model_window", lambda model: (128_000, source))
    rec = _Rec()
    await _runner()._step_ended(
        rec,
        _Composed(),
        1,
        0.0,
        {"prompt_tokens": 3067, "completion_tokens": 10},
        "stop",
    )
    assert rec.measured == [(3067, 128_000, source)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_fallback_window_still_writes_the_gauge_and_says_so(monkeypatch):
    import app.agent_framework.context_window as cw

    monkeypatch.setattr(cw, "resolve_model_window", lambda model: (28_000, "fallback"))
    rec = _Rec()
    await _runner()._step_ended(
        rec, _Composed(), 1, 0.0, {"prompt_tokens": 700}, "stop"
    )
    assert rec.measured == [(700, 28_000, "fallback")]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_zero_prompt_or_zero_window_measures_nothing(monkeypatch):
    import app.agent_framework.context_window as cw

    monkeypatch.setattr(cw, "resolve_model_window", lambda model: (0, "fallback"))
    rec = _Rec()
    runner = _runner()
    await runner._step_ended(rec, _Composed(), 1, 0.0, {"prompt_tokens": 100}, "stop")
    monkeypatch.setattr(cw, "resolve_model_window", lambda model: (1000, "builtin"))
    await runner._step_ended(rec, _Composed(), 2, 0.0, {"prompt_tokens": 0}, "stop")
    assert rec.measured == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_failing_gauge_is_logged_not_swallowed_and_never_fails_the_turn(
    monkeypatch, caplog
):
    import app.agent_framework.context_window as cw

    monkeypatch.setattr(cw, "resolve_model_window", lambda model: (1000, "builtin"))
    rec = _Rec(boom=True)
    with caplog.at_level(logging.WARNING):
        await _runner()._step_ended(
            rec, _Composed(), 3, 0.0, {"prompt_tokens": 100}, "stop"
        )
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("fold down" in r.getMessage() for r in warned), [
        r.getMessage() for r in caplog.records
    ]
