"""Phase 2a Task 3: the runner dispatches AskUser on BOTH paths and parks the
turn right after the tool ran (awaiting_input), instead of letting the model
keep talking to nobody."""

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.ai.adapters.base import StreamChunk
from app.services.ai.tools.ask_user_tool import ASK_USER_TOOL_NAME

pytestmark = pytest.mark.unit

RUNNER_SRC = Path("app/services/ai/runner/agent_runner.py")
CHAT_SRC = Path("app/services/ai/chat/ai_library_chat_service.py")


def test_both_tool_ladders_dispatch_ask_user():
    src = RUNNER_SRC.read_text(encoding="utf-8")
    hits = re.findall(r"elif tool_name == ASK_USER_TOOL_NAME:", src)
    assert len(hits) == 2, "AskUser must be in the stream AND the non-stream ladder"
    assert src.count("self._dispatch_ask_user(") == 2


def test_chat_service_injects_ask_user_on_both_triggers_and_finish_issue_only_on_issue():
    src = CHAT_SRC.read_text(encoding="utf-8")
    fi = src.index("[finish_issue_spec()]")
    au = src.index("[ask_user_spec()]")
    assert fi < au, "AskUser is added after (outside) the issue-only FinishIssue block"
    # the AskUser line is not indented under the issue trigger `if`
    au_line = src[:au].rsplit("\n", 1)[-1]
    fi_line = src[:fi].rsplit("\n", 1)[-1]
    assert len(au_line) - len(au_line.lstrip()) < len(fi_line) - len(fi_line.lstrip())


class _Rec:
    def __init__(self):
        self.run_id = 7
        self.events = []
        self.next_event_seq = 3

    @property
    def views(self):
        from app.services.ai.runner import run_projection as rp

        v = rp.empty_views()
        for t, p in self.events:
            v = rp.apply(v, t, p)
        return v

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload))
        self.next_event_seq += 1

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


def _composed():
    from uuid import UUID

    from app.schemas.ai_library import ComposedSystemPrompt

    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="t",
        model="m",
        temperature=0.0,
        max_tokens=16,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="f",
    )


def _runner(adapter):
    from app.services.ai.runner.agent_runner import AgentRunner

    class _Tool:
        recorder = None

        async def execute(self, args):
            return {}

    return AgentRunner(adapter=adapter, skill_tool=_Tool())


_ASK = {
    "id": "call_1",
    "type": "function",
    "function": {
        "name": ASK_USER_TOOL_NAME,
        "arguments": '{"question": "Which ending?", "options": [{"label": "Twist"}]}',
    },
}


async def test_run_turn_parks_on_ask_user_and_returns_the_question():
    adapter = AsyncMock()
    calls = []

    async def _call(composed, messages, **kw):
        calls.append(1)
        return {
            "choices": [
                {
                    "message": {"content": "", "tool_calls": [_ASK]},
                    "finish_reason": "tool_calls",
                }
            ]
        }

    adapter.call = _call
    rec = _Rec()
    out = await _runner(adapter).run_turn(
        _composed(), [{"role": "user", "content": "q"}], recorder=rec
    )
    assert len(calls) == 1, "the model must not be called again after AskUser"
    assert out["awaiting_input"] is True and out["stop_reason"] == "awaiting_input"
    asked = [p for t, p in rec.events if t == "question_asked"][0]
    assert out["question"]["question_id"] == asked["question_id"]
    assert asked["question_id"].startswith("q:7:")
    assert out["question"]["options"] == [{"label": "Twist", "description": None}]
    assert out["tool_calls"][0]["name"] == ASK_USER_TOOL_NAME
    types = [t for t, _ in rec.events]
    assert (
        types.index("question_asked")
        < types.index("tool_call")
        < types.index("turn_end")
    )
    assert rec.turn_ends()[0]["reason"] == "awaiting_input"


async def test_stream_turn_parks_on_ask_user_with_a_typed_terminal_chunk():
    adapter = AsyncMock()

    async def _stream(composed, messages, **kw):
        yield StreamChunk(
            tool_call_delta={
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "function": {
                            "name": ASK_USER_TOOL_NAME,
                            "arguments": '{"question": "Which ending?"}',
                        },
                    }
                ]
            }
        )
        yield StreamChunk(finish_reason="tool_calls")

    adapter.stream = _stream
    rec = _Rec()
    chunks = []
    async for ch in _runner(adapter).stream_turn(
        _composed(),
        [{"role": "user", "content": "q"}],
        recorder=rec,
        auto_recorder=False,
    ):
        chunks.append(ch)
    last = chunks[-1]
    assert last.finish_reason == "stop"
    assert last.usage == {"stop_reason": "awaiting_input"}
    assert (
        last.tool_call_trace and last.tool_call_trace[0]["name"] == ASK_USER_TOOL_NAME
    )
    assert rec.turn_ends()[0]["reason"] == "awaiting_input"
