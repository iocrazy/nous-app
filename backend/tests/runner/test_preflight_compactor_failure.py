"""A compactor crash must not cost the whole turn.

Before 3d batch 2 the chat service ran its own compactor inside a try, so a
tokenizer / prune exception degraded to "send the raw history". Once the
runner's preflight became the ONLY compactor, the same exception propagated
out of ``_preflight_compact_and_budget`` and failed the turn. The preflight
now logs the crash and falls back to the raw history; the budget guard that
follows still turns an oversized history into the typed
``context_budget_exceeded`` rejection instead of a stack trace.
"""

from __future__ import annotations

import pytest

from app.services.ai.runner import agent_runner as ar
from tests.runner.test_turn_end_reasons import (
    _composed,
    _NoStreamAdapter,
    _Rec,
    _runner,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def exploding_compactor(monkeypatch):
    async def _boom(**kw):
        raise RuntimeError("tokenizer exploded")

    monkeypatch.setattr(ar._DEFAULT_COMPACTOR, "maybe_compact", _boom)


async def test_compactor_crash_falls_back_to_raw_history(exploding_compactor):
    history = [{"role": "user", "content": "q"}]
    out = await _runner(_NoStreamAdapter(), "paused").run_turn(
        _composed(), history, recorder=_Rec()
    )
    assert out["stop_reason"] == "paused", out


async def test_compactor_crash_still_hits_the_budget_guard(
    exploding_compactor, monkeypatch
):
    from app.agent_framework import ContextWindowError

    def _too_big(*a, **k):
        raise ContextWindowError("way over")

    monkeypatch.setattr("app.agent_framework.check_context_budget", _too_big)
    history = [{"role": "user", "content": "x" * 10}]
    out = await _runner(_NoStreamAdapter(), "paused").run_turn(
        _composed(), history, recorder=_Rec()
    )
    assert out.get("error_code") == "context_budget_exceeded", out
