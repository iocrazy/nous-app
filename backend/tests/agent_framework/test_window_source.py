"""``resolve_model_window`` names where the window came from (FH2 T6).

A bool ``is_known`` could not tell the admin-owned catalog from the hardcoded
table; the UI needs to point at Admin → AI Models only when neither knew the
model, and a reader of a gauge needs to know which layer answered.
"""

from __future__ import annotations

import typing

import pytest

import app.agent_framework.catalog_windows as cwin
from app.agent_framework import context_window as cw
from app.core.config import settings


@pytest.fixture(autouse=True)
def _clean_catalog_cache():
    cwin._reset_for_tests()
    yield
    cwin._reset_for_tests()


async def _catalog(monkeypatch, model: str, window: int | None) -> None:
    async def _fetch():
        return [
            {
                "name": f"nous-{model}",
                "actual_model": model,
                "context_window_tokens": window,
            }
        ]

    monkeypatch.setattr(cwin, "_fetch_catalog_rows", _fetch)
    await cwin.refresh_catalog_windows()


@pytest.mark.unit
async def test_catalog_hit_is_catalog(monkeypatch):
    await _catalog(monkeypatch, "claude-sonnet-4-6", 150_000)
    assert cw.resolve_model_window("claude-sonnet-4-6") == (150_000, "catalog")


@pytest.mark.unit
def test_table_hit_is_builtin_including_the_base_name_strip():
    assert cw.resolve_model_window("claude-sonnet-4-6") == (200_000, "builtin")
    assert cw.resolve_model_window("gpt-4o-instruct")[1] == "builtin"


@pytest.mark.unit
@pytest.mark.parametrize("model", ["totally-made-up", "", None])
def test_nobody_knew_it_is_fallback(model):
    assert cw.resolve_model_window(model) == (
        settings.LLM_MAX_CONTEXT_TOKENS,
        "fallback",
    )


@pytest.mark.unit
def test_the_source_is_a_closed_literal():
    assert set(typing.get_args(cw.WindowSource)) == {"catalog", "builtin", "fallback"}


@pytest.mark.unit
async def test_compaction_and_step_gauge_report_the_same_source_for_one_model():
    """Two writers feed view.context; for the same model they must agree on
    where the denominator came from, or the gauge flips meaning mid-run."""
    import contextlib

    from app.agent_framework.context_compactor import ContextCompactor
    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.ai.runner.step_hooks import StepHookChain
    from tests.agent_framework.test_compaction_events import _msgs, _orange, _Recorder

    model = "nous-qwen3-llm"  # in neither the (empty) catalog nor the table
    rec = _Recorder()
    msgs = _msgs()
    with contextlib.ExitStack() as st:
        for p in _orange(msgs, summary_tokens=50)[1:]:  # real resolve_model_window
            st.enter_context(p)
        from unittest.mock import patch

        # the fallback window is settings-driven; pin it so orange is reachable
        st.enter_context(patch.object(settings, "LLM_MAX_CONTEXT_TOKENS", 1000))
        await ContextCompactor().maybe_compact(
            system_message="sys",
            user_messages=msgs,
            model=model,
            adapter=object(),
            recorder=rec,
        )
        start = rec.payload("compaction_start")

        class _Gauge:
            measured: list = []

            async def record_event(self, *a, **k):
                pass

            def record_usage(self, **kw):
                pass

            def cost_of(self, p, c, cached=0):
                return None

            def measure_context(self, used, window, window_source=None):
                self.measured.append((used, window, window_source))

        class _Composed:
            pass

        _Composed.model = model
        g = _Gauge()
        runner = AgentRunner(
            adapter=object(), skill_tool=object(), step_hooks=StepHookChain([])
        )
        await runner._step_ended(g, _Composed(), 1, 0.0, {"prompt_tokens": 500}, "stop")

    assert start["window_source"] == "fallback"
    assert g.measured == [(500, start["window"], start["window_source"])]
