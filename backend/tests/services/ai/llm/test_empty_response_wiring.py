"""The empty-reply diagnosis reaches the run record.

`test_empty_response_diagnosis.py` proves the diagnosis is correct.
This proves the runner actually performs it — the failure mode that shipped
elsewhere in this repo repeatedly is a correct helper that no live path calls.

Anchored on the one seam where the raw envelope and the recorder are both in
hand: `AgentRunner.run_turn`'s terminal no-tool-calls branch.
"""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.runner.agent_runner import AgentRunner


def _composed():
    return ComposedSystemPrompt(
        agent_id=UUID(int=0),
        agent_slug="t",
        model="doubao-seed-2-0-lite-260428",
        temperature=0.0,
        max_tokens=64,
        system_message="s",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="",
    )


class _FakeSkillTool:
    async def execute(self, args):  # pragma: no cover - never reached here
        return {}


class _Recorder:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

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


def _events_of(rec, kind):
    return [p for t, p in rec.events if t == kind]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_an_empty_reply_records_where_the_tokens_went():
    """The exact production shape: no text, no tool call, tokens billed."""
    adapter = AsyncMock()
    adapter.call.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "z" * 300,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"completion_tokens": 649, "prompt_tokens": 1418},
    }
    rec = _Recorder()
    runner = AgentRunner(adapter=adapter, skill_tool=_FakeSkillTool())
    out = await runner.run_turn(
        _composed(), [{"role": "user", "content": "hi"}], recorder=rec
    )

    assert out["content"] == ""
    diagnoses = _events_of(rec, "error")
    assert diagnoses, f"no diagnosis recorded; events={[t for t, _ in rec.events]}"
    d = diagnoses[0]
    assert d.get("kind") == "empty_response"
    assert d["message_keys"]["reasoning_content"] == 300
    assert d["billed_but_empty"] is True
    assert d["completion_tokens"] == 649


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_normal_reply_records_no_diagnosis():
    """The negative half — a diagnosis on every turn is noise nobody reads."""
    adapter = AsyncMock()
    adapter.call.return_value = {
        "choices": [{"message": {"content": "a real answer"}, "finish_reason": "stop"}]
    }
    rec = _Recorder()
    runner = AgentRunner(adapter=adapter, skill_tool=_FakeSkillTool())
    await runner.run_turn(
        _composed(), [{"role": "user", "content": "hi"}], recorder=rec
    )
    assert not _events_of(rec, "error")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_assistant_event_is_still_recorded_for_an_empty_reply():
    """The diagnosis is additional evidence, not a replacement — dropping the
    assistant event would put a hole in the transcript."""
    adapter = AsyncMock()
    adapter.call.return_value = {
        "choices": [{"message": {"content": ""}, "finish_reason": "stop"}]
    }
    rec = _Recorder()
    runner = AgentRunner(adapter=adapter, skill_tool=_FakeSkillTool())
    await runner.run_turn(
        _composed(), [{"role": "user", "content": "hi"}], recorder=rec
    )
    assert _events_of(rec, "assistant") == [{"content": ""}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_recorder_that_throws_does_not_break_the_turn():
    """Diagnostics are never worth losing a turn over."""

    class _Angry(_Recorder):
        async def record_event(self, event_type, payload):
            if event_type == "error":
                raise RuntimeError("sink down")
            await super().record_event(event_type, payload)

    adapter = AsyncMock()
    adapter.call.return_value = {
        "choices": [{"message": {"content": ""}, "finish_reason": "stop"}]
    }
    runner = AgentRunner(adapter=adapter, skill_tool=_FakeSkillTool())
    out = await runner.run_turn(
        _composed(), [{"role": "user", "content": "hi"}], recorder=_Angry()
    )
    assert out["content"] == ""
