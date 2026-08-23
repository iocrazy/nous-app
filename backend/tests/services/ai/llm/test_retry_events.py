"""Every retry leaves a durable trace.

Retry state lives in a loop variable today (`for attempt in range(...)`), which
means three things are impossible: the UI cannot say "attempt 2 of 4, waiting
3.2s", a restart resets the count to zero mid-backoff, and after the fact
nobody can answer "how many times did we try before this failed".

The fix is to emit each retry as an event before sleeping. The count then comes
from the event stream rather than from process memory.

`policy_key` is what keeps the count honest across a config change: it is a
normalized serialization of the policy actually in force at that moment, so
changing max_retries or the delays starts a fresh count instead of silently
continuing an old one under new rules.

⚠️ Boundary, deliberately restated here because it is easy to lose: everything
in this file is SAME-MODEL transient retry. Cross-model fallback remains
prohibited by an explicit product decision — a retry event is not a fallback
event, and this must never become the seam where one turns into the other.
"""

from unittest.mock import AsyncMock

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.llm.llm_retry_middleware import LLMRetryMiddleware


def _composed(model="qwen-max"):
    from uuid import UUID

    return ComposedSystemPrompt(
        agent_id=UUID(int=0),
        agent_slug="t",
        model=model,
        temperature=0.0,
        max_tokens=16,
        system_message="s",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="",
    )


class _Boom(Exception):
    def __init__(self, status=503):
        super().__init__("upstream unavailable")
        self.status_code = status


def _mw(adapter, events, **kw):
    async def _observe(ev):
        events.append(ev)

    mw = LLMRetryMiddleware(adapter=adapter, on_retry=_observe, **kw)

    async def _sleep(_s):
        return None

    mw.sleep = _sleep
    return mw


# ── the event ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_one_event_per_retry_not_per_attempt():
    """3 failures then success = 3 retries. The first try is not a retry."""
    adapter = AsyncMock()
    adapter.call.side_effect = [
        _Boom(),
        _Boom(),
        _Boom(),
        {"choices": [{"message": {"content": "ok"}}]},
    ]
    events: list[dict] = []
    await _mw(adapter, events, max_retries=5).call(_composed(), [])
    assert len(events) == 3, [e.get("attempt") for e in events]
    assert [e["attempt"] for e in events] == [1, 2, 3]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_event_when_the_first_try_succeeds():
    adapter = AsyncMock()
    adapter.call.return_value = {"choices": [{"message": {"content": "ok"}}]}
    events: list[dict] = []
    await _mw(adapter, events).call(_composed(), [])
    assert events == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_event_carries_what_a_ui_needs_to_render_progress():
    adapter = AsyncMock()
    adapter.call.side_effect = [_Boom(), {"choices": [{"message": {"content": "k"}}]}]
    events: list[dict] = []
    await _mw(adapter, events, max_retries=4, base_delay_s=2.0).call(
        _composed("qwen-max"), []
    )
    ev = events[0]
    assert ev["attempt"] == 1 and ev["max_retries"] == 4
    assert ev["model"] == "qwen-max"
    assert isinstance(ev["delay_ms"], int) and ev["delay_ms"] > 0
    assert ev["failure"], "a retry with no reason is not actionable"
    assert ev["policy_key"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_event_lands_before_the_wait_not_after():
    """A UI that only learns of the wait once it is over cannot show it, and a
    crash mid-backoff would leave no trace at all."""
    order: list[str] = []
    adapter = AsyncMock()
    adapter.call.side_effect = [_Boom(), {"choices": [{"message": {"content": "k"}}]}]

    async def _observe(_ev):
        order.append("event")

    mw = LLMRetryMiddleware(adapter=adapter, on_retry=_observe)

    async def _sleep(_s):
        order.append("sleep")

    mw.sleep = _sleep
    await mw.call(_composed(), [])
    assert order[:2] == ["event", "sleep"], order


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_observer_that_throws_never_breaks_the_call():
    """Telemetry is never worth failing a turn over."""
    adapter = AsyncMock()
    adapter.call.side_effect = [_Boom(), {"choices": [{"message": {"content": "k"}}]}]

    async def _angry(_ev):
        raise RuntimeError("sink down")

    mw = LLMRetryMiddleware(adapter=adapter, on_retry=_angry)

    async def _sleep(_s):
        return None

    mw.sleep = _sleep
    out = await mw.call(_composed(), [])
    assert out["choices"][0]["message"]["content"] == "k"


# ── policy_key ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_policy_key_is_stable_for_the_same_policy():
    a = LLMRetryMiddleware(adapter=None, max_retries=3, base_delay_s=1.0)
    b = LLMRetryMiddleware(adapter=None, max_retries=3, base_delay_s=1.0)
    assert a.policy_key() == b.policy_key()


@pytest.mark.unit
@pytest.mark.parametrize(
    "kw", [{"max_retries": 4}, {"base_delay_s": 2.0}, {"max_delay_s": 60.0}]
)
def test_policy_key_changes_when_the_policy_changes(kw):
    """A count carried across a policy change is a count under the wrong
    rules — the key changing is what makes it start over."""
    base = LLMRetryMiddleware(adapter=None)
    changed = LLMRetryMiddleware(adapter=None, **kw)
    assert base.policy_key() != changed.policy_key()


@pytest.mark.unit
def test_policy_key_ignores_things_that_are_not_policy():
    """The adapter and the cancel hook are not policy; keying on them would
    restart the count for unrelated reasons."""
    a = LLMRetryMiddleware(adapter=object())
    b = LLMRetryMiddleware(adapter=object(), cancel_check=AsyncMock())
    assert a.policy_key() == b.policy_key()
