"""The preflight measures the history once: the compactor's ``tokens_after``
is handed to the budget guard as ``measured_tokens``.

``tokens_after == 0`` is the compactor's "not measured" sentinel (kill
switch / window <= 0), and a crashed compactor yields no stats at all — both
must leave the guard to count for itself.
"""

from __future__ import annotations

import pytest

from app.agent_framework.context_compactor import CompactionStats, CompactionTier
from app.services.ai.runner import agent_runner as ar
from tests.runner.test_turn_end_reasons import (
    _composed,
    _NoStreamAdapter,
    _Rec,
    _runner,
)

pytestmark = pytest.mark.unit


def _stats(tokens_after: int) -> CompactionStats:
    return CompactionStats(
        tier=CompactionTier.GREEN,
        tokens_before=tokens_after,
        tokens_after=tokens_after,
        tokens_saved=0,
    )


@pytest.fixture
def seen(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def _spy(**kw):
        calls.append(kw)

    monkeypatch.setattr("app.agent_framework.check_context_budget", _spy)
    return calls


async def _run() -> dict:
    return await _runner(_NoStreamAdapter(), "paused").run_turn(
        _composed(), [{"role": "user", "content": "q"}], recorder=_Rec()
    )


@pytest.mark.parametrize("tokens_after,expected", [(1234, 1234), (0, None)])
async def test_compactor_measurement_reaches_the_guard(
    monkeypatch, seen, tokens_after, expected
):
    async def _compact(**kw):
        return kw["user_messages"], _stats(tokens_after)

    monkeypatch.setattr(ar._DEFAULT_COMPACTOR, "maybe_compact", _compact)
    await _run()
    assert seen and seen[0]["measured_tokens"] == expected


async def test_crashed_compactor_leaves_the_guard_to_count(monkeypatch, seen):
    async def _boom(**kw):
        raise RuntimeError("tokenizer exploded")

    monkeypatch.setattr(ar._DEFAULT_COMPACTOR, "maybe_compact", _boom)
    await _run()
    assert seen and seen[0]["measured_tokens"] is None
