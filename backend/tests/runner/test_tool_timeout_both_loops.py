"""A hanging tool times out and the TURN CONTINUES (phase 2b-1 §3) — on both
loops. The stream case uses an adapter WITHOUT ``stream`` so it takes the
buffered-fallback branch production always takes (CLAUDE.md 已知陷阱)."""

import asyncio

import pytest

from tests.runner.test_turn_end_reasons import _composed

pytestmark = pytest.mark.unit

_SKILL_CALL = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "Skill", "arguments": '{"skill": "x"}'},
}


class _Rec:
    def __init__(self):
        self.events = []
        self.views = {"view": {}}

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload))

    def record_usage(self, **k):
        pass

    async def heartbeat(self):
        pass

    async def check_cancelled(self):
        return False

    def record_skill(self, s):
        pass

    def turn_ends(self):
        return [p for t, p in self.events if t == "turn_end"]


class _TwoRoundAdapter:  # no ``stream`` attribute on purpose
    def __init__(self):
        self.calls = 0

    async def call(self, composed, messages, **kw):
        self.calls += 1
        if self.calls == 1:
            return {
                "choices": [
                    {
                        "message": {"content": "", "tool_calls": [_SKILL_CALL]},
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        return {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}


class _HangingSkill:
    recorder = None

    def __init__(self):
        self.cancelled = False

    async def execute(self, args):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _runner(skill):
    from app.services.ai.runner.agent_runner import AgentRunner

    return AgentRunner(adapter=_TwoRoundAdapter(), skill_tool=skill)


@pytest.mark.parametrize("stream", [False, True])
async def test_a_hanging_tool_times_out_and_the_turn_continues(stream, monkeypatch):
    from app.services.ai.runner import tool_timeouts as tt

    monkeypatch.setattr(tt.settings, "TOOL_TIMEOUTS", {"Skill": 0.05})
    skill = _HangingSkill()
    runner = _runner(skill)
    rec = _Rec()
    msgs = [{"role": "user", "content": "q"}]
    if stream:
        chunks = [
            c
            async for c in runner.stream_turn(
                _composed(), msgs, recorder=rec, auto_recorder=False
            )
        ]
        assert chunks and chunks[-1].finish_reason == "stop"
    else:
        out = await runner.run_turn(_composed(), msgs, recorder=rec)
        assert out["content"] == "done"
    assert runner.adapter.calls == 2, "the model is called again after the timeout"
    tool_events = [p for t, p in rec.events if t == "tool_call"]
    assert tool_events, rec.events
    result = tool_events[0]["result"]
    assert result["error"] == "timeout" and result["timed_out"] is True
    assert result["tool"] == "Skill" and result["timeout_s"] == 0.05
    assert skill.cancelled is True, "the hung handler must be cancelled"
    assert rec.turn_ends()[-1]["reason"] == "completed"  # a timeout is not a stop
