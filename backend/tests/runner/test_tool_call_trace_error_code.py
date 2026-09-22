"""``tool_call_trace`` 必须带 ``error_code``（3d batch1 Task 4 修复轮）。

transcript 的 ``tool_call`` 事件从 3c §3.2 起就带这个归一化失败码，但**聊天气泡读
的不是 transcript**——它读的是折进助手消息的这条 trace（`ChatToolCall`）。两条路
少了一条，代价是真实的：一个排在 AskUser 后面、**根本没执行**的 ``CreateShot``，在
气泡上渲染成成功，还被 ``summarizeWrites`` 计进「wrote N cards」，用户去画布上找
一张不存在的卡。

所以两处必须同源：`tool_error_code(result)` 每次派发只算一次，trace 与 transcript
共用那一个值。下面对 ``run_turn`` / ``stream_turn`` 两条路径各钉一遍——2026-09-08
的教训是生产走的那条路径的单测才算数。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.adapters.base import StreamChunk
from app.services.ai.runner.agent_runner import AgentRunner

pytestmark = pytest.mark.unit


def _composed():
    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="t",
        model="unit-model",
        temperature=0.0,
        max_tokens=64,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="fp",
    )


class _Recorder:
    def __init__(self):
        self.events = []

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload))

    def record_usage(self, **kw):
        return None

    def record_skill(self, slug):
        return None

    def cost_of(self, prompt, completion, cached=0):
        return 0.0

    async def heartbeat(self):
        return None

    async def check_cancelled(self):
        return False


class _SkillTool:
    """``result`` 由用例给定 —— 这里要覆盖的正是那些**不带 ``ok`` 键**的真实形状。"""

    def __init__(self, result):
        self.result = result

    async def execute(self, args):
        return self.result


def _transcript_codes(rec):
    return [p["error_code"] for t, p in rec.events if t == "tool_call"]


def _buffered_adapter():
    adapter = AsyncMock()
    adapter.call.side_effect = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "tc1",
                                "function": {
                                    "name": "Skill",
                                    "arguments": json.dumps({"skill": "s"}),
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"content": "FINAL"}}]},
    ]
    return adapter


class _StreamingAdapter:
    """生产的聊天路径。注意它**有** ``stream``，走的是真流式分支。"""

    def __init__(self):
        self.iter = 0

    async def call(self, composed, messages):
        return {"choices": [{"message": {"content": "fallback"}}]}

    async def stream(self, composed, messages):
        self.iter += 1
        if self.iter == 1:
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {"name": "Skill", "arguments": ""},
                        }
                    ]
                }
            )
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {"index": 0, "function": {"arguments": '{"skill":"s"}'}}
                    ]
                }
            )
            yield StreamChunk(finish_reason="tool_calls")
        else:
            yield StreamChunk(delta_text="FINAL", finish_reason="stop")


# 真实形状，不是理想化的：前者是 finish_issue / 被腰斩调用那一族（有 ``error``
# 或非 ok 的 ``outcome``，但**没有** ``ok`` 键），后者是干净成功。
FAILED_RESULT = {"error": "not executed: the turn parked on AskUser before this call"}
OK_RESULT = {"ok": True, "text": "done"}


@pytest.mark.parametrize(
    "result,expected",
    [(FAILED_RESULT, "tool_error"), (OK_RESULT, None)],
)
async def test_run_turn_trace_carries_error_code(result, expected):
    rec = _Recorder()
    runner = AgentRunner(adapter=_buffered_adapter(), skill_tool=_SkillTool(result))

    out = await runner.run_turn(
        _composed(), [{"role": "user", "content": "go"}], recorder=rec
    )

    assert out["content"] == "FINAL"
    (call,) = out["tool_calls"]
    assert call["error_code"] == expected
    # 同源：trace 与 transcript 报的是同一个值，不是各算各的。
    assert _transcript_codes(rec) == [expected]


@pytest.mark.parametrize(
    "result,expected",
    [(FAILED_RESULT, "tool_error"), (OK_RESULT, None)],
)
async def test_stream_turn_trace_carries_error_code(result, expected):
    rec = _Recorder()
    runner = AgentRunner(adapter=_StreamingAdapter(), skill_tool=_SkillTool(result))

    traces = []
    async for chunk in runner.stream_turn(
        _composed(), [{"role": "user", "content": "go"}], recorder=rec
    ):
        if chunk.tool_call_trace is not None:
            traces = chunk.tool_call_trace

    (call,) = traces
    assert call["error_code"] == expected
    assert _transcript_codes(rec) == [expected]
