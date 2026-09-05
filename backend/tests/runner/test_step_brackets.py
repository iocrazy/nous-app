"""Every LLM call is bracketed by ``step_start`` / ``step_end`` on both runner
paths, with usage → cost computed at the recorder's rates. ``run.cost``
folds from nothing else, so a missing bracket is a silent wrong bill."""

from __future__ import annotations

import pytest

from app.services.ai.runner.agent_runner import AgentRunner
from app.services.ai.runner.step_hooks import StepHookChain


class _Recorder:
    def __init__(self):
        self.events = []
        self.usage = []

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload, turn, step))

    def record_usage(self, **kw):
        self.usage.append(kw)

    def cost_of(self, prompt, completion, cached=0):
        return prompt * 0.001 + completion * 0.002

    async def heartbeat(self):
        return None

    async def check_cancelled(self):
        return False


def _composed():
    from uuid import UUID

    from app.schemas.ai_library import ComposedSystemPrompt

    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="t",
        model="unit-model",
        temperature=0.0,
        max_tokens=16,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="f",
    )


class _Adapter:
    async def call(self, composed, messages):
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "hi"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }


class _SkillTool:
    recorder = None

    async def execute(self, args):
        return {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_turn_brackets_the_call_with_step_start_and_step_end():
    rec = _Recorder()
    runner = AgentRunner(
        adapter=_Adapter(), skill_tool=_SkillTool(), step_hooks=StepHookChain([])
    )
    await runner.run_turn(_composed(), [{"role": "user", "content": "q"}], recorder=rec)
    kinds = [e[0] for e in rec.events]
    assert kinds.index("step_start") < kinds.index("step_end")
    start = next(e for e in rec.events if e[0] == "step_start")
    end = next(e for e in rec.events if e[0] == "step_end")
    assert (start[2], start[3]) == (1, 1) and (end[2], end[3]) == (1, 1)
    assert start[1]["model"] == "unit-model" and start[1]["is_stream"] is False
    assert end[1]["usage"] == {"prompt": 100, "completion": 50, "cached": 0}
    assert end[1]["cost_cents"] == pytest.approx(100 * 0.001 + 50 * 0.002)
    assert end[1]["finish_reason"] == "stop"
    assert isinstance(end[1]["duration_ms"], int)


@pytest.mark.unit
def test_both_inner_paths_carry_exactly_one_bracket_pair():
    """Source guard: run and stream inner loops each call the shared
    ``_step_started`` / ``_step_ended`` once — no path may lose its bracket."""
    import inspect

    from app.services.ai.runner import agent_runner as ar

    for fn in (ar.AgentRunner._run_turn_inner, ar.AgentRunner._stream_turn_inner):
        src = inspect.getsource(fn)
        assert src.count("self._step_started(") == 1, fn.__name__
        assert src.count("self._step_ended(") == 1, fn.__name__
