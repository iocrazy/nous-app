"""Compaction runs exactly once per turn on the production route.

The chat wiring hands ``stream_turn`` an ``LLMFallbackChain`` — no ``stream``
attribute — so every chunk_callback turn takes the buffered fallback, which
delegates to ``run_turn``. Both ``_stream_turn_inner`` and ``_run_turn_inner``
used to run ``_preflight_compact_and_budget``, so the history was compacted
(and, above threshold, summarised by an LLM) twice per turn. The delegated
call now skips its own preflight; the direct ``run_turn`` path keeps it.
"""

from __future__ import annotations

import pytest

from app.services.ai.runner import agent_runner as ar
from tests.runner.test_turn_end_reasons import (
    _NoStreamAdapter,
    _Rec,
    _composed,
    _runner,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def compactor_calls(monkeypatch):
    calls: list[list[dict]] = []
    real = ar._DEFAULT_COMPACTOR.maybe_compact

    async def _counting(**kw):
        calls.append(list(kw["user_messages"]))
        return await real(**kw)

    monkeypatch.setattr(ar._DEFAULT_COMPACTOR, "maybe_compact", _counting)
    return calls


async def test_buffered_fallback_compacts_once_per_turn(compactor_calls):
    rec = _Rec()
    history = [{"role": "user", "content": "q"}]
    chunks = [
        ch
        async for ch in _runner(_NoStreamAdapter(), "paused").stream_turn(
            _composed(), history, recorder=rec, auto_recorder=False
        )
    ]
    assert chunks and (chunks[-1].usage or {}).get("stop_reason") == "paused"
    assert compactor_calls == [history], compactor_calls


async def test_direct_run_turn_still_runs_its_own_preflight(compactor_calls):
    history = [{"role": "user", "content": "q"}]
    out = await _runner(_NoStreamAdapter(), "paused").run_turn(
        _composed(), history, recorder=_Rec()
    )
    assert out["stop_reason"] == "paused"
    assert compactor_calls == [history], compactor_calls


async def test_recorderless_stream_turn_routes_the_callers_user_id(monkeypatch):
    """``auto_recorder=False`` + no recorder: the caller's ``user_id`` is the
    only routing context the legacy maintenance-model summary can get
    (codex-local dials that user's machine). It used to be dropped."""
    from uuid import UUID

    seen: list[str | None] = []
    real = ar._DEFAULT_COMPACTOR.maybe_compact

    async def _spy(**kw):
        seen.append(kw.get("user_id"))
        return await real(**kw)

    monkeypatch.setattr(ar._DEFAULT_COMPACTOR, "maybe_compact", _spy)
    uid = UUID(int=42)
    async for _ in _runner(_NoStreamAdapter(), "paused").stream_turn(
        _composed(),
        [{"role": "user", "content": "q"}],
        recorder=None,
        auto_recorder=False,
        user_id=uid,
    ):
        pass
    assert seen == [str(uid)], seen
