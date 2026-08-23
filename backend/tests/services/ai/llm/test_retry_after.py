"""The provider's own `Retry-After` is the best information available.

Today the retry middleware ignores it entirely. On a 429 that says
`Retry-After: 3600`, we retry three times on our own 1-30s backoff, hit the
same wall each time, burn the whole budget in under a minute, and report
exhaustion — having been told plainly, up front, that nothing would work for
an hour.

Two rules, and the second matters more than the first:

* **within our ceiling** — honour it. The provider knows when the bucket
  refills; a computed backoff is a guess next to a fact.
* **beyond our ceiling** — give up NOW and surface the wait. Sleeping an hour
  inside one turn is worse than failing: it holds a connection, a task row,
  and the user's attention, and every caller above has its own deadline. The
  header stops being advice and becomes a way for one provider to hang the
  run.
"""

import pytest

from app.services.ai.llm.llm_retry_middleware import retry_after_seconds


class _Resp:
    def __init__(self, headers, status_code=429):
        self.headers = headers
        self.status_code = status_code


class _Err(Exception):
    def __init__(self, headers=None, status_code=429):
        super().__init__("rate limited")
        self.status_code = status_code
        if headers is not None:
            self.response = _Resp(headers, status_code)


@pytest.mark.unit
@pytest.mark.parametrize("raw, want", [("30", 30.0), ("0", 0.0), ("3600", 3600.0)])
def test_reads_delay_seconds_form(raw, want):
    assert retry_after_seconds(_Err({"Retry-After": raw})) == want


@pytest.mark.unit
def test_header_lookup_is_case_insensitive():
    """HTTP header names are case-insensitive and providers disagree in
    practice; a case-sensitive read silently returns None on half of them."""
    assert retry_after_seconds(_Err({"retry-after": "12"})) == 12.0


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw", ["", "   ", "soon", "-5", "NaN", "Wed, 21 Oct 2026 07:28:00 GMT"]
)
def test_unparseable_values_are_none_not_zero(raw):
    """`None` means "no guidance, use our backoff". Zero would mean "retry
    immediately", which is the opposite of what a garbled header implies.

    The HTTP-date form is deliberately unsupported rather than
    half-implemented: parsing it needs the server's clock, and a wrong
    absolute time is more dangerous than no guidance.
    """
    assert retry_after_seconds(_Err({"Retry-After": raw})) is None


@pytest.mark.unit
def test_absent_header_and_absent_response_are_none():
    assert retry_after_seconds(_Err({})) is None
    assert retry_after_seconds(_Err(headers=None)) is None
    assert retry_after_seconds(Exception("plain")) is None


# ── the two rules, at the middleware seam ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.unit
async def test_a_retry_after_within_our_ceiling_is_honoured():
    """The provider knows when its bucket refills; our backoff is a guess."""
    from unittest.mock import AsyncMock

    from app.services.ai.llm.llm_retry_middleware import LLMRetryMiddleware

    slept: list[float] = []

    adapter = AsyncMock()
    adapter.call.side_effect = [
        _Err({"Retry-After": "7"}),
        {"choices": [{"message": {"content": "ok"}}]},
    ]
    mw = LLMRetryMiddleware(adapter=adapter, max_retries=3, max_delay_s=30.0)

    async def _sleep(s):
        slept.append(s)

    mw.sleep = _sleep
    out = await mw.call(_composed_stub(), [])
    assert out["choices"][0]["message"]["content"] == "ok"
    # Assert the TOTAL, not the individual calls: the backoff sleep is
    # chunked into 1s polls so a cancel can land mid-wait. The chunking is
    # cancellation machinery; the honoured delay is the behaviour.
    assert (
        abs(sum(slept) - 7.0) < 0.01
    ), f"expected the provider's 7s in total, slept {slept} (sum={sum(slept)})"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_a_retry_after_beyond_our_ceiling_gives_up_immediately():
    """Never sleep an hour inside one turn. Fail, and say how long it asked
    for, so the caller above can decide — it has its own deadline."""
    from unittest.mock import AsyncMock

    from app.services.ai.llm.llm_retry_middleware import (
        LLMRetryExhausted,
        LLMRetryMiddleware,
    )

    slept: list[float] = []
    adapter = AsyncMock()
    adapter.call.side_effect = _Err({"Retry-After": "3600"})
    mw = LLMRetryMiddleware(adapter=adapter, max_retries=3, max_delay_s=30.0)

    async def _sleep(s):
        slept.append(s)

    mw.sleep = _sleep
    with pytest.raises((LLMRetryExhausted, Exception)) as ei:
        await mw.call(_composed_stub(), [])

    assert not slept, f"gave up but still slept: {slept}"
    assert adapter.call.await_count == 1, (
        "asked to wait an hour and tried again anyway: "
        f"{adapter.call.await_count} calls"
    )
    assert "3600" in str(ei.value), str(ei.value)


def _composed_stub():
    from uuid import UUID

    from app.schemas.ai_library import ComposedSystemPrompt

    return ComposedSystemPrompt(
        agent_id=UUID(int=0),
        agent_slug="t",
        model="m",
        temperature=0.0,
        max_tokens=16,
        system_message="s",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="",
    )
