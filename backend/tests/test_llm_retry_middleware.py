"""Unit tests for LLMRetryMiddleware — error classification, backoff, cancel."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.llm.llm_retry_middleware import (
    LLMCallError,
    LLMRetryExhausted,
    LLMRetryMiddleware,
    RunCancelled,
    classify_error,
    compute_backoff,
)


def _composed() -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=UUID("00000000-0000-0000-0000-000000000001"),
        agent_slug="script_ai",
        model="qwen-max",
        temperature=0.7,
        max_tokens=1024,
        system_message="SYSTEM",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="x",
    )


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


class _StatusError(Exception):
    def __init__(self, status_code: int, msg: str = "err") -> None:
        super().__init__(msg)
        self.status_code = status_code


@pytest.mark.unit
def test_classify_401_is_non_retryable():
    assert classify_error(_StatusError(401)) == "non_retryable"


@pytest.mark.unit
def test_classify_400_is_non_retryable():
    assert classify_error(_StatusError(400)) == "non_retryable"


@pytest.mark.unit
def test_classify_429_is_retryable():
    assert classify_error(_StatusError(429)) == "retryable"


@pytest.mark.unit
def test_classify_503_is_retryable():
    assert classify_error(_StatusError(503)) == "retryable"


@pytest.mark.unit
def test_classify_504_is_retryable():
    assert classify_error(_StatusError(504)) == "retryable"


@pytest.mark.unit
def test_classify_502_5xx_default_retryable():
    """5xx codes not explicitly listed default to retryable."""
    assert classify_error(_StatusError(599)) == "retryable"


@pytest.mark.unit
def test_classify_4xx_default_non_retryable():
    """4xx codes not explicitly listed default to non_retryable."""
    assert classify_error(_StatusError(418)) == "non_retryable"


@pytest.mark.unit
def test_classify_timeout_by_name():
    class TimeoutError_(Exception):
        pass

    assert classify_error(TimeoutError_()) == "retryable"


@pytest.mark.unit
def test_classify_connection_by_name():
    class ConnectionRefusedError_(Exception):
        pass

    assert classify_error(ConnectionRefusedError_()) == "retryable"


@pytest.mark.unit
def test_classify_unknown_returns_unknown():
    assert classify_error(ValueError("???")) == "unknown"


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_backoff_first_attempt_at_base_delay():
    """attempt=1 with rng=0.5 → no jitter offset → exactly base_delay."""
    result = compute_backoff(1, base_delay_s=2.0, max_delay_s=60.0, rng=lambda: 0.5)
    assert result == pytest.approx(2.0)


@pytest.mark.unit
def test_backoff_exponential_growth():
    """attempt=2 → 2x base, attempt=3 → 4x base (with rng=0.5 → no jitter)."""
    rng = lambda: 0.5  # noqa: E731
    assert compute_backoff(2, base_delay_s=1.0, rng=rng) == pytest.approx(2.0)
    assert compute_backoff(3, base_delay_s=1.0, rng=rng) == pytest.approx(4.0)
    assert compute_backoff(4, base_delay_s=1.0, rng=rng) == pytest.approx(8.0)


@pytest.mark.unit
def test_backoff_capped_at_max_delay():
    rng = lambda: 0.5  # noqa: E731
    result = compute_backoff(20, base_delay_s=1.0, max_delay_s=10.0, rng=rng)
    assert result == pytest.approx(10.0)


@pytest.mark.unit
def test_backoff_jitter_low_end():
    """rng=0 → minimum jitter → delay = raw * (1 - jitter_ratio)."""
    result = compute_backoff(1, base_delay_s=10.0, jitter_ratio=0.25, rng=lambda: 0.0)
    assert result == pytest.approx(10.0 * (1 - 0.25))


@pytest.mark.unit
def test_backoff_jitter_high_end():
    """rng=1 → maximum jitter → delay = raw * (1 + jitter_ratio)."""
    result = compute_backoff(1, base_delay_s=10.0, jitter_ratio=0.25, rng=lambda: 1.0)
    assert result == pytest.approx(10.0 * (1 + 0.25))


# ---------------------------------------------------------------------------
# Middleware happy + retry paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_first_call_success_returns_immediately():
    adapter = AsyncMock()
    adapter.call.return_value = {"choices": [{"message": {"content": "ok"}}]}

    mw = LLMRetryMiddleware(adapter)
    result = await mw.call(_composed(), [])

    assert result["choices"][0]["message"]["content"] == "ok"
    adapter.call.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_retryable_raises_llm_call_error_no_retry():
    adapter = AsyncMock()
    adapter.call.side_effect = _StatusError(401, "unauthorized")

    mw = LLMRetryMiddleware(adapter, max_retries=3)
    with pytest.raises(LLMCallError):
        await mw.call(_composed(), [])

    adapter.call.assert_awaited_once()  # no retry


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retryable_then_success_returns_value():
    adapter = AsyncMock()
    adapter.call.side_effect = [
        _StatusError(503, "down"),
        {"choices": [{"message": {"content": "recovered"}}]},
    ]

    mw = LLMRetryMiddleware(adapter, base_delay_s=0)
    result = await mw.call(_composed(), [])

    assert result["choices"][0]["message"]["content"] == "recovered"
    assert adapter.call.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retryable_exhausted_raises_retry_exhausted():
    adapter = AsyncMock()
    adapter.call.side_effect = _StatusError(429)

    mw = LLMRetryMiddleware(adapter, max_retries=2, base_delay_s=0)
    with pytest.raises(LLMRetryExhausted):
        await mw.call(_composed(), [])

    # 1 first try + 2 retries = 3 calls
    assert adapter.call.await_count == 3


# ---------------------------------------------------------------------------
# Cancel during backoff
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_during_backoff_raises_run_cancelled():
    """★ P0 — cancel signal must interrupt sleep."""
    adapter = AsyncMock()
    adapter.call.side_effect = _StatusError(503)

    cancel_check = AsyncMock()
    cancel_check.return_value = True  # cancel set immediately

    mw = LLMRetryMiddleware(
        adapter,
        cancel_check=cancel_check,
        max_retries=3,
        base_delay_s=0.5,  # longer than poll interval so we hit the check
    )

    with pytest.raises(RunCancelled):
        await mw.call(_composed(), [])

    cancel_check.assert_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_check_false_lets_retry_proceed():
    adapter = AsyncMock()
    adapter.call.side_effect = [
        _StatusError(503),
        {"choices": [{"message": {"content": "ok"}}]},
    ]
    cancel_check = AsyncMock(return_value=False)

    mw = LLMRetryMiddleware(adapter, cancel_check=cancel_check, base_delay_s=0)
    result = await mw.call(_composed(), [])

    assert result["choices"][0]["message"]["content"] == "ok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_check_exception_does_not_break_sleep():
    """If cancel_check raises, middleware logs + keeps sleeping. Run still retries."""
    adapter = AsyncMock()
    adapter.call.side_effect = [
        _StatusError(503),
        {"choices": [{"message": {"content": "ok"}}]},
    ]
    cancel_check = AsyncMock(side_effect=RuntimeError("DB down"))

    mw = LLMRetryMiddleware(adapter, cancel_check=cancel_check, base_delay_s=0)
    result = await mw.call(_composed(), [])

    assert result["choices"][0]["message"]["content"] == "ok"


# ---------------------------------------------------------------------------
# AI-007: global retry deadline
# ---------------------------------------------------------------------------


def _clock(values):
    """A fake monotonic() returning each value in turn, then the last forever."""
    seq = list(values)

    def _next() -> float:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return _next


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deadline_stops_retries_early():
    """With a global deadline, the loop stops well before max_retries once the
    clock shows the budget is spent — far fewer than 1 + max_retries calls."""
    adapter = AsyncMock()
    adapter.call.side_effect = _StatusError(503, "down")

    mw = LLMRetryMiddleware(
        adapter, max_retries=10, base_delay_s=0, total_deadline_seconds=5.0
    )
    # start=0, then every subsequent monotonic() reads 10s (> 5s deadline).
    mw.monotonic = _clock([0.0, 10.0])

    with pytest.raises(LLMRetryExhausted):
        await mw.call(_composed(), [])

    # First attempt runs; the deadline trips before any retry → just 1 call.
    assert adapter.call.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_deadline_uses_full_retry_budget():
    """total_deadline_seconds=None (default) keeps legacy behavior."""
    adapter = AsyncMock()
    adapter.call.side_effect = _StatusError(429)

    mw = LLMRetryMiddleware(adapter, max_retries=2, base_delay_s=0)
    with pytest.raises(LLMRetryExhausted):
        await mw.call(_composed(), [])

    assert adapter.call.await_count == 3  # 1 + 2 retries
