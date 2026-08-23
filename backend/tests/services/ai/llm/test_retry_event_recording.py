"""Retry events reach the run transcript.

The middleware emits (`test_retry_events.py`); this proves the emission is
connected to storage. The gap between "helper works" and "helper is called" is
where this repo has lost features before, so both halves get a test.

The adapter is built during wiring, before the run exists, so the recorder
cannot be constructor-injected. The runner attaches the observer for the
duration of the turn and detaches afterwards — an adapter that outlived one
turn while still holding a previous run's recorder would write this run's
retries into the previous run's transcript.
"""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.llm.retry_events import make_retry_observer
from app.services.ai.runner.agent_runner import AgentRunner


def _composed():
    return ComposedSystemPrompt(
        agent_id=UUID(int=0),
        agent_slug="t",
        model="qwen-max",
        temperature=0.0,
        max_tokens=16,
        system_message="s",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="",
    )


class _Recorder:
    def __init__(self):
        self.events = []

    async def record_event(self, event_type, payload):
        self.events.append((event_type, payload))

    def record_usage(self, **kw):
        pass

    async def heartbeat(self):
        pass

    async def check_cancelled(self):
        return False

    def record_skill(self, slug):
        pass


class _SkillTool:
    async def execute(self, args):
        return {}


class _AdapterWithSlot:
    """Stands in for the fallback chain: it accepts an observer."""

    on_retry = None

    def __init__(self):
        self.calls = 0

    async def call(self, composed, messages, **kw):
        self.calls += 1
        return {"choices": [{"message": {"content": "ok"}}]}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_observer_writes_an_llm_retry_event():
    rec = _Recorder()
    obs = make_retry_observer(rec)
    await obs({"attempt": 2, "max_retries": 3, "delay_ms": 3200})
    assert rec.events == [
        ("llm_retry", {"attempt": 2, "max_retries": 3, "delay_ms": 3200})
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_recorder_without_record_event_is_a_no_op_not_a_crash():
    obs = make_retry_observer(object())
    await obs({"attempt": 1})  # must not raise


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_runner_attaches_the_observer_for_the_turn():
    adapter = _AdapterWithSlot()
    runner = AgentRunner(adapter=adapter, skill_tool=_SkillTool())
    rec = _Recorder()

    seen = {}

    async def _call(composed, messages, **kw):
        seen["attached"] = adapter.on_retry is not None
        return {"choices": [{"message": {"content": "ok"}}]}

    adapter.call = _call
    await runner.run_turn(
        _composed(), [{"role": "user", "content": "hi"}], recorder=rec
    )
    assert seen.get("attached") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_observer_is_detached_after_the_turn():
    """An adapter still holding the previous run's recorder would file this
    run's retries under that run."""
    adapter = _AdapterWithSlot()
    runner = AgentRunner(adapter=adapter, skill_tool=_SkillTool())
    await runner.run_turn(
        _composed(), [{"role": "user", "content": "hi"}], recorder=_Recorder()
    )
    assert adapter.on_retry is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_adapter_without_the_slot_is_left_alone():
    """Plain adapters (every non-chain one) must keep working untouched."""

    plain = AsyncMock()
    plain.call.return_value = {"choices": [{"message": {"content": "ok"}}]}
    runner = AgentRunner(adapter=plain, skill_tool=_SkillTool())
    out = await runner.run_turn(
        _composed(), [{"role": "user", "content": "hi"}], recorder=_Recorder()
    )
    assert out["content"] == "ok"
